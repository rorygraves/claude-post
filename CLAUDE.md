# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ClaudePost is an MCP (Model Context Protocol) server that provides email management capabilities through Claude Desktop. The project has two major architectural components:

1. **Email Client** - IMAP/SMTP email operations and data processing with pandas-based collections
2. **MCP Framework** - Annotation-based framework for building MCP servers using Python decorators

## Development Commands

### Environment Setup
```bash
# Install dependencies (creates venv automatically)
uv pip install -e .

# Install dev dependencies
uv pip install -e ".[dev]"
```

### Code Quality
```bash
# Format code (line length: 120)
black .

# Lint code
ruff check .

# Type checking
mypy src/
```

### Testing
```bash
# Run integration tests (requires .env with valid email credentials)
uv run python run_integration_tests.py

# Run unit tests
uv run python -m pytest tests/test_calculator_server.py
uv run python -m pytest tests/test_pagination.py
uv run python -m pytest tests/test_mcp_data_processing.py
uv run python -m pytest tests/test_mcp_email_endpoints.py

# Run security tests
uv run python -m pytest tests/test_datastore_security.py
uv run python -m pytest tests/test_config.py
```

### Running the Email Server
```bash
# Show available tools and parameters
uv run email-client --describe

# Run in read-only mode (default, safe)
uv run email-client

# Run with write operations enabled (move/delete)
uv run email-client --enable-write-operations
```

## Architecture

### Email MCP Server (`server.py`)

The production email server uses the annotation-based MCP framework. Key architectural concepts:

1. **Tool Prefix Pattern**: All tools are prefixed with `mail-` (e.g., `mail-search`, `mail-send`)
2. **Write Operations Safety**: Move/delete operations are disabled by default and require explicit `--enable-write-operations` flag
3. **DataStore Integration**: Search results create pandas-based collections (returns metadata only, not raw data)
4. **Decorator-Based**: Uses `@mcp_tool` decorator for automatic tool registration and schema generation

### Data Processing Architecture (`data_processing/datastore.py`)

The DataStore provides a unique data reference pattern with security-validated operations:

- **Collections**: Search results stored as pandas DataFrames with unique IDs
- **Metadata-First**: Operations return collection metadata (shape, columns, dtypes), not raw email data
- **Transform Operations**: Supports pandas operations via `update()` for filtering/grouping/analysis
  - **Security**: Operations validated with AST parsing before execution
  - Blocks file access, imports, system commands, and dangerous builtins
  - Only allows safe DataFrame/pandas/numpy operations
- **Fetch Pattern**: Clients explicitly fetch data from collections with limit/format controls
- **Combine Pattern**: Collections can be merged using `combine()` method

Example flow:
1. `mail-search` creates a collection → returns metadata with collection ID
2. `mail-preview` or `mail-fetch` retrieves actual email data using collection ID
3. `mail-update` applies pandas transformations (validated for security) → returns updated metadata
4. `mail-combine` merges two collections → returns combined metadata

### MCP Framework (`mcp_framework/`)

Reusable annotation-based framework for building MCP servers:

- **BaseMCPServer**: Base class with automatic tool discovery from `@mcp_tool` decorated methods
- **Schema Generation**: Automatic JSON schema extraction from Python type hints and docstrings
- **Command Line Integration**: Built-in `--describe` flag and extensible argument parsing
- **Tool Prefix Support**: Optional prefix for all tool names (e.g., `mail-` prefix)

Key files:
- `base.py`: BaseMCPServer core implementation
- `decorators.py`: `@mcp_tool` decorator definition
- `schema_generator.py`: Type hint to JSON schema conversion

### Email Client (`email_client.py`)

Core IMAP/SMTP operations with security hardening:
- Async operations using asyncio
- Connection pooling pattern (connects per operation)
- Custom exceptions: EmailConnectionError, EmailSearchError, EmailSendError, EmailDeletionError, EmailAttachmentError
- SearchCriteria dataclass for type-safe search parameters
- **Security**: IMAP string escaping prevents injection attacks in search queries
- Input validation: Source/destination folder checking for move operations
- Support for both inbox and sent folder operations

**IMAP UIDs are folder-scoped.** A message's UID in `INBOX` differs from its UID in `[Gmail]/Bin`, and moving a message changes its UID. `mail-move` and `mail-delete` therefore resolve the requested UIDs against a `UID SEARCH` in the target folder (`_filter_existing_uids`) and act only on those that actually exist. They return a `MailboxOperationResult` (`affected` vs `not_found`) so the tool reports what the server actually did, not the input — and raise if *none* of the requested UIDs exist rather than silently succeeding. After any move, re-search the destination folder to get current UIDs before operating on them again.

### Attachment Downloads

The `mail-download-attachment` tool saves files to disk instead of returning base64 data to context:
- **Required `output_dir` parameter**: User must specify absolute path to save directory
- **File naming**: Uses sanitized original filename with collision handling (`report.pdf` → `report_1.pdf`)
- **Security validations**:
  - Path traversal prevention via `os.path.realpath()` checks
  - Filename sanitization removes dangerous characters
  - Directory validation ensures path exists and is writable
  - 25MB size limit enforced
- **Returns metadata only**: `{filename, saved_as, filepath, content_type, size, email_id}`

### Email Export

The `mail-export` tool exports emails from a collection directly to markdown files on disk, keeping content out of LLM context:

**Context-efficient workflow:**
```
mail-search → mail-export → Done (files on disk, minimal context used)
```

**Features:**
- **Collection-based**: Takes a `collection_id` from `mail-search`, exports all emails in the collection
- **Markdown format**: Each email becomes a `.md` file with YAML frontmatter (from, to, date, subject)
- **File naming**: `YYYYMMDD-HHMM-sanitized_subject.md` with collision handling
- **Automatic attachments**: Downloads attachments alongside markdown files (configurable)
- **Returns metadata only**: `{emails_exported, files_created, attachments_downloaded, errors}`

**Example usage:**
```
# Step 1: Search for emails
mail-search(sender="@company.com", collection_name="company_emails")
→ Returns: {id: "abc123", shape: {rows: 50, columns: 4}, ...}

# Step 2: Export to files
mail-export(collection_id="abc123", output_dir="/Users/name/Downloads/emails")
→ Returns: {emails_exported: 50, files_created: [...], attachments_downloaded: [...]}
```

**Security**: Uses the same validated helper functions as attachment downloads (path traversal prevention, filename sanitization, directory validation).

## Configuration

### Environment Variables (.env)

**Required** (will fail with clear error if missing):
```
EMAIL_ADDRESS=your.email@gmail.com
EMAIL_PASSWORD=your-app-specific-password
```

**Optional** (defaults provided for Gmail):
```
IMAP_SERVER=imap.gmail.com  # default
SMTP_SERVER=smtp.gmail.com  # default
SMTP_PORT=587               # default
```

The configuration validates at startup and provides helpful error messages if required credentials are missing, including instructions for setting up Gmail app passwords.

### Multiple Accounts (`accounts.toml`)

ClaudePost can serve several mailboxes from one server. To enable this, create an `accounts.toml` (path overridable via the `ACCOUNTS_FILE` env var); see `accounts.toml.example`. Each `[accounts.<alias>]` table is one account, and passwords may reference an environment variable as `${VAR_NAME}`:

```toml
[accounts.work]
email_address = "you@company.com"
password = "${WORK_APP_PASSWORD}"
primary = true

[accounts.personal]
email_address = "you@gmail.com"
password = "${PERSONAL_APP_PASSWORD}"
```

- **Backward compatible**: with no `accounts.toml`, the single `EMAIL_*` env config is used, aliased `default`. Nothing changes for existing setups.
- **Per-tool targeting**: every mailbox tool accepts an optional `account` alias (e.g. `mail-search(account="personal")`). Omitting it uses the account marked `primary` (or the first defined). Use `mail-accounts` to list configured accounts.
- **Lazy credential loading**: accounts load on first mailbox use, so `--describe` still works with no credentials.
- **Provenance**: search collections record the `account` they came from; `mail-export` automatically exports from that account.
- **Config loading**: `load_accounts_config()` in `config.py` builds an `AccountsConfig` (alias → `EmailConfig` + primary); the server keeps a lazily-built `alias → EmailClient` registry and routes each call via `_client_for(account)`.

### Claude Desktop Configuration
The server is designed to run through Claude Desktop's MCP server configuration:
- Path: `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS)
- Uses `uv run` command with `--directory` pointing to `src/email_client`
- Entry point: `email-client` script (defined in pyproject.toml)

## Testing Strategy

### Integration Tests (`tests/test_email_integration.py`, `run_integration_tests.py`)
- Manual execution only (not CI/CD)
- Requires live email server credentials
- Tests real IMAP/SMTP operations
- Uses `[TEST-EMAIL]` prefix for easy identification
- Includes cleanup (moves test emails to trash)

### Unit Tests
- Focused on MCP server logic, pagination, data processing
- Located in `tests/` directory
- Uses pytest framework

## Important Implementation Notes

1. **Server Implementation**: The project uses a single production server (`server.py`) built on the annotation-based MCP framework. The older `annotated_server.py` has been removed.

2. **Tool Naming Convention**: When adding new tools, use the `@mcp_tool(name="...")` decorator with kebab-case names (e.g., `"get-content"` not `"get_content"`).

3. **DataStore Operations**:
   - The `update()` method executes pandas code using `exec()` with **security validation**
   - All operations are validated with AST parsing before execution
   - Blocks file access, imports, system commands, and dangerous operations
   - Operations must return a DataFrame and use `df` to reference the current data
   - See `validate_operation_safety()` in `datastore.py` for security implementation

4. **Write Operations Guard**: Always check `self.write_operations_enabled` before executing move/delete operations.

5. **Input Validation**:
   - IMAP search strings are escaped using `escape_imap_string()` to prevent injection
   - Folder operations validate source != destination
   - Type validation uses cast() after runtime checks for Literal types

6. **Logging**: All operations log to `email_client.log`. Use the logging module, not print statements.

7. **Type Hints**: Project uses strict mypy with `strict = true`. All functions should have complete type hints. Use `cast()` after validation rather than `# type: ignore`.

8. **Line Length**: Black configured for 120 character line length (not default 88).

9. **Security Tests**: Comprehensive security tests in `tests/test_datastore_security.py` verify that dangerous operations are blocked while legitimate pandas operations work correctly.

## Project Structure
```
claude-post/
├── src/
│   ├── email_client/          # Email MCP server
│   │   ├── server.py          # Production server (framework-based)
│   │   ├── email_client.py    # IMAP/SMTP operations
│   │   ├── config.py          # Environment configuration
│   │   └── data_processing/   # DataStore and pandas operations
│   └── mcp_framework/         # Reusable MCP framework
│       ├── base.py            # BaseMCPServer
│       ├── decorators.py      # @mcp_tool decorator
│       └── schema_generator.py
├── tests/                     # Unit and integration tests
└── run_integration_tests.py   # Integration test runner
```