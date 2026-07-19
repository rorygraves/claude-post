"""Email client implementation for IMAP and SMTP operations.

This module contains the EmailClient class which handles all email-related
operations including reading, searching, and sending emails.
"""

import asyncio
import base64
import email
import imaplib
import logging
import os
import re
import smtplib
import ssl
from collections import Counter
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.header import decode_header, make_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, ParamSpec, Tuple, TypeVar, Union
from urllib.parse import quote

# Constants from environment configuration
from .config import EmailConfig, load_email_config

# Operation Configuration Constants
MAX_EMAILS = 500  # Hard upper bound for one search page
MAX_ATTACHMENT_SIZE = 25 * 1024 * 1024
GMAIL_WEB_BASE_URL = "https://mail.google.com/mail/u/0/"
GMAIL_METADATA_FETCH = "(X-GM-MSGID X-GM-THRID BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])"

# Grouping dimensions supported by aggregate_emails / mail-aggregate.
AGGREGATE_GROUPINGS = frozenset({"sender", "recipient", "date"})

P = ParamSpec("P")
R = TypeVar("R")


async def _run_blocking(function: Callable[P, R], *args: P.args, **kwargs: P.kwargs) -> R:
    """Run bounded blocking I/O without racing cleanup when cancelled."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        with suppress(Exception):
            await task
        raise


# Server capabilities we're interested in logging
INTERESTING_CAPABILITIES = [
    "IDLE",
    "MOVE",
    "QUOTA",
    "NAMESPACE",
    "UNSELECT",
    "UIDPLUS",
    "CONDSTORE",
    "QRESYNC",
    "SORT",
    "THREAD",
    "COMPRESS",
    "ENABLE",
    "LIST-EXTENDED",
    "SPECIAL-USE",
]


# Custom Exceptions for Email Operations
class EmailConnectionError(Exception):
    """Raised when IMAP/SMTP connection or authentication fails.

    This includes network connectivity issues, invalid credentials,
    server unavailability, or SSL/TLS handshake failures.
    """

    pass


class EmailSearchError(Exception):
    """Raised when email search or retrieval operations fail.

    This includes IMAP search syntax errors, folder selection failures,
    message fetching errors, or email parsing issues.
    """

    pass


class EmailSendError(Exception):
    """Raised when email sending operations fail.

    This includes SMTP connection issues, recipient validation errors,
    message formatting problems, or delivery failures.
    """

    pass


class EmailDeletionError(Exception):
    """Raised when email deletion operations fail.

    This includes IMAP folder selection failures, message not found errors,
    or deletion permission issues.
    """

    pass


class EmailAttachmentError(Exception):
    """Raised when email attachment operations fail.

    This includes attachment not found, file write failures,
    path validation errors, or permission issues.
    """

    pass


# Security: IMAP String Escaping
def escape_imap_string(value: str) -> str:
    """Escape special characters for IMAP search strings.

    IMAP search strings use quotes to delimit values. If user input contains
    quotes or backslashes, they must be escaped to prevent IMAP injection
    attacks or syntax errors.

    Args:
        value: The string value to escape

    Returns:
        Escaped string safe for use in IMAP search commands

    Examples:
        >>> escape_imap_string('test')
        'test'
        >>> escape_imap_string('test "quoted"')
        'test \\"quoted\\"'
        >>> escape_imap_string('path\\\\name')
        'path\\\\\\\\name'
    """
    # CR, LF, and NUL cannot be represented safely inside an IMAP command and
    # could otherwise terminate or corrupt the command being sent.
    if any(character in value for character in ("\r", "\n", "\x00")):
        raise ValueError("IMAP values cannot contain CR, LF, or NUL characters")

    # Escape backslashes first (they're the escape character)
    escaped = value.replace("\\", "\\\\")
    # Then escape quotes
    escaped = escaped.replace('"', '\\"')
    return escaped


def quote_imap_mailbox(value: str) -> str:
    """Return a safely quoted IMAP mailbox argument."""
    unquoted = value.strip('"')
    return '"' + escape_imap_string(unquoted) + '"'


def decode_imap_utf7(text: str) -> str:
    """Decode an IMAP modified-UTF-7 mailbox name to Unicode (RFC 3501 5.1.3).

    Gmail and other servers encode non-ASCII folder/label names in modified UTF-7:
    printable ASCII passes through, ``&`` shifts into a base64 (with ``,`` for ``/``)
    UTF-16-BE run terminated by ``-``, and ``&-`` is a literal ``&``. Names already
    plain ASCII are returned unchanged. Malformed sequences are left verbatim rather
    than raising, since a display name is best-effort.
    """
    if "&" not in text:
        return text
    result: list[str] = []
    i = 0
    length = len(text)
    while i < length:
        char = text[i]
        if char != "&":
            result.append(char)
            i += 1
            continue
        end = text.find("-", i + 1)
        if end == -1:
            # Unterminated shift; treat the remainder as literal.
            result.append(text[i:])
            break
        chunk = text[i + 1 : end]
        if chunk == "":
            result.append("&")  # "&-" encodes a literal ampersand
        else:
            b64 = chunk.replace(",", "/")
            padding = "=" * (-len(b64) % 4)
            try:
                result.append(base64.b64decode(b64 + padding).decode("utf-16-be"))
            except (ValueError, UnicodeDecodeError):
                result.append(text[i : end + 1])  # leave malformed run as-is
        i = end + 1
    return "".join(result)


def _decode_list_bytes(raw: bytes) -> str:
    """Decode LIST-response bytes, tolerating non-UTF-8 octets."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _tokenize_imap_astrings(text: str) -> List[str]:
    """Tokenize the delimiter/mailbox portion of a LIST line into unescaped strings.

    Each token is a quoted string (honouring ``\\"`` and ``\\\\`` escapes), the atom
    ``NIL`` (returned as an empty string), or a bare atom read up to whitespace.
    """
    tokens: List[str] = []
    i = 0
    length = len(text)
    while i < length:
        char = text[i]
        if char.isspace():
            i += 1
            continue
        if char == '"':
            i += 1
            buf: list[str] = []
            while i < length:
                current = text[i]
                if current == "\\" and i + 1 < length:
                    buf.append(text[i + 1])
                    i += 2
                    continue
                if current == '"':
                    i += 1
                    break
                buf.append(current)
                i += 1
            tokens.append("".join(buf))
        else:
            start = i
            while i < length and not text[i].isspace():
                i += 1
            atom = text[start:i]
            tokens.append("" if atom == "NIL" else atom)
    return tokens


def parse_list_response_line(entry: object) -> Optional[Dict[str, str]]:
    """Parse one imaplib LIST entry into ``{name, display_name, attributes}``.

    Handles every mailbox-name encoding servers actually emit, which the previous
    ``split('"')`` approach mangled or dropped:

    - **quoted strings** with ``\\"``/``\\\\`` escapes (naive splitting truncated
      names containing an escaped quote),
    - **literals** (``{n}`` — imaplib yields a ``(prefix, name)`` tuple, previously
      skipped entirely),
    - **bare atoms** / ``NIL`` delimiters (no quotes, previously dropped).

    ``name`` is the IMAP wire form (unescaped but still modified-UTF-7) so it round-trips
    through :func:`quote_imap_mailbox` for use in commands; ``display_name`` is the
    modified-UTF-7-decoded, ``[Gmail]/``-stripped human label. Returns ``None`` for
    entries that are not parseable folder lines.
    """
    literal_name: Optional[str] = None
    if isinstance(entry, tuple):
        if len(entry) < 2 or not isinstance(entry[0], (bytes, bytearray)):
            return None
        line = _decode_list_bytes(bytes(entry[0]))
        second = entry[1]
        literal_name = _decode_list_bytes(bytes(second)) if isinstance(second, (bytes, bytearray)) else str(second)
    elif isinstance(entry, (bytes, bytearray)):
        line = _decode_list_bytes(bytes(entry))
    else:
        return None

    attr_match = re.match(r"\s*\(([^)]*)\)\s*", line)
    if not attr_match:
        return None
    attributes = attr_match.group(1).strip()
    remainder = line[attr_match.end() :]

    if literal_name is not None:
        name = literal_name
    else:
        tokens = _tokenize_imap_astrings(remainder)
        # Expected shape after the attribute list is: <delimiter> <mailbox>.
        if len(tokens) < 2:
            return None
        name = tokens[1]

    name = name.strip()
    if not name:
        return None

    display_name = decode_imap_utf7(name)
    if display_name.startswith("[Gmail]/"):
        display_name = display_name[len("[Gmail]/") :]

    return {"name": name, "display_name": display_name, "attributes": attributes}


def normalize_email_date(date_str: str) -> str:
    """Normalize email date header to ISO 8601 format.

    Converts RFC 2822 format dates (e.g., "Mon, 15 Jan 2024 10:30:00 -0500")
    to ISO 8601 format (e.g., "2024-01-15T10:30:00-05:00").

    Args:
        date_str: Date string from email header

    Returns:
        ISO 8601 formatted date string, or original string if parsing fails

    Examples:
        >>> normalize_email_date("Mon, 15 Jan 2024 10:30:00 -0500")
        "2024-01-15T10:30:00-05:00"
        >>> normalize_email_date("Unknown")
        "Unknown"
    """
    if not date_str or date_str == "Unknown":
        return date_str
    try:
        dt = parsedate_to_datetime(date_str)
        return dt.isoformat()
    except (ValueError, TypeError):
        return date_str


def decode_email_header(value: str | None, default: str) -> str:
    """Decode RFC 2047 encoded email header text with a safe fallback."""
    if not value:
        return default
    try:
        return str(make_header(decode_header(value)))
    except (LookupError, UnicodeDecodeError):
        return value


# Security: Attachment File Handling
def _sanitize_filename(filename: str) -> str:
    """Sanitize filename to remove dangerous characters.

    Removes path separators, null bytes, and other potentially dangerous
    characters. Preserves file extension where possible.

    Args:
        filename: Original filename from email attachment

    Returns:
        Sanitized filename safe for filesystem use

    Examples:
        >>> _sanitize_filename('report.pdf')
        'report.pdf'
        >>> _sanitize_filename('../../../etc/passwd')
        '______etc_passwd'
        >>> _sanitize_filename('file\\x00name.txt')
        'file_name.txt'
    """
    # Remove null bytes
    filename = filename.replace("\x00", "")

    # Get basename only (removes directory components)
    filename = os.path.basename(filename)

    # Remove/replace dangerous characters, keep alphanumeric, dots, underscores, hyphens, spaces
    sanitized = re.sub(r"[^\w\.\-\s]", "_", filename)

    # Collapse multiple underscores
    sanitized = re.sub(r"_+", "_", sanitized)

    # Remove leading dots (hidden files) and leading/trailing whitespace
    sanitized = sanitized.lstrip(".").strip()

    # Truncate to reasonable length, preserving extension
    max_length = 200
    if len(sanitized) > max_length:
        name, ext = os.path.splitext(sanitized)
        name = name[: max_length - len(ext)]
        sanitized = name + ext

    # Fallback if empty
    return sanitized if sanitized else "unnamed"


def _validate_output_directory(output_dir: str) -> None:
    """Validate that output directory is safe and writable.

    Args:
        output_dir: Directory path to validate

    Raises:
        ValueError: If directory is invalid, doesn't exist, or isn't writable
    """
    # Must be absolute path
    if not os.path.isabs(output_dir):
        raise ValueError(
            f"output_dir must be an absolute path, got: {output_dir}\n"
            f"Hint: Use full path like '/Users/name/Downloads' or 'C:\\Users\\name\\Downloads'"
        )

    # Must exist
    if not os.path.exists(output_dir):
        raise ValueError(
            f"Output directory does not exist: {output_dir}\n"
            f"Hint: Create the directory first or specify an existing directory"
        )

    # Must be a directory
    if not os.path.isdir(output_dir):
        raise ValueError(
            f"Output path is not a directory: {output_dir}\nHint: Specify a directory path, not a file path"
        )

    # Must be writable
    if not os.access(output_dir, os.W_OK):
        raise ValueError(
            f"Output directory is not writable: {output_dir}\nHint: Check permissions or choose a different directory"
        )


def _get_unique_filepath(output_dir: str, filename: str) -> Tuple[str, str]:
    """Generate a unique filepath, handling collisions.

    Args:
        output_dir: Directory to save file to
        filename: Sanitized filename

    Returns:
        Tuple of (full_path, actual_filename) where actual_filename may differ
        from input if collision was detected

    Raises:
        ValueError: If too many collisions (>1000 files with same name) or path traversal detected

    Examples:
        >>> _get_unique_filepath('/tmp', 'report.pdf')
        ('/tmp/report.pdf', 'report.pdf')  # If doesn't exist
        >>> _get_unique_filepath('/tmp', 'report.pdf')
        ('/tmp/report_1.pdf', 'report_1.pdf')  # If report.pdf exists
    """
    base_path = os.path.join(output_dir, filename)

    # Verify final path is within output_dir (prevent traversal)
    real_path = os.path.realpath(base_path)
    real_output_dir = os.path.realpath(output_dir)
    if not real_path.startswith(real_output_dir + os.sep) and real_path != os.path.join(real_output_dir, filename):
        raise ValueError(f"Invalid filename: path traversal detected in '{filename}'")

    if not os.path.exists(real_path):
        return real_path, filename

    # Handle collision
    name, ext = os.path.splitext(filename)
    counter = 1
    max_attempts = 1000

    while counter <= max_attempts:
        new_filename = f"{name}_{counter}{ext}"
        new_path = os.path.join(output_dir, new_filename)
        real_new_path = os.path.realpath(new_path)

        if not os.path.exists(real_new_path):
            return real_new_path, new_filename
        counter += 1

    raise ValueError(
        f"Too many files with name '{name}' in output directory (>{max_attempts})\n"
        f"Hint: Clean up the output directory or use a different location"
    )


def _format_email_as_markdown(email_content: Dict[str, Any]) -> str:
    """Format email content as markdown with YAML frontmatter.

    Converts email data structure to a markdown file format with
    YAML frontmatter containing metadata and body content below.

    Args:
        email_content: Dictionary containing email fields:
            - from: Sender address
            - to: Recipient address
            - date: ISO format date string
            - subject: Email subject line
            - content: Body text
            - attachments: List of attachment metadata (optional)

    Returns:
        Formatted markdown string with YAML frontmatter

    Examples:
        >>> content = {'from': 'a@b.com', 'to': 'c@d.com', 'date': '2024-01-15T10:30:00',
        ...            'subject': 'Test', 'content': 'Hello', 'attachments': []}
        >>> md = _format_email_as_markdown(content)
        >>> '---' in md
        True
    """
    # Build YAML frontmatter
    lines = ["---"]

    # Escape YAML values (handle quotes and special characters)
    def yaml_escape(value: str) -> str:
        # If contains special chars, wrap in quotes and escape internal quotes
        if any(c in value for c in [":", '"', "'", "\n", "\r", "#", "{", "}", "[", "]"]):
            return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
        return value

    lines.append(f"from: {yaml_escape(email_content.get('from', 'Unknown'))}")
    lines.append(f"to: {yaml_escape(email_content.get('to', 'Unknown'))}")
    lines.append(f"date: {yaml_escape(email_content.get('date', 'Unknown'))}")
    lines.append(f"subject: {yaml_escape(email_content.get('subject', 'No Subject'))}")
    lines.append("---")
    lines.append("")

    # Add body content
    body = email_content.get("content", "")
    # Clean up HTML-ish content if present
    body = body.replace("<br />", "\n").replace("<br/>", "\n").replace("<br>", "\n")
    body = re.sub(r"<[^>]+>", "", body)  # Remove HTML tags
    body = body.replace("\r\n", "\n").replace("\r", "\n")
    lines.append(body.strip())

    # Add attachments section if present
    attachments = email_content.get("attachments", [])
    if attachments:
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("## Attachments")
        lines.append("")
        for att in attachments:
            filename = att.get("filename", "unknown")
            size = att.get("size", 0)
            lines.append(f"- {filename} ({size} bytes)")

    return "\n".join(lines)


def _generate_email_filename(email_content: Dict[str, Any]) -> str:
    """Generate a filename for an email based on date and subject.

    Creates filename in format: YYYYMMDD-HHMM-sanitized_subject.md

    Args:
        email_content: Dictionary containing email fields (date, subject)

    Returns:
        Sanitized filename string with .md extension

    Examples:
        >>> content = {'date': '2024-10-15T14:30:00', 'subject': 'Meeting Notes'}
        >>> filename = _generate_email_filename(content)
        >>> filename.endswith('.md')
        True
    """
    # Parse date
    date_str = email_content.get("date", "")
    try:
        # Handle ISO format with timezone
        if "+" in date_str or date_str.endswith("Z"):
            date_str_clean = date_str.replace("Z", "+00:00")
            # Handle various timezone formats
            dt = datetime.fromisoformat(date_str_clean)
        else:
            dt = datetime.fromisoformat(date_str[:19]) if date_str else datetime.now()
    except (ValueError, TypeError):
        dt = datetime.now()

    # Create date prefix
    date_prefix = dt.strftime("%Y%m%d-%H%M")

    # Sanitize subject for filename
    subject = email_content.get("subject", "No Subject")
    safe_subject = _sanitize_filename(subject)

    # Truncate subject if too long (leave room for date prefix and extension)
    max_subject_len = 80
    if len(safe_subject) > max_subject_len:
        safe_subject = safe_subject[:max_subject_len]

    return f"{date_prefix}-{safe_subject}.md"


# Data Classes for Input Validation and Type Safety
@dataclass
class SearchCriteria:
    """Encapsulates and validates email search parameters.

    Provides type-safe search criteria with automatic validation of date formats
    and folder names. Used to ensure consistent search parameters across the
    email search functionality.

    Attributes:
        folder: Email folder to search ('inbox' or 'sent')
        start_date: Search start date in YYYY-MM-DD format (optional)
        end_date: Search end date in YYYY-MM-DD format (optional)
        subject: Text to search for in email subject line (optional)
        sender: Text to search for in sender email address or name (optional)
        to: Text to search for in recipient email address or name (optional)
        body: Text to search for in email body content (optional)
        max_results: Maximum number of emails to return (default: 100)
        start_from: Starting position for pagination (default: 0)
        direction: Sort direction for emails ('newest' or 'oldest', default: 'newest')
    """

    folder: str = "inbox"
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    subject: Optional[str] = None
    sender: Optional[str] = None
    to: Optional[str] = None
    body: Optional[str] = None
    max_results: int = 100
    start_from: int = 0
    direction: Literal["newest", "oldest"] = "newest"

    def __post_init__(self) -> None:
        """Automatically validate criteria after object creation."""
        self.validate()

    def validate(self) -> None:
        """Validate date formats and pagination parameters.

        Raises:
            ValueError: If date strings don't match YYYY-MM-DD format or pagination params are invalid
        """
        # Validate start_date format if provided
        if self.start_date:
            try:
                datetime.strptime(self.start_date, "%Y-%m-%d")
            except ValueError as e:
                raise ValueError(f"Invalid start_date format: {self.start_date}. Expected YYYY-MM-DD") from e

        # Validate end_date format if provided
        if self.end_date:
            try:
                datetime.strptime(self.end_date, "%Y-%m-%d")
            except ValueError as e:
                raise ValueError(f"Invalid end_date format: {self.end_date}. Expected YYYY-MM-DD") from e

        # Validate pagination parameters
        if self.max_results <= 0:
            raise ValueError(f"max_results must be positive, got: {self.max_results}")
        if self.start_from < 0:
            raise ValueError(f"start_from must be non-negative, got: {self.start_from}")
        if self.max_results > MAX_EMAILS:
            raise ValueError(f"max_results cannot exceed {MAX_EMAILS}, got: {self.max_results}")
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must be on or before end_date")
        if not self.folder.strip():
            raise ValueError("folder cannot be empty")


@dataclass
class PaginationInfo:
    """Information about pagination for search results.

    Attributes:
        total_available: Total number of emails matching the search criteria
        returned: Number of emails returned in this response
        start_from: Starting offset used for this search
        has_more: Whether there are more results beyond this page
        next_start_from: Offset to use for fetching the next page (None if no more results)
    """

    total_available: int
    returned: int
    start_from: int
    has_more: bool
    next_start_from: Optional[int]

    def to_dict(self) -> Dict[str, Any]:
        """Convert pagination info to a dictionary for JSON serialization."""
        return {
            "total_available": self.total_available,
            "returned": self.returned,
            "start_from": self.start_from,
            "has_more": self.has_more,
            "next_start_from": self.next_start_from,
        }


@dataclass(frozen=True)
class MailboxOperationResult:
    """Outcome of a move/delete operation, reported from the server's actual result.

    IMAP UIDs are folder-scoped, so a requested UID may not exist in the folder being
    acted upon. This separates the UIDs actually affected from those not found, so a
    caller never mistakes a silent no-op for success.
    """

    requested: List[str]
    affected: List[str]
    not_found: List[str]

    @classmethod
    def from_request(cls, requested: List[str], affected: List[str]) -> "MailboxOperationResult":
        affected_set = set(affected)
        not_found = [uid for uid in requested if uid not in affected_set]
        return cls(requested=list(requested), affected=list(affected), not_found=not_found)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "requested_count": len(self.requested),
            "affected_count": len(self.affected),
            "affected": self.affected,
            "not_found": self.not_found,
        }


@dataclass
class EmailMessage:
    """Encapsulates and validates email message data before sending.

    Provides type-safe email composition with automatic validation of required
    fields. Ensures all necessary components are present before attempting to
    send the email via SMTP.

    Attributes:
        to_addresses: List of recipient email addresses (required)
        subject: Email subject line (required, cannot be empty)
        content: Email body content (required, cannot be empty)
        cc_addresses: List of CC recipient addresses (optional)
    """

    to_addresses: List[str]
    subject: str
    content: str
    cc_addresses: Optional[List[str]] = None

    def __post_init__(self) -> None:
        """Automatically validate message data after object creation."""
        self.validate()

    def validate(self) -> None:
        """Validate email message components for completeness.

        Ensures all required fields are present and non-empty.

        Raises:
            ValueError: If required fields are missing or empty
        """
        # Ensure at least one recipient is specified
        if not self.to_addresses:
            raise ValueError("At least one recipient email address is required")

        # Ensure subject is not empty or just whitespace
        if not self.subject.strip():
            raise ValueError("Email subject cannot be empty")

        # Ensure content is not empty or just whitespace
        if not self.content.strip():
            raise ValueError("Email content cannot be empty")


class EmailClient:
    """Centralized email operations manager for IMAP and SMTP functionality.

    This class encapsulates all email-related operations including:
    - IMAP connection management for reading emails
    - SMTP connection management for sending emails
    - Email searching with various criteria
    - Email content retrieval and parsing
    - Daily email counting and statistics

    The client handles connection lifecycle, error handling, and provides
    a consistent interface for all email operations used by the MCP server.

    Attributes:
        email_address: Email account address from environment config
        email_password: Email account password/app-password from environment
        imap_server: IMAP server hostname for reading emails
        smtp_server: SMTP server hostname for sending emails
        smtp_port: SMTP server port number
    """

    def __init__(self, config: EmailConfig | None = None) -> None:
        """Initialize EmailClient with configuration from environment variables.

        Loads email server settings from the global configuration constants
        that were extracted from environment variables at startup.
        """
        self._config = config

    @property
    def config(self) -> EmailConfig:
        """Load configuration lazily so help and tool discovery need no credentials."""
        if self._config is None:
            self._config = load_email_config()
        return self._config

    @property
    def email_address(self) -> str:
        return self.config.email_address

    @property
    def email_password(self) -> str:
        return self.config.email_password

    @property
    def imap_server(self) -> str:
        return self.config.imap_server

    @property
    def smtp_server(self) -> str:
        return self.config.smtp_server

    @property
    def smtp_port(self) -> int:
        return self.config.smtp_port

    @staticmethod
    def _validate_email_ids(email_ids: List[str], *, maximum: int = 500) -> None:
        if not email_ids:
            raise ValueError("At least one email UID is required")
        if len(email_ids) > maximum:
            raise ValueError(f"At most {maximum} email UIDs may be processed at once")
        if any(not isinstance(email_id, str) or not email_id.isdigit() or int(email_id) <= 0 for email_id in email_ids):
            raise ValueError("Email IDs must be positive numeric IMAP UIDs")

    @staticmethod
    def _validate_gmail_msgids(gmail_msgids: List[str], *, maximum: int = 500) -> None:
        if not gmail_msgids:
            raise ValueError("At least one gmail_msgid is required")
        if len(gmail_msgids) > maximum:
            raise ValueError(f"At most {maximum} gmail_msgids may be processed at once")
        # X-GM-MSGID is an unsigned 64-bit integer; enforce digits-only to prevent
        # search-command injection and reject junk early.
        if any(not isinstance(msgid, str) or not msgid.isdigit() or int(msgid) <= 0 for msgid in gmail_msgids):
            raise ValueError("gmail_msgids must be positive numeric X-GM-MSGID values")

    async def connect_imap(self) -> imaplib.IMAP4_SSL:
        """Establish an authenticated SSL IMAP connection.

        Creates a secure connection to the IMAP server and authenticates
        using the configured email credentials. The connection is ready
        for folder selection and email operations.

        Returns:
            Authenticated IMAP4_SSL connection object ready for use

        Raises:
            EmailConnectionError: If connection fails, authentication fails,
                                or SSL handshake encounters issues
        """

        connected_mail: list[imaplib.IMAP4_SSL] = []

        def connect() -> imaplib.IMAP4_SSL:
            context = ssl.create_default_context()
            mail = imaplib.IMAP4_SSL(
                self.imap_server,
                self.config.imap_port,
                ssl_context=context,
                timeout=self.config.connection_timeout,
            )
            try:
                mail.login(self.email_address, self.email_password)
            except Exception:
                with suppress(Exception):
                    mail.logout()
                raise
            connected_mail.append(mail)
            return mail

        try:
            logging.info("Connecting to IMAP server: %s", self.imap_server)
            mail = await _run_blocking(connect)
            logging.info("IMAP login successful")
        except asyncio.CancelledError:
            if connected_mail:
                await _run_blocking(connected_mail[0].logout)
            raise
        except Exception as e:
            logging.exception("IMAP connection/login failed")
            raise EmailConnectionError(f"Failed to connect to IMAP server: {e!s}") from e
        else:
            return mail

    async def close_imap_connection(self, mail: imaplib.IMAP4_SSL) -> None:
        """Safely close IMAP connection."""

        def close() -> None:
            # IMAP CLOSE expunges every message marked Deleted. UNSELECT does not.
            if getattr(mail, "state", None) == "SELECTED" and hasattr(mail, "unselect"):
                mail.unselect()
            mail.logout()

        try:
            await _run_blocking(close)
            logging.info("IMAP connection closed")
        except Exception as e:
            logging.warning(f"Error closing IMAP connection: {e!s}")

    async def _get_capability_set(self, mail: imaplib.IMAP4_SSL) -> set[str]:
        """Refresh and normalize capabilities on the authenticated connection.

        ``imaplib.IMAP4.capabilities`` is populated during connection setup and
        may therefore contain only the server's pre-authentication features.
        Extensions such as MOVE and UIDPLUS must be detected from a fresh
        CAPABILITY response after LOGIN.
        """
        status, data = await _run_blocking(mail.capability)
        if status != "OK" or not data or not data[0]:
            raise EmailConnectionError("Failed to refresh IMAP capabilities after authentication")
        first_response = data[0]
        capabilities = first_response.split() if isinstance(first_response, bytes) else str(first_response).split()
        return {
            item.decode("ascii", errors="ignore").upper() if isinstance(item, bytes) else str(item).upper()
            for item in capabilities
        }

    async def _supports_gmail_extensions(self, mail: imaplib.IMAP4_SSL) -> bool:
        """Return whether optional Gmail IMAP metadata is available."""
        try:
            return "X-GM-EXT-1" in await self._get_capability_set(mail)
        except Exception as exc:
            logging.warning("Could not detect optional Gmail extensions: %s", type(exc).__name__)
            return False

    async def _supports_sort(self, mail: imaplib.IMAP4_SSL) -> bool:
        """Return whether the server supports the IMAP SORT extension (RFC 5256)."""
        try:
            return "SORT" in await self._get_capability_set(mail)
        except Exception as exc:
            logging.warning("Could not detect IMAP SORT support: %s", type(exc).__name__)
            return False

    @staticmethod
    def _extract_fetch_number(descriptor: object, field: str) -> str | None:
        """Extract a decimal IMAP FETCH attribute without converting its precision."""
        if not isinstance(descriptor, bytes):
            return None
        match = re.search(rb"\b" + re.escape(field.encode("ascii")) + rb" (\d+)\b", descriptor, re.IGNORECASE)
        return match.group(1).decode("ascii") if match else None

    @staticmethod
    def _normalize_message_id(message_id: object) -> str | None:
        """Normalize an RFC-822 Message-ID while preserving its angle brackets."""
        if not isinstance(message_id, str):
            return None
        normalized = message_id.strip()
        return normalized or None

    def _build_gmail_url(self, gmail_msgid: str | None, message_id: str | None) -> str | None:
        """Build an account-pinned Gmail message URL, falling back to RFC-822 search."""
        fragment: str | None = None
        if gmail_msgid and gmail_msgid.isdigit():
            numeric_msgid = int(gmail_msgid)
            if 0 <= numeric_msgid <= (1 << 64) - 1:
                fragment = f"all/{numeric_msgid:x}"
        if fragment is None and message_id:
            search_message_id = message_id.strip().strip("<>")
            if search_message_id:
                fragment = f"search/rfc822msgid:{quote(search_message_id, safe='')}"
        if fragment is None:
            return None
        authuser = quote(self.email_address, safe="@.")
        return f"{GMAIL_WEB_BASE_URL}?authuser={authuser}#{fragment}"

    def _build_backlink_fields(
        self,
        *,
        message_id: object = None,
        gmail_msgid: object = None,
        gmail_thrid: object = None,
    ) -> Dict[str, str | None]:
        """Return JSON-safe backlink fields with Gmail identifiers kept as strings."""
        normalized_message_id = self._normalize_message_id(message_id)
        normalized_gmail_msgid = str(gmail_msgid) if isinstance(gmail_msgid, str) and gmail_msgid.isdigit() else None
        normalized_gmail_thrid = str(gmail_thrid) if isinstance(gmail_thrid, str) and gmail_thrid.isdigit() else None
        return {
            "message_id": normalized_message_id,
            "gmail_msgid": normalized_gmail_msgid,
            "gmail_thrid": normalized_gmail_thrid,
            "gmail_url": self._build_gmail_url(normalized_gmail_msgid, normalized_message_id),
        }

    def _merge_backlink_fields(
        self,
        content: Dict[str, Any],
        metadata: Dict[str, str | None] | None,
    ) -> Dict[str, Any]:
        """Merge optional Gmail metadata with Message-ID parsed from full content."""
        metadata = metadata or {}
        content.update(
            self._build_backlink_fields(
                message_id=metadata.get("message_id") or content.get("message_id"),
                gmail_msgid=metadata.get("gmail_msgid"),
                gmail_thrid=metadata.get("gmail_thrid"),
            )
        )
        return content

    async def _fetch_backlink_metadata(
        self,
        mail: imaplib.IMAP4_SSL,
        email_ids: List[str],
    ) -> Dict[str, Dict[str, str | None]]:
        """Fetch stable Gmail identifiers and Message-ID headers without setting Seen."""
        if not email_ids or not await self._supports_gmail_extensions(mail):
            return {}

        try:
            responses = await self._uid_command(mail, "FETCH", ",".join(email_ids), GMAIL_METADATA_FETCH)
        except EmailSearchError as exc:
            logging.warning("Optional Gmail metadata fetch failed: %s", type(exc).__name__)
            return {}

        metadata_by_uid: Dict[str, Dict[str, str | None]] = {}
        for response in responses:
            if not isinstance(response, tuple) or len(response) < 2:
                continue
            descriptor, header_bytes = response[0], response[1]
            uid = self._extract_fetch_number(descriptor, "UID")
            if uid is None or not isinstance(header_bytes, bytes):
                continue
            header_message = email.message_from_bytes(header_bytes)
            metadata_by_uid[uid] = self._build_backlink_fields(
                message_id=header_message.get("Message-ID"),
                gmail_msgid=self._extract_fetch_number(descriptor, "X-GM-MSGID"),
                gmail_thrid=self._extract_fetch_number(descriptor, "X-GM-THRID"),
            )
        return metadata_by_uid

    async def _uid_command(
        self,
        mail: imaplib.IMAP4_SSL,
        command: str,
        *arguments: str | None,
    ) -> list[Any]:
        """Execute a UID command and require an OK response."""
        uid_method: Any = mail.uid
        status, data = await _run_blocking(lambda: uid_method(command, *arguments))
        if status != "OK":
            raise EmailSearchError(f"UID {command} failed")
        if not isinstance(data, list):
            raise EmailSearchError(f"UID {command} returned an invalid response")
        return data

    async def _filter_existing_uids(self, mail: imaplib.IMAP4_SSL, email_ids: list[str]) -> list[str]:
        """Return the subset of email_ids that actually exist in the selected folder.

        IMAP UIDs are folder-scoped, so a UID valid in one folder may match nothing
        in another. Resolving the request against a UID SEARCH lets callers act only
        on real messages and report the rest as not-found, instead of silently
        no-opping while reporting success.
        """
        message_set = ",".join(email_ids)
        data = await self._uid_command(mail, "SEARCH", None, f"UID {message_set}")
        found: set[str] = set()
        for part in data:
            if not part:
                continue
            text = part.decode() if isinstance(part, (bytes, bytearray)) else str(part)
            found.update(text.split())
        # Preserve the caller's order and drop duplicates.
        seen: set[str] = set()
        existing: list[str] = []
        for uid in email_ids:
            if uid in found and uid not in seen:
                seen.add(uid)
                existing.append(uid)
        return existing

    async def _resolve_gmail_msgids(
        self, mail: imaplib.IMAP4_SSL, gmail_msgids: list[str]
    ) -> tuple[dict[str, str], list[str]]:
        """Resolve stable X-GM-MSGID values to current UIDs in the selected folder.

        Gmail message IDs are stable across folders and moves, unlike IMAP UIDs.
        Returns ({gmail_msgid: uid}, [unresolved_gmail_msgids]).
        """
        if not await self._supports_gmail_extensions(mail):
            raise EmailSearchError(
                "Server does not support the Gmail extensions (X-GM-MSGID) required for gmail_msgid lookups"
            )
        resolved: dict[str, str] = {}
        unresolved: list[str] = []
        for msgid in gmail_msgids:
            data = await self._uid_command(mail, "SEARCH", None, f"X-GM-MSGID {msgid}")
            uids = data[0].split() if data and data[0] else []
            if uids:
                resolved[msgid] = uids[-1].decode("ascii")
            else:
                unresolved.append(msgid)
        return resolved, unresolved

    async def _resolve_action_targets(
        self,
        mail: imaplib.IMAP4_SSL,
        email_ids: list[str] | None,
        gmail_msgids: list[str] | None,
    ) -> tuple[list[str], dict[str, str]]:
        """Resolve the requested identifiers to UIDs in the selected folder.

        Returns (uids_to_act_on, uid -> original caller identifier). Unresolved
        gmail_msgids are simply omitted; they surface as not_found because the
        result is built from the full requested list.
        """
        if gmail_msgids:
            resolved, _unresolved = await self._resolve_gmail_msgids(mail, gmail_msgids)
            return list(resolved.values()), {uid: msgid for msgid, uid in resolved.items()}
        ids = email_ids or []
        return list(ids), {uid: uid for uid in ids}

    async def _move_uids(
        self,
        mail: imaplib.IMAP4_SSL,
        email_ids: list[str],
        destination_folder: str,
    ) -> list[str]:
        """Move only the UIDs that exist in the selected folder, without a mailbox-wide EXPUNGE.

        Returns the UIDs actually acted upon (those present in the source folder).
        """
        existing = await self._filter_existing_uids(mail, email_ids)
        if not existing:
            return []
        message_set = ",".join(existing)
        capabilities = await self._get_capability_set(mail)
        if "MOVE" in capabilities:
            await self._uid_command(mail, "MOVE", message_set, destination_folder)
            return existing
        if "UIDPLUS" not in capabilities:
            raise EmailDeletionError(
                "The IMAP server supports neither MOVE nor UIDPLUS; refusing an unsafe mailbox-wide expunge"
            )
        await self._uid_command(mail, "COPY", message_set, destination_folder)
        await self._uid_command(mail, "STORE", message_set, "+FLAGS.SILENT", "(\\Deleted)")
        await self._uid_command(mail, "EXPUNGE", message_set)
        return existing

    async def query_server_capabilities(self) -> None:
        """Query and log IMAP server capabilities for debugging and feature discovery."""
        mail = None
        try:
            mail = await self.connect_imap()
            logging.info("=== IMAP Server Capabilities ===")

            await self._query_capabilities(mail)
            await self._query_namespace(mail)
            await self._query_server_id(mail)

            logging.info("=== End Server Capabilities ===")
        except Exception as e:
            logging.error(f"Error querying server capabilities: {e!s}", exc_info=True)
        finally:
            if mail:
                await self.close_imap_connection(mail)

    async def _query_capabilities(self, mail: imaplib.IMAP4_SSL) -> None:
        """Query and log server capabilities."""
        typ, capability_data = await _run_blocking(mail.capability)
        if typ != "OK" or not capability_data:
            logging.warning(f"Failed to query capabilities: {typ}")
            return

        capabilities = capability_data[0].decode("utf-8")
        logging.info(f"Server capabilities: {capabilities}")

        cap_list = capabilities.split()
        found_caps = [cap for cap in INTERESTING_CAPABILITIES if cap in cap_list]

        if found_caps:
            logging.info(f"Notable capabilities: {', '.join(found_caps)}")
        else:
            logging.info("No notable extended capabilities found")

    async def _query_namespace(self, mail: imaplib.IMAP4_SSL) -> None:
        """Query namespace information if supported."""
        if not hasattr(mail, "namespace"):
            return

        try:
            typ, namespace_data = await _run_blocking(mail.namespace)
            if typ == "OK" and namespace_data:
                namespace_info = namespace_data[0].decode("utf-8") if namespace_data[0] else "None"
                logging.info(f"Namespace info: {namespace_info}")
        except Exception as e:
            logging.debug(f"Namespace query failed (not supported): {e}")

    async def _query_server_id(self, mail: imaplib.IMAP4_SSL) -> None:
        """Query server ID if supported."""

        def query_id() -> str | None:
            mail.send(b"ID NIL")
            typ, _ = mail.response("ID")
            if typ != "OK":
                return None
            response = mail.response("ID")
            if not response or len(response) != 2:
                return None
            response_type, data = response
            if response_type != "OK" or not data or not data[0]:
                return None
            return str(data[0].decode("utf-8"))

        try:
            server_id = await _run_blocking(query_id)
            if server_id:
                logging.info(f"Server ID: {server_id}")
        except Exception as e:
            logging.debug(f"Server ID query failed (not supported): {e}")

    async def search_emails(self, criteria: SearchCriteria) -> Tuple[List[Dict[str, Any]], PaginationInfo]:
        """Search for emails matching the specified criteria.

        Connects to IMAP, selects the appropriate folder, and searches for emails
        based on the provided criteria (date range, keywords, folder). Returns
        a list of email summaries with basic metadata and pagination info.

        Args:
            criteria: SearchCriteria object containing search parameters

        Returns:
            Tuple of:
            - List of dictionaries containing email metadata:
              [{'id': str, 'from': str, 'date': str, 'subject': str,
                'gmail_msgid': str | None}, ...]
            - PaginationInfo with details about total results and pagination

        Raises:
            EmailSearchError: If folder selection, search execution, or
                            email parsing fails
            EmailConnectionError: If IMAP connection fails
        """
        mail = None
        try:
            # Establish IMAP connection
            mail = await self.connect_imap()

            await self._select_folder(mail, criteria.folder)

            # Convert search criteria to IMAP search syntax
            search_criteria = await self._build_search_criteria(criteria)
            logging.debug("Built IMAP search criteria")

            # Execute the search and fetch email summaries
            email_list, pagination = await self._execute_search(mail, search_criteria, criteria)
            logging.info(f"Successfully fetched {len(email_list)} emails")

        except Exception as e:
            logging.error("Email search failed with %s", type(e).__name__)
            raise EmailSearchError(f"Email search failed: {e!s}") from e
        else:
            return email_list, pagination
        finally:
            # Always clean up the IMAP connection
            if mail:
                await self.close_imap_connection(mail)

    async def get_email_content(
        self,
        email_id: Optional[str] = None,
        folder: str = "inbox",
        *,
        gmail_msgid: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Get full content of a specific email.

        Target the email either by folder-scoped IMAP UID (``email_id``) or by stable
        Gmail message id (``gmail_msgid``, X-GM-MSGID). Provide exactly one.

        Args:
            email_id: The IMAP UID of the email to retrieve.
            folder: Folder containing the email (defaults to 'inbox').
            gmail_msgid: Stable Gmail message id, resolved to the current UID in ``folder``.

        Returns:
            Email content with RFC-822 and optional stable Gmail backlink fields.
        """
        if (email_id is None) == (gmail_msgid is None):
            raise ValueError("Provide exactly one of email_id or gmail_msgid")
        if gmail_msgid is None:
            self._validate_email_ids([email_id], maximum=1)  # type: ignore[list-item]
        else:
            self._validate_gmail_msgids([gmail_msgid], maximum=1)
        mail = None
        try:
            mail = await self.connect_imap()
            await self._select_folder(mail, folder)

            if gmail_msgid is not None:
                resolved, _unresolved = await self._resolve_gmail_msgids(mail, [gmail_msgid])
                if not resolved:
                    raise EmailSearchError(f"gmail_msgid {gmail_msgid} was not found in '{folder}'")
                email_id = next(iter(resolved.values()))

            assert email_id is not None  # guaranteed by the exactly-one check above
            msg_data = await self._uid_command(mail, "FETCH", email_id, "(UID BODY.PEEK[])")
            message_response = next(
                (
                    response
                    for response in msg_data
                    if isinstance(response, tuple) and len(response) >= 2 and isinstance(response[1], bytes)
                ),
                None,
            )
            if message_response is not None:
                content = self._format_email_content((message_response,))
                metadata_by_uid = await self._fetch_backlink_metadata(mail, [email_id])
                return self._merge_backlink_fields(content, metadata_by_uid.get(email_id))

        except Exception as e:
            logging.error("Email content fetch failed with %s", type(e).__name__)
            raise EmailSearchError(f"Failed to get email content: {e!s}") from e
        else:
            self._raise_no_email_data_error()
            return None  # This line will never be reached but satisfies mypy
        finally:
            if mail:
                await self.close_imap_connection(mail)

    async def get_email_contents_bulk(
        self, email_ids: List[str], folder: str = "inbox", max_emails: int = 50
    ) -> Dict[str, Any]:
        """Get full content of multiple emails in a single connection.

        Args:
            email_ids: List of email IDs to retrieve
            folder: Folder containing the emails (defaults to 'inbox')
            max_emails: Maximum number of emails to fetch (default 50, for safety)

        Returns:
            Dictionary containing:
            - emails: List of email content dictionaries
            - fetched: Number of emails successfully fetched
            - errors: List of error dictionaries for failed fetches
            Each email includes RFC-822 and optional stable Gmail backlink fields.
        """
        mail = None
        # Limit the number of emails to prevent abuse
        if max_emails <= 0 or max_emails > 500:
            raise ValueError("max_emails must be between 1 and 500")
        limited_ids = email_ids[:max_emails]
        self._validate_email_ids(limited_ids, maximum=max_emails)

        try:
            mail = await self.connect_imap()
            await self._select_folder(mail, folder)

            emails: List[Dict[str, Any]] = []
            errors: List[Dict[str, str]] = []

            # Batch fetch for efficiency using comma-separated IDs
            message_set = ",".join(limited_ids)
            logging.debug("Bulk fetching %s emails", len(limited_ids))

            msg_data_list = await self._uid_command(mail, "FETCH", message_set, "(UID BODY.PEEK[])")
            metadata_by_uid = await self._fetch_backlink_metadata(mail, limited_ids)

            if msg_data_list:
                for msg_data in msg_data_list:
                    if isinstance(msg_data, tuple) and len(msg_data) >= 2 and isinstance(msg_data[1], bytes):
                        try:
                            content = self._format_email_content((msg_data,))
                            uid = self._extract_fetch_number(msg_data[0], "UID")
                            emails.append(self._merge_backlink_fields(content, metadata_by_uid.get(uid or "")))
                        except Exception as e:
                            logging.warning("Failed to format an email: %s", type(e).__name__)
                            errors.append({"error": str(e), "email_id": "unknown"})

            return {
                "emails": emails,
                "fetched": len(emails),
                "errors": errors,
                "truncated": len(email_ids) > max_emails,
                "total_requested": len(email_ids),
            }

        except Exception as e:
            logging.error("Bulk email fetch failed with %s", type(e).__name__)
            raise EmailSearchError(f"Failed to get email contents: {e!s}") from e
        finally:
            if mail:
                await self.close_imap_connection(mail)

    async def download_attachment(
        self, email_id: str, attachment_index: int, output_dir: str, folder: str = "inbox"
    ) -> Optional[Dict[str, Any]]:
        """Download a specific attachment from an email and save to disk.

        Args:
            email_id: The ID of the email containing the attachment
            attachment_index: Zero-based index of the attachment to download
            output_dir: Absolute path to directory where file should be saved
            folder: Folder containing the email (defaults to 'inbox')

        Returns:
            Dictionary containing filename, saved_as, filepath, content_type, and size,
            or None if attachment not found

        Raises:
            ValueError: If attachment_index is negative, output_dir is invalid, or exceeds size limit
            EmailSearchError: If the email cannot be fetched
            EmailAttachmentError: If file cannot be written
        """

        # Security: Validate attachment index
        if attachment_index < 0:
            raise ValueError("Attachment index must be non-negative")
        self._validate_email_ids([email_id], maximum=1)

        # Security: Validate output directory
        _validate_output_directory(output_dir)

        mail = None
        try:
            mail = await self.connect_imap()
            await self._select_folder(mail, folder)

            status, msg_data = await _run_blocking(
                mail.uid,
                "FETCH",
                email_id,
                "(BODY.PEEK[])",
            )
            if status != "OK":
                raise EmailSearchError(f"UID FETCH failed for email {email_id}: {msg_data}")

            if not msg_data or not msg_data[0]:
                raise EmailSearchError(f"Email {email_id} not found")

            raw_email = msg_data[0][1]
            if not isinstance(raw_email, bytes):
                raise EmailSearchError(f"Invalid email data for {email_id}")

            email_body = email.message_from_bytes(raw_email)
            current_index = 0

            for part in email_body.walk():
                content_disposition = part.get("Content-Disposition")
                if content_disposition and "attachment" in content_disposition:
                    if current_index == attachment_index:
                        original_filename = decode_email_header(part.get_filename(), "unnamed")
                        content_type = part.get_content_type()
                        payload = part.get_payload(decode=True)

                        # Ensure payload is bytes
                        if payload is None:
                            raise EmailAttachmentError(f"Attachment {attachment_index} has no content")
                        elif isinstance(payload, bytes):
                            payload_bytes = payload
                        else:
                            payload_bytes = str(payload).encode("utf-8")

                        # Security: Check size limit
                        if len(payload_bytes) > MAX_ATTACHMENT_SIZE:
                            raise ValueError(
                                f"Attachment exceeds maximum size of {MAX_ATTACHMENT_SIZE} bytes "
                                f"(actual: {len(payload_bytes)} bytes)"
                            )

                        # Sanitize filename and get unique path
                        sanitized_name = _sanitize_filename(original_filename)
                        filepath, actual_filename = _get_unique_filepath(output_dir, sanitized_name)

                        # Write file to disk
                        try:
                            with Path(filepath).open("xb") as f:
                                f.write(payload_bytes)
                        except OSError as e:
                            raise EmailAttachmentError(f"Failed to write attachment to {filepath}: {e}") from e

                        return {
                            "filename": original_filename,
                            "saved_as": actual_filename,
                            "filepath": filepath,
                            "content_type": content_type,
                            "size": len(payload_bytes),
                            "email_id": email_id,
                        }
                    current_index += 1

            # Attachment not found at the given index
            return None

        except (ValueError, EmailAttachmentError):
            raise
        except Exception as e:
            logging.error("Attachment download failed with %s", type(e).__name__)
            raise EmailSearchError(f"Failed to download attachment: {e!s}") from e
        finally:
            if mail:
                await self.close_imap_connection(mail)

    async def export_email_to_markdown(
        self,
        email_id: str,
        output_dir: str,
        folder: str = "inbox",
        include_attachments: bool = True,
    ) -> Dict[str, Any]:
        """Export a single email to a markdown file with optional attachments.

        Writes email content directly to disk without returning it to caller.
        This is context-efficient for bulk operations.

        Args:
            email_id: The ID of the email to export
            output_dir: Absolute path to directory where files should be saved
            folder: Folder containing the email (defaults to 'inbox')
            include_attachments: If True (default), download attachments alongside

        Returns:
            Dictionary containing:
            - email_id: The exported email ID
            - filepath: Path to the created markdown file
            - attachments: List of {filepath, size} for downloaded attachments

        Raises:
            ValueError: If output_dir is invalid
            EmailSearchError: If email cannot be fetched
            EmailAttachmentError: If attachment download fails
        """

        # Validate output directory
        _validate_output_directory(output_dir)

        # Fetch email content
        content = await self.get_email_content(email_id, folder)
        if not content:
            raise EmailSearchError(f"Email {email_id} not found in folder '{folder}'")

        # Generate filename and get unique path
        filename = _generate_email_filename(content)
        filepath, _actual_filename = _get_unique_filepath(output_dir, filename)

        # Format as markdown
        markdown_content = _format_email_as_markdown(content)

        # Write markdown file
        try:
            with Path(filepath).open("x", encoding="utf-8") as f:
                f.write(markdown_content)
        except OSError as e:
            raise EmailAttachmentError(f"Failed to write markdown file to {filepath}: {e}") from e

        result: Dict[str, Any] = {
            "email_id": email_id,
            "filepath": filepath,
            "attachments": [],
        }

        # Download attachments if requested
        if include_attachments:
            raw_attachments: Any = content.get("attachments", [])
            attachments: List[Dict[str, Any]] = raw_attachments if isinstance(raw_attachments, list) else []
            for att in attachments:
                att_index: int = int(att.get("index", 0))
                try:
                    att_result = await self.download_attachment(email_id, att_index, output_dir, folder)
                    if att_result:
                        result["attachments"].append(
                            {
                                "filepath": att_result["filepath"],
                                "size": att_result["size"],
                                "filename": att_result["saved_as"],
                            }
                        )
                except Exception as e:
                    logging.warning("Attachment download failed with %s", type(e).__name__)
                    result["attachments"].append(
                        {
                            "error": str(e),
                            "index": att_index,
                        }
                    )

        return result

    async def export_emails_bulk(
        self,
        email_ids: List[str],
        output_dir: str,
        folder: str = "inbox",
        include_attachments: bool = True,
    ) -> Dict[str, Any]:
        """Export multiple emails to markdown files with optional attachments.

        Writes emails directly to disk without returning content to caller.
        This is context-efficient for bulk export operations.

        Args:
            email_ids: List of email IDs to export
            output_dir: Absolute path to directory where files should be saved
            folder: Folder containing the emails (defaults to 'inbox')
            include_attachments: If True (default), download attachments alongside

        Returns:
            Dictionary containing:
            - output_dir: Path where files were saved
            - emails_exported: Number of emails successfully exported
            - files_created: List of {email_id, filepath} for each markdown file
            - attachments_downloaded: List of {email_id, filepath, size} for attachments
            - errors: List of {email_id, error} for any failures
        """
        self._validate_email_ids(email_ids)
        # Validate output directory once
        _validate_output_directory(output_dir)

        result: Dict[str, Any] = {
            "output_dir": output_dir,
            "emails_exported": 0,
            "files_created": [],
            "attachments_downloaded": [],
            "errors": [],
        }

        for email_id in email_ids:
            try:
                export_result = await self.export_email_to_markdown(email_id, output_dir, folder, include_attachments)
                result["files_created"].append(
                    {
                        "email_id": email_id,
                        "filepath": export_result["filepath"],
                    }
                )
                result["emails_exported"] += 1

                # Add attachments to summary
                for att in export_result.get("attachments", []):
                    if "error" not in att:
                        result["attachments_downloaded"].append(
                            {
                                "email_id": email_id,
                                "filepath": att["filepath"],
                                "size": att["size"],
                            }
                        )
                    else:
                        result["errors"].append(
                            {
                                "email_id": email_id,
                                "error": f"Attachment download failed: {att['error']}",
                            }
                        )

            except Exception as e:
                logging.warning("Email export failed with %s", type(e).__name__)
                result["errors"].append(
                    {
                        "email_id": email_id,
                        "error": str(e),
                    }
                )

        return result

    async def send_email(self, message: EmailMessage) -> None:
        """Send email with specified parameters."""
        try:
            # Create message
            msg = MIMEMultipart()
            msg["From"] = self.email_address
            msg["To"] = ", ".join(message.to_addresses)
            if message.cc_addresses:
                msg["Cc"] = ", ".join(message.cc_addresses)
            msg["Subject"] = message.subject

            # Add body
            msg.attach(MIMEText(message.content, "plain", "utf-8"))

            # Send email
            await self._send_via_smtp(msg, message.to_addresses, message.cc_addresses)
            logging.info("Email sent successfully")

        except Exception as e:
            logging.error("Email send failed with %s", type(e).__name__)
            raise EmailSendError(f"Failed to send email: {e!s}") from e

    async def delete_email(
        self,
        email_ids: Union[str, List[str], None] = None,
        folder: str = "inbox",
        permanent: bool = False,
        *,
        gmail_msgids: Union[str, List[str], None] = None,
    ) -> "MailboxOperationResult":
        """Delete one or more emails by moving to trash or permanently deleting.

        Target messages either by folder-scoped IMAP UID (``email_ids``) or by stable
        Gmail message id (``gmail_msgids``, X-GM-MSGID), which survives folder moves.
        Provide exactly one.

        Args:
            email_ids: IMAP UID(s) to delete (string or list).
            folder: The folder containing the email(s) ('inbox' or 'sent').
            permanent: If True, permanently delete (mark + expunge). Else move to trash.
            gmail_msgids: Stable Gmail message id(s), resolved to the current UID in
                ``folder`` server-side.

        Returns:
            MailboxOperationResult (in the identifier space you supplied) describing which
            messages were deleted and which were not found in the folder.

        Raises:
            EmailDeletionError: If the deletion fails, or if none of the requested
                messages were found in the folder.
            EmailConnectionError: If IMAP connection fails
        """
        ids_list, gmail_list, requested = self._normalize_target_identifiers(email_ids, gmail_msgids, action="deletion")

        if permanent:
            affected = await self._permanent_delete_emails(folder, email_ids=ids_list, gmail_msgids=gmail_list)
        else:
            affected = await self._move_emails_to_trash(folder, email_ids=ids_list, gmail_msgids=gmail_list)

        if not affected:
            raise EmailDeletionError(self._not_found_message(requested, gmail_list is not None, folder, "deleting"))
        return MailboxOperationResult.from_request(requested, affected)

    @staticmethod
    def _normalize_target_identifiers(
        email_ids: Union[str, List[str], None],
        gmail_msgids: Union[str, List[str], None],
        *,
        action: str,
    ) -> tuple[list[str] | None, list[str] | None, list[str]]:
        """Validate and normalize the requested identifiers to exactly one kind.

        Returns (uid_list_or_None, gmail_list_or_None, requested_identifiers).
        """
        ids_list = [email_ids] if isinstance(email_ids, str) else (list(email_ids) if email_ids else None)
        gmail_list = [gmail_msgids] if isinstance(gmail_msgids, str) else (list(gmail_msgids) if gmail_msgids else None)
        if ids_list and gmail_list:
            raise EmailDeletionError("Provide either email_ids or gmail_msgids, not both")
        if not ids_list and not gmail_list:
            raise EmailDeletionError(f"No email_ids or gmail_msgids provided for {action}")
        if gmail_list:
            EmailClient._validate_gmail_msgids(gmail_list)
            return None, gmail_list, gmail_list
        assert ids_list is not None
        EmailClient._validate_email_ids(ids_list)
        return ids_list, None, ids_list

    @staticmethod
    def _not_found_message(requested: list[str], by_gmail: bool, folder: str, verb: str) -> str:
        if by_gmail:
            return (
                f"None of the {len(requested)} requested gmail_msgids were found in '{folder}'. "
                f"The messages may be in a different folder."
            )
        return (
            f"None of the {len(requested)} requested UIDs exist in '{folder}'. "
            "IMAP UIDs are folder-scoped and change when a message moves; re-search "
            f"'{folder}' to get its current UIDs before {verb}."
        )

    async def _permanent_delete_emails(
        self,
        folder: str,
        *,
        email_ids: list[str] | None = None,
        gmail_msgids: list[str] | None = None,
    ) -> List[str]:
        """Permanently delete matching messages; return the identifiers acted upon."""
        mail = None
        try:
            mail = await self.connect_imap()
            await self._select_folder(mail, folder)

            capabilities = await self._get_capability_set(mail)
            if "UIDPLUS" not in capabilities:
                raise EmailDeletionError(
                    "UIDPLUS is required for targeted permanent deletion; refusing mailbox-wide EXPUNGE"
                )
            target_uids, uid_to_ident = await self._resolve_action_targets(mail, email_ids, gmail_msgids)
            existing = await self._filter_existing_uids(mail, target_uids)
            if not existing:
                return []
            logging.info("Permanently deleting %s of %s requested emails by UID", len(existing), len(target_uids))
            message_set = ",".join(existing)
            await self._uid_command(mail, "STORE", message_set, "+FLAGS.SILENT", "(\\Deleted)")
            await self._uid_command(mail, "EXPUNGE", message_set)
            logging.info("Successfully permanently deleted %s emails", len(existing))
            return [uid_to_ident[uid] for uid in existing]

        except Exception as e:
            logging.error("Permanent delete failed with %s", type(e).__name__)
            raise EmailDeletionError(f"Failed to permanently delete emails: {e!s}") from e
        finally:
            if mail:
                await self.close_imap_connection(mail)

    async def _move_emails_to_trash(
        self,
        folder: str,
        *,
        email_ids: list[str] | None = None,
        gmail_msgids: list[str] | None = None,
    ) -> List[str]:
        """Move matching messages to trash; return the identifiers acted upon."""
        mail = None
        try:
            mail = await self.connect_imap()
            await self._select_folder(mail, folder)

            # Determine trash folder name
            trash_folder = await self._get_trash_folder_name(mail)

            target_uids, uid_to_ident = await self._resolve_action_targets(mail, email_ids, gmail_msgids)
            logging.info("Moving up to %s emails to trash by UID", len(target_uids))
            affected = await self._move_uids(mail, target_uids, trash_folder)
            logging.info("Successfully moved %s of %s requested emails to trash", len(affected), len(target_uids))
            return [uid_to_ident[uid] for uid in affected]

        except Exception as e:
            logging.error("Move to trash failed with %s", type(e).__name__)
            raise EmailDeletionError(f"Failed to move emails to trash: {e!s}") from e
        finally:
            if mail:
                await self.close_imap_connection(mail)

    async def _select_folder(self, mail: imaplib.IMAP4_SSL, folder: str) -> None:
        """Select the appropriate email folder by name.

        Args:
            mail: Active IMAP connection
            folder: Folder name to select. Can be:
                   - 'inbox' or 'INBOX' (case insensitive)
                   - 'sent' (maps to Gmail sent folder)
                   - Any exact folder name from list_folders()

        Raises:
            EmailSearchError: If folder selection fails
        """
        logging.debug("Selecting email folder")

        # Handle special folder mappings for backwards compatibility
        folder_to_select = folder.strip('"')
        if folder.lower() == "inbox":
            folder_to_select = "INBOX"
        elif folder.lower() == "sent":
            folder_to_select = await self._find_special_use_folder(mail, b"\\Sent") or "[Gmail]/Sent Mail"

        try:
            quoted_folder = quote_imap_mailbox(folder_to_select)
            result = await _run_blocking(mail.select, quoted_folder)
            if result[0] != "OK":
                raise EmailSearchError(f"Failed to select folder {quoted_folder}: {result[1]}")

            logging.debug("Successfully selected email folder")

        except Exception as e:
            logging.error("Folder selection failed with %s", type(e).__name__)
            raise EmailSearchError(f"Failed to select folder '{folder}': {e!s}") from e

    async def _find_special_use_folder(self, mail: imaplib.IMAP4_SSL, attribute: bytes) -> str | None:
        """Find a mailbox advertised with an IMAP SPECIAL-USE attribute."""
        status, folders = await _run_blocking(mail.list)
        if status != "OK" or not folders:
            return None
        wanted = attribute.decode("ascii", errors="ignore").lower()
        for entry in folders:
            folder_info = parse_list_response_line(entry)
            if folder_info is None:
                continue
            # SPECIAL-USE attributes are backslash-prefixed flags in the (...) list,
            # e.g. "\Trash". Match case-insensitively against the parsed attributes.
            if wanted in folder_info["attributes"].lower():
                return folder_info["name"]
        return None

    async def _get_trash_folder_name(self, mail: imaplib.IMAP4_SSL) -> str:
        """Determine the correct trash folder name for this email provider."""
        special_use = await self._find_special_use_folder(mail, b"\\Trash")
        if special_use:
            return quote_imap_mailbox(special_use)

        # Fall back to common names when SPECIAL-USE is unavailable.
        status, folders = await _run_blocking(mail.list)
        if status != "OK":
            raise EmailDeletionError("Failed to list folders while locating trash")
        folder_names = []
        if folders:
            for entry in folders:
                folder_info = parse_list_response_line(entry)
                if folder_info is None:
                    continue
                if "\\Trash" in folder_info["attributes"] or "Bin" in folder_info["name"]:
                    folder_names.append(folder_info["name"])

        # Use the first trash folder found, or default to Gmail Bin
        if folder_names:
            trash_folder = quote_imap_mailbox(folder_names[0])
            logging.debug("Found trash folder by name/attribute")
            return trash_folder

        # Default fallback
        default_trash = '"[Gmail]/Bin"'
        logging.debug("Using fallback trash folder")
        return default_trash

    async def list_folders(self) -> List[Dict[str, str]]:
        """List all available IMAP folders with their attributes.

        Returns:
            List of dictionaries containing folder information:
            [{'name': str, 'display_name': str, 'attributes': str}, ...]

        Raises:
            EmailConnectionError: If IMAP connection fails
            EmailSearchError: If folder listing fails
        """
        mail = None
        try:
            mail = await self.connect_imap()

            # List all folders
            logging.info("Listing all available IMAP folders")
            status, folders = await _run_blocking(mail.list)
            if status != "OK":
                raise EmailSearchError("IMAP LIST failed")

            folder_list = []
            if folders:
                for entry in folders:
                    folder_info = parse_list_response_line(entry)
                    if folder_info is not None:
                        folder_list.append(folder_info)

            # Sort folders for consistent ordering (inbox first, then alphabetical)
            folder_list.sort(
                key=lambda x: (
                    x["name"].lower() != "inbox",  # inbox first
                    x["display_name"].lower(),
                )
            )

            logging.info(f"Successfully listed {len(folder_list)} folders")
        except Exception as e:
            logging.error("Folder listing failed with %s", type(e).__name__)
            raise EmailSearchError(f"Failed to list folders: {e!s}") from e
        else:
            return folder_list
        finally:
            if mail:
                await self.close_imap_connection(mail)

    async def move_email(
        self,
        email_ids: Union[str, List[str], None] = None,
        source_folder: str = "inbox",
        destination_folder: str = "",
        *,
        gmail_msgids: Union[str, List[str], None] = None,
    ) -> "MailboxOperationResult":
        """Move one or more emails from one folder to another.

        Target messages either by folder-scoped IMAP UID (``email_ids``) or by stable
        Gmail message id (``gmail_msgids``, X-GM-MSGID), which survives folder moves.
        Provide exactly one.

        Args:
            email_ids: IMAP UID(s) to move (string or list).
            source_folder: The folder containing the email(s) (e.g., 'inbox', 'INBOX').
            destination_folder: The destination folder (e.g., 'Archive', '[Gmail]/Important').
            gmail_msgids: Stable Gmail message id(s), resolved to the current UID in
                ``source_folder`` server-side.

        Returns:
            MailboxOperationResult (in the identifier space you supplied) describing which
            messages were moved and which were not found in source_folder.

        Raises:
            EmailDeletionError: If the move fails, or if none of the requested messages
                were found in source_folder.
            EmailConnectionError: If IMAP connection fails
        """
        # Validate that source and destination are different
        if source_folder == destination_folder:
            raise EmailDeletionError(
                f"Source and destination folders are the same: '{source_folder}'. No move operation needed."
            )

        ids_list, gmail_list, requested = self._normalize_target_identifiers(email_ids, gmail_msgids, action="moving")

        affected = await self._move_emails_batch(
            source_folder, destination_folder, email_ids=ids_list, gmail_msgids=gmail_list
        )
        if not affected:
            raise EmailDeletionError(
                self._not_found_message(requested, gmail_list is not None, source_folder, "moving")
            )
        return MailboxOperationResult.from_request(requested, affected)

    async def _move_emails_batch(
        self,
        source_folder: str,
        destination_folder: str,
        *,
        email_ids: list[str] | None = None,
        gmail_msgids: list[str] | None = None,
    ) -> List[str]:
        """Move matching messages out of the source folder; return the identifiers moved."""
        mail = None
        try:
            mail = await self.connect_imap()

            # Select the source folder
            await self._select_folder(mail, source_folder)

            # Validate destination folder by checking if it exists
            await self._validate_destination_folder(mail, destination_folder)

            # Ensure destination folder is properly quoted
            quoted_dest = quote_imap_mailbox(destination_folder)

            target_uids, uid_to_ident = await self._resolve_action_targets(mail, email_ids, gmail_msgids)
            logging.info(
                "Moving up to %s emails from '%s' to '%s' by UID",
                len(target_uids),
                source_folder,
                destination_folder,
            )
            affected = await self._move_uids(mail, target_uids, quoted_dest)
            logging.info("Successfully moved %s of %s requested emails", len(affected), len(target_uids))
            return [uid_to_ident[uid] for uid in affected]

        except Exception as e:
            logging.error("Email move failed with %s", type(e).__name__)
            raise EmailDeletionError(
                f"Failed to move emails from '{source_folder}' to '{destination_folder}': {e!s}"
            ) from e
        finally:
            if mail:
                await self.close_imap_connection(mail)

    async def _validate_destination_folder(self, mail: imaplib.IMAP4_SSL, folder_name: str) -> None:
        """Validate that a destination folder exists.

        Args:
            mail: Active IMAP connection
            folder_name: Folder name to validate

        Raises:
            EmailDeletionError: If folder doesn't exist
        """
        try:
            # List all folders to check if destination exists
            status, folders = await _run_blocking(mail.list)
            if status != "OK":
                raise EmailDeletionError("IMAP LIST failed")

            # Check if folder exists (compare unquoted names so a caller passing
            # either "Foo" or Foo matches the server's listing).
            wanted = folder_name.strip('"')
            folder_exists = False
            if folders:
                for entry in folders:
                    folder_info = parse_list_response_line(entry)
                    if folder_info is not None and folder_info["name"] == wanted:
                        folder_exists = True
                        break

            if not folder_exists:
                raise EmailDeletionError(f"Destination folder '{folder_name}' does not exist")

            logging.debug("Validated destination folder")

        except EmailDeletionError:
            raise  # Re-raise our custom error
        except Exception as e:
            logging.error("Folder validation failed with %s", type(e).__name__)
            raise EmailDeletionError(f"Failed to validate destination folder '{folder_name}': {e!s}") from e

    async def count_daily_emails(self, start_date: str, end_date: str) -> Dict[str, int]:
        """Count emails received for each day in the specified date range.

        Iterates through each day between start_date and end_date (inclusive)
        and counts the number of emails received on that specific day.

        Args:
            start_date: Start date in YYYY-MM-DD format (inclusive)
            end_date: End date in YYYY-MM-DD format (inclusive)

        Returns:
            Dictionary mapping date strings (YYYY-MM-DD) to email counts.
            Returns -1 for dates where the count operation timed out.

        Raises:
            EmailSearchError: If IMAP connection fails or search operation fails
            ValueError: If date format is invalid
        """
        mail = None
        try:
            # Connect to IMAP server and select inbox
            mail = await self.connect_imap()
            await self._select_folder(mail, "inbox")

            # Parse input date strings into datetime objects for iteration
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")  # Start of date range
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")  # End of date range (inclusive)
            if start_dt > end_dt:
                raise ValueError("start_date must be on or before end_date")

            # Dictionary to store daily email counts: {"YYYY-MM-DD": count}
            daily_counts = {}
            current_date = start_dt  # Iterator starting from start date

            # Iterate through each day in the range (inclusive of end date)
            while current_date <= end_dt:
                # Convert to IMAP date format: "DD-MMM-YYYY" (e.g., "15-Dec-2024")
                date_str = current_date.strftime("%d-%b-%Y")
                # Build IMAP search criteria for emails received on this specific date
                search_criteria = f'(ON "{date_str}")'

                count = await self._count_emails(mail, search_criteria)
                daily_counts[current_date.strftime("%Y-%m-%d")] = count

                # Move to next day
                current_date += timedelta(days=1)

        except Exception as e:
            logging.error("Daily count failed with %s", type(e).__name__)
            raise EmailSearchError(f"Failed to count emails: {e!s}") from e
        else:
            return daily_counts
        finally:
            if mail:
                await self.close_imap_connection(mail)

    async def _build_search_criteria(self, criteria: SearchCriteria) -> str:
        """Convert SearchCriteria object into IMAP search syntax.

        Transforms user-friendly search parameters into the specific syntax
        required by IMAP SEARCH command. Handles date range logic, keyword
        searching, and applies sensible defaults.

        Args:
            criteria: SearchCriteria containing user search parameters

        Returns:
            IMAP search criteria string ready for mail.search() command

        Note:
            - No default dates applied - searches all emails if no dates provided
            - Single day searches use ON command for efficiency
            - Date ranges use SINCE + BEFORE with exclusive end date
            - Supports separate filtering by subject, sender, and body fields
            - Supports partial date ranges (start only, end only, or both)
            - Multiple search criteria are combined with AND logic
        """
        search_criteria_parts = []

        # Add date criteria if provided
        date_criteria = self._build_date_criteria(criteria)
        if date_criteria:
            search_criteria_parts.append(date_criteria)

        # Add field-specific searches
        field_criteria = self._build_field_criteria(criteria)
        search_criteria_parts.extend(field_criteria)

        # Combine all criteria parts
        return self._combine_criteria_parts(search_criteria_parts)

    def _build_date_criteria(self, criteria: SearchCriteria) -> str:
        """Build date-based search criteria from SearchCriteria."""
        if not (criteria.start_date or criteria.end_date):
            logging.info("No date criteria provided - searching all emails")
            return ""

        if criteria.start_date and criteria.end_date:
            return self._build_date_range_criteria(criteria.start_date, criteria.end_date)
        elif criteria.start_date:
            return self._build_start_date_criteria(criteria.start_date)
        elif criteria.end_date:  # criteria.end_date only
            return self._build_end_date_criteria(criteria.end_date)
        else:
            return ""

    def _build_date_range_criteria(self, start_date: str, end_date: str) -> str:
        """Build criteria for date range or single day."""
        start_date_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_date_dt = datetime.strptime(end_date, "%Y-%m-%d")
        logging.debug("Date range search requested")

        imap_start_date = start_date_dt.strftime("%d-%b-%Y")

        if start_date_dt.date() == end_date_dt.date():
            # Single day search - more efficient with ON command
            date_criteria = f'ON "{imap_start_date}"'
            logging.debug("Built single-day criterion")
            return date_criteria
        else:
            # Date range search - BEFORE is exclusive, so add 1 day to end date
            imap_next_day_after_end = (end_date_dt + timedelta(days=1)).strftime("%d-%b-%Y")
            date_criteria = f'SINCE "{imap_start_date}" BEFORE "{imap_next_day_after_end}"'
            logging.debug("Built date-range criterion")
            return date_criteria

    def _build_start_date_criteria(self, start_date: str) -> str:
        """Build criteria for start date only."""
        start_date_dt = datetime.strptime(start_date, "%Y-%m-%d")
        imap_start_date = start_date_dt.strftime("%d-%b-%Y")
        date_criteria = f'SINCE "{imap_start_date}"'
        logging.debug("Built start-date criterion")
        return date_criteria

    def _build_end_date_criteria(self, end_date: str) -> str:
        """Build criteria for end date only."""
        end_date_dt = datetime.strptime(end_date, "%Y-%m-%d")
        # BEFORE is exclusive, so add 1 day to end date
        imap_next_day_after_end = (end_date_dt + timedelta(days=1)).strftime("%d-%b-%Y")
        date_criteria = f'BEFORE "{imap_next_day_after_end}"'
        logging.debug("Built end-date criterion")
        return date_criteria

    def _build_field_criteria(self, criteria: SearchCriteria) -> List[str]:
        """Build field-specific search criteria with proper escaping.

        Escapes user input to prevent IMAP injection attacks and syntax errors.
        """
        field_criteria = []

        if criteria.subject:
            escaped_subject = escape_imap_string(criteria.subject)
            subject_criteria = f'SUBJECT "{escaped_subject}"'
            field_criteria.append(subject_criteria)
            logging.debug("Added subject search criterion")

        if criteria.sender:
            escaped_sender = escape_imap_string(criteria.sender)
            sender_criteria = f'FROM "{escaped_sender}"'
            field_criteria.append(sender_criteria)
            logging.debug("Added sender search criterion")

        if criteria.to:
            escaped_to = escape_imap_string(criteria.to)
            to_criteria = f'TO "{escaped_to}"'
            field_criteria.append(to_criteria)
            logging.debug("Added recipient search criterion")

        if criteria.body:
            escaped_body = escape_imap_string(criteria.body)
            body_criteria = f'BODY "{escaped_body}"'
            field_criteria.append(body_criteria)
            logging.debug("Added body search criterion")

        return field_criteria

    def _combine_criteria_parts(self, search_criteria_parts: List[str]) -> str:
        """Combine search criteria parts into final search string."""
        if not search_criteria_parts:
            search_criteria = "ALL"
        elif len(search_criteria_parts) == 1:
            search_criteria = search_criteria_parts[0]
        else:
            # Multiple criteria - combine with AND logic
            search_criteria = "(" + " ".join(search_criteria_parts) + ")"

        logging.debug("Combined IMAP search criteria")
        return search_criteria

    async def _ordered_search_uids(self, mail: imaplib.IMAP4_SSL, search_criteria: str, direction: str) -> List[bytes]:
        """Return all matching UIDs ordered by arrival date for the requested direction.

        Positional pagination is only meaningful over a stable, date-ordered sequence.
        IMAP UIDs are assigned in folder-append order, not by message date, so ordering
        by raw UID makes ``direction="newest"`` return the highest-UID messages rather
        than the most recently received — a window that can span years by date and makes
        ``start_from`` unreliable as "the Nth most-recent email".

        When the server advertises the SORT extension (RFC 5256) we ask it for a
        server-side ``UID SORT (ARRIVAL)`` — ascending by internal (received) date,
        with equal dates broken deterministically by the server. ``newest`` reverses
        that list. Without SORT we fall back to raw UID order (the previous behaviour)
        and log that ordering is by UID, not date.
        """
        if await self._supports_sort(mail):
            # SORT requires a charset argument; UTF-8 is universally supported by
            # servers advertising the extension. ARRIVAL == INTERNALDATE (received).
            sorted_data = await self._uid_command(mail, "SORT", "(ARRIVAL)", "UTF-8", search_criteria)
            ordered = sorted_data[0].split() if sorted_data and sorted_data[0] else []
            # SORT returns ascending (oldest first); reverse for newest first.
            if direction == "newest":
                ordered = list(reversed(ordered))
            logging.debug("Ordered %s UIDs by arrival date via server SORT (%s)", len(ordered), direction)
            return ordered

        # Fallback: no SORT extension. Order by UID, which only approximates arrival
        # order and is not a strict date sort. Positional paging remains stable within
        # a single result set but is not guaranteed to be date-ordered.
        logging.warning("Server lacks SORT extension; ordering by UID instead of arrival date")
        messages = await self._uid_command(mail, "SEARCH", None, search_criteria)
        ids = messages[0].split() if messages and messages[0] else []
        if direction == "newest":
            ids = list(reversed(ids))
        return ids

    async def _search_with_pagination(
        self, mail: imaplib.IMAP4_SSL, search_criteria: str, criteria: SearchCriteria
    ) -> Tuple[List[bytes], int]:
        """Execute a UID search and apply deterministic, date-ordered client-side pagination.

        Args:
            mail: Active IMAP4_SSL connection
            search_criteria: IMAP search criteria string
            criteria: SearchCriteria object with pagination parameters

        Returns:
            Tuple of (paginated message ID bytes, total count of matching messages)
        """
        all_message_ids = await self._ordered_search_uids(mail, search_criteria, criteria.direction)
        total_count = len(all_message_ids)
        if total_count == 0:
            return [], 0

        logging.info("Found %s total messages, applying pagination", total_count)

        # Apply client-side pagination over the date-ordered sequence.
        start_idx = criteria.start_from
        end_idx = start_idx + criteria.max_results
        paginated_ids: List[bytes] = all_message_ids[start_idx:end_idx]

        logging.info(
            "Returning %s messages after pagination (direction: %s)",
            len(paginated_ids),
            criteria.direction,
        )
        return paginated_ids, total_count

    async def _execute_search(
        self, mail: imaplib.IMAP4_SSL, search_criteria: str, criteria: SearchCriteria
    ) -> Tuple[List[Dict[str, Any]], PaginationInfo]:
        """Execute IMAP search with pagination support and return formatted email summaries.

        Performs an IMAP SEARCH command with the given criteria, supports pagination
        using either ESEARCH (if available) or regular SEARCH with client-side pagination.
        Fetches email headers using efficient batch fetching.

        Args:
            mail: Active IMAP4_SSL connection with a folder already selected
            search_criteria: IMAP search criteria string (e.g., 'SINCE "01-Jan-2024"')
            criteria: SearchCriteria object containing pagination parameters

        Returns:
            Tuple of:
            - List of email summary dictionaries, each containing:
              - 'id': Email message ID (string) - used for fetching full content later
              - 'from': Sender email address and name (string)
              - 'date': Email date header (string) - as received from server
              - 'subject': Email subject line (string) - "No Subject" if missing
            - PaginationInfo with pagination details

            Returns empty list if no emails match the search criteria.
            Limited by criteria.max_results for performance.

        Performance:
            Uses batch FETCH with comma-separated message IDs for efficiency,
            reducing network round-trips compared to individual fetch operations.
            Supports ESEARCH for server-side pagination when available.
        """
        message_ids, total_count = await self._search_with_pagination(mail, search_criteria, criteria)

        if not message_ids:
            logging.info("No messages found matching criteria")
            pagination = PaginationInfo(
                total_available=total_count,
                returned=0,
                start_from=criteria.start_from,
                has_more=False,
                next_start_from=None,
            )
            return [], pagination

        logging.info(
            f"Found {len(message_ids)} messages for pagination range "
            f"{criteria.start_from}-{criteria.start_from + criteria.max_results}"
        )

        # Create comma-separated list of message IDs for batch fetch
        message_set = b",".join(message_ids).decode()
        logging.debug("Batch fetching %s email headers", len(message_ids))

        # Fetch only headers; full bodies and attachments are retrieved on demand.
        gmail_fetch_item = " X-GM-MSGID" if await self._supports_gmail_extensions(mail) else ""
        msg_data_list = await self._uid_command(
            mail,
            "FETCH",
            message_set,
            f"(UID{gmail_fetch_item} BODY.PEEK[HEADER.FIELDS (FROM TO DATE SUBJECT MESSAGE-ID)])",
        )

        # Process the batch response into email summaries
        email_list: List[Dict[str, Any]] = []
        if msg_data_list:
            for msg_data in msg_data_list:
                if msg_data and len(msg_data) >= 2:  # Ensure we have both ID and content
                    try:
                        email_list.append(self._format_email_summary((msg_data,)))
                    except Exception as e:
                        logging.warning("Failed to format email summary: %s", type(e).__name__)
                        continue

        logging.info(f"Successfully processed {len(email_list)} emails from batch fetch")

        # Calculate pagination info
        returned = len(email_list)
        # Advance by UIDs consumed, not by successfully parsed messages, so a
        # malformed email cannot cause duplicate pages or an infinite loop.
        end_position = criteria.start_from + len(message_ids)
        has_more = end_position < total_count
        next_start_from = end_position if has_more else None

        pagination = PaginationInfo(
            total_available=total_count,
            returned=returned,
            start_from=criteria.start_from,
            has_more=has_more,
            next_start_from=next_start_from,
        )

        return email_list, pagination

    async def _send_via_smtp(
        self, msg: MIMEMultipart, to_addresses: List[str], cc_addresses: Optional[List[str]]
    ) -> None:
        """Send email via SMTP."""

        def send_sync() -> None:
            context = ssl.create_default_context()
            if self.config.smtp_security == "ssl":
                smtp_server: smtplib.SMTP = smtplib.SMTP_SSL(
                    self.smtp_server,
                    self.smtp_port,
                    timeout=self.config.connection_timeout,
                    context=context,
                )
            else:
                smtp_server = smtplib.SMTP(
                    self.smtp_server,
                    self.smtp_port,
                    timeout=self.config.connection_timeout,
                )
                smtp_server.starttls(context=context)
            with smtp_server:
                smtp_server.login(self.email_address, self.email_password)
                all_recipients = to_addresses + (cc_addresses or [])
                result = smtp_server.send_message(msg, self.email_address, all_recipients)

                if result:
                    raise EmailSendError(f"Failed to send to some recipients: {result}")

        await _run_blocking(send_sync)

    async def _count_emails(self, mail: imaplib.IMAP4_SSL, search_criteria: str) -> int:
        """Count emails matching search criteria."""
        messages = await self._uid_command(mail, "SEARCH", None, search_criteria)
        return len(messages[0].split()) if messages and messages[0] else 0

    async def count_emails(self, criteria: SearchCriteria) -> int:
        """Count emails matching a filter without fetching rows or creating a collection."""
        mail = None
        try:
            mail = await self.connect_imap()
            await self._select_folder(mail, criteria.folder)
            search_criteria = await self._build_search_criteria(criteria)
            return await self._count_emails(mail, search_criteria)
        except Exception as e:
            logging.error("Email count failed with %s", type(e).__name__)
            raise EmailSearchError(f"Failed to count emails: {e!s}") from e
        finally:
            if mail:
                await self.close_imap_connection(mail)

    async def aggregate_emails(
        self, criteria: SearchCriteria, group_by: str, top_n: int = 20, batch_size: int = 500
    ) -> Dict[str, Any]:
        """Group matching emails server-side and return a small top-N frequency table.

        Fetches only the header/metadata needed for the grouping key (never bodies),
        aggregates in-process, and returns counts -- no collection is created and no
        per-row data crosses into context. This replaces paging thousands of rows to
        count them client-side.

        Args:
            criteria: Search filter (folder, dates, sender/subject/body substrings).
            group_by: One of 'sender', 'recipient', 'date'.
            top_n: Number of most-frequent groups to return.
            batch_size: UIDs fetched per IMAP round-trip.

        Returns:
            {group_by, folder, total_matched, total_grouped, distinct_keys, top_n,
             groups: [{key, count}, ...], truncated}
        """
        if group_by not in AGGREGATE_GROUPINGS:
            allowed = ", ".join(sorted(AGGREGATE_GROUPINGS))
            raise EmailSearchError(f"Unsupported group_by '{group_by}'. Supported: {allowed}")
        if not isinstance(top_n, int) or isinstance(top_n, bool) or top_n <= 0:
            raise ValueError("top_n must be a positive integer")

        mail = None
        try:
            mail = await self.connect_imap()
            await self._select_folder(mail, criteria.folder)
            search_criteria = await self._build_search_criteria(criteria)
            messages = await self._uid_command(mail, "SEARCH", None, search_criteria)
            uids = messages[0].split() if messages and messages[0] else []
            total_matched = len(uids)

            counts: Counter[str] = Counter()
            for start in range(0, len(uids), batch_size):
                batch = uids[start : start + batch_size]
                counts.update(await self._fetch_group_keys(mail, batch, group_by))

            total_grouped = sum(counts.values())
            top = counts.most_common(top_n)
            return {
                "group_by": group_by,
                "folder": criteria.folder,
                "total_matched": total_matched,
                "total_grouped": total_grouped,
                "distinct_keys": len(counts),
                "top_n": top_n,
                "groups": [{"key": key, "count": count} for key, count in top],
                "truncated": len(counts) > top_n,
            }
        except (EmailSearchError, ValueError):
            raise
        except Exception as e:
            logging.error("Email aggregation failed with %s", type(e).__name__)
            raise EmailSearchError(f"Failed to aggregate emails: {e!s}") from e
        finally:
            if mail:
                await self.close_imap_connection(mail)

    async def _fetch_group_keys(self, mail: imaplib.IMAP4_SSL, uid_batch: List[bytes], group_by: str) -> List[str]:
        """Fetch and extract the grouping key for one batch of UIDs."""
        if not uid_batch:
            return []
        message_set = b",".join(uid_batch).decode()
        if group_by == "date":
            data = await self._uid_command(mail, "FETCH", message_set, "(UID INTERNALDATE)")
            return [key for key in (self._internaldate_to_day(item) for item in data) if key]

        header_field = "FROM" if group_by == "sender" else "TO"
        data = await self._uid_command(mail, "FETCH", message_set, f"(UID BODY.PEEK[HEADER.FIELDS ({header_field})])")
        keys: List[str] = []
        for item in data:
            if not (isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray))):
                continue
            header_message = email.message_from_bytes(bytes(item[1]))
            raw = decode_email_header(header_message.get(header_field.title()), "").strip()
            if not raw:
                continue
            address = parseaddr(raw)[1].lower()
            keys.append(address or raw)
        return keys

    @staticmethod
    def _internaldate_to_day(item: object) -> str | None:
        """Extract the YYYY-MM-DD day from an INTERNALDATE FETCH response item."""
        raw = item if isinstance(item, (bytes, bytearray)) else (item[0] if isinstance(item, tuple) else None)
        if not isinstance(raw, (bytes, bytearray)):
            return None
        match = re.search(rb'INTERNALDATE "(\d{2}-[A-Za-z]{3}-\d{4})', bytes(raw))
        if not match:
            return None
        try:
            return datetime.strptime(match.group(1).decode("ascii"), "%d-%b-%Y").strftime("%Y-%m-%d")
        except ValueError:
            return None

    def _format_email_summary(self, msg_data: Tuple[Any, ...]) -> Dict[str, Any]:
        """Format an email message into a summary dict with basic information."""
        email_body = email.message_from_bytes(msg_data[0][1])
        descriptor = msg_data[0][0]
        if not isinstance(descriptor, bytes):
            raise EmailSearchError("Invalid UID FETCH response")
        uid_match = re.search(rb"\bUID (\d+)\b", descriptor)
        if not uid_match:
            raise EmailSearchError("UID missing from FETCH response")

        return {
            "id": uid_match.group(1).decode("ascii"),
            "from": decode_email_header(email_body.get("From"), "Unknown"),
            "date": normalize_email_date(email_body.get("Date", "Unknown")),
            "subject": decode_email_header(email_body.get("Subject"), "No Subject"),
            "gmail_msgid": self._extract_fetch_number(descriptor, "X-GM-MSGID"),
        }

    def _decode_payload(self, part: Any, payload: bytes) -> str:
        """Decode email payload with charset detection and fallback.

        Args:
            part: Email part containing charset information
            payload: Raw bytes to decode

        Returns:
            Decoded string, using charset from Content-Type or UTF-8 fallback
        """
        charset = part.get_content_charset() or "utf-8"
        try:
            return payload.decode(charset)
        except (UnicodeDecodeError, LookupError):
            # Fallback to UTF-8 with replacement characters for invalid bytes
            return payload.decode("utf-8", errors="replace")

    def _format_email_content(self, msg_data: Tuple[Any, ...]) -> Dict[str, Any]:
        """Format an email message into a dict with full content and attachment info."""
        email_body = email.message_from_bytes(msg_data[0][1])

        # Extract body content and attachment info
        body = ""
        html_body = ""
        attachments: List[Dict[str, Any]] = []
        attachment_index = 0

        if email_body.is_multipart():
            for part in email_body.walk():
                content_type = part.get_content_type()
                content_disposition = part.get("Content-Disposition")

                # Check if this is an attachment
                if content_disposition and "attachment" in content_disposition:
                    filename = decode_email_header(part.get_filename(), "unnamed")
                    payload = part.get_payload(decode=True)
                    size = len(payload) if payload else 0
                    attachments.append(
                        {
                            "index": attachment_index,
                            "filename": filename,
                            "content_type": content_type,
                            "size": size,
                        }
                    )
                    attachment_index += 1
                elif content_type == "text/plain":
                    payload = part.get_payload(decode=True)
                    if isinstance(payload, bytes) and not body:
                        body = self._decode_payload(part, payload)
                elif content_type == "text/html":
                    if not html_body:
                        payload = part.get_payload(decode=True)
                        if isinstance(payload, bytes):
                            html_body = self._decode_payload(part, payload)
        else:
            payload = email_body.get_payload(decode=True)
            if isinstance(payload, bytes):
                body = self._decode_payload(email_body, payload)

        if not body:
            body = html_body

        return {
            "from": decode_email_header(email_body.get("From"), "Unknown"),
            "to": decode_email_header(email_body.get("To"), "Unknown"),
            "date": normalize_email_date(email_body.get("Date", "Unknown")),
            "subject": decode_email_header(email_body.get("Subject"), "No Subject"),
            "content": body,
            "attachments": attachments,
            **self._build_backlink_fields(message_id=email_body.get("Message-ID")),
        }

    def _raise_no_email_data_error(self) -> None:
        """Raise error for no email data."""
        raise EmailSearchError("No email data returned")
