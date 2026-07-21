# ClaudePost

A Model Context Protocol (MCP) server that provides a seamless email management interface through Claude. This integration allows you to handle emails directly through natural language conversations with Claude, supporting features like searching, reading, and sending emails securely.

## Features & Demo

### Email Search and Reading

<p align="center">
  <img src="assets/gif1.gif" width="800"/>
</p>

- 📧 Search emails by date range and keywords
- 📅 View daily email statistics
- 📝 Read full email content with threading support

### Email Composition and Sending

<p align="center">
  <img src="assets/gif2.gif" width="800"/>
</p>

- ✉️ Send emails with CC recipients support
- 🔒 Secure email handling with TLS

## Prerequisites

- Python 3.14 or higher
- A Gmail account (or other email provider)
- If using Gmail:
  - Two-factor authentication enabled
  - [App-specific password](https://support.google.com/mail/answer/185833?hl=en) generated
- Claude Desktop application

## Setup

1. Install uv:

   ```bash
   # MacOS/Linux
   curl -LsSf https://astral.sh/uv/install.sh | sh

   # Remember to restart your terminal after installation
   ```

2. Clone and set up the project:

   ```bash
   # Clone the repository
   git clone https://github.com/ZilongXue/claude-post.git
   cd claude-post

   # Create the environment and install runtime + development dependencies
   uv sync --extra dev
   ```

3. Create a `.env` file in the project root:

   ```env
   EMAIL_ADDRESS=your.email@gmail.com
   EMAIL_PASSWORD=your-app-specific-password
   IMAP_SERVER=imap.gmail.com
   SMTP_SERVER=smtp.gmail.com
   SMTP_PORT=587
   SMTP_SECURITY=starttls
   IMAP_PORT=993
   EMAIL_CONNECTION_TIMEOUT=30
   EMAIL_CLIENT_LOG_LEVEL=INFO
   ```

4. Configure Claude Desktop:

   First, make sure you have Claude for Desktop installed. You can install the latest version [here](https://claude.ai/download). If you already have Claude for Desktop, make sure it's updated to the latest version.

   Open your Claude Desktop configuration file:

   ```bash
   # MacOS
   ~/Library/Application Support/Claude/claude_desktop_config.json

   # Create the file if it doesn't exist
   mkdir -p ~/Library/Application\ Support/Claude
   touch ~/Library/Application\ Support/Claude/claude_desktop_config.json
   ```

   Add the following configuration:

   **Read-Only Mode (Recommended for safety):**
   ```json
   {
     "mcpServers": {
       "email": {
         "command": "/Users/username/.local/bin/uv",
         "args": [
           "--directory",
           "/path/to/claude-post",
           "run",
           "email-client"
         ]
       }
     }
   }
   ```

   **Explicit side-effect capabilities:**
   ```json
   {
     "mcpServers": {
       "email": {
         "command": "/Users/username/.local/bin/uv",
         "args": [
           "--directory",
           "/path/to/claude-post",
           "run",
           "email-client",
           "--enable-write-operations",
           "--enable-send-operations",
           "--enable-file-operations"
         ]
       }
     }
   }
   ```

   Replace `/Users/username` and `/path/to/claude-post` with your actual paths.

   After updating the configuration, restart Claude Desktop for the changes to take effect.

## Running the Server

The server runs automatically through Claude Desktop:

- The server will start when Claude launches if configured correctly
- No manual server management needed
- Server stops when Claude is closed

## Usage Through Claude

You can interact with your emails using natural language commands. Here are some examples:

### Search Emails

- "Show me emails from last week"
- "Find emails with subject containing 'meeting'"
- "Search for emails from recruiting@linkedin.com between 2024-01-01 and 2024-01-07"
- "Search sent emails from last month"

### Read Email Content

- "Show me the content of email #12345"
- "What's the full message of the last email from HR?"

### Email Statistics

- "How many emails did I receive today?"
- "Show me daily email counts for the past week"

### Send Emails

- "I want to send an email to john@example.com"
- "Send a meeting confirmation to team@company.com"

Sending is disabled by default. Enable it only with `--enable-send-operations`, and have the calling client obtain confirmation before invoking the tool.

## Command Line Options and Security

For safety, the EmailClient MCP server exposes only mailbox-read and in-memory collection operations by default. Email sending, filesystem writes, and mailbox mutation are independent opt-in capabilities.

### Command Line Options

```bash
# Show help and available options
uv run email-client --help

# List all available tools with descriptions and parameters
uv run email-client --describe

# Start server in read-only mode (default)
uv run email-client

# Enable mailbox move/delete operations
uv run email-client --enable-write-operations

# Independently enable sending or filesystem export/download
uv run email-client --enable-send-operations
uv run email-client --enable-file-operations
```

### Read-Only Mode (Default)
```bash
uv run email-client
```
Available tools: `mail-search`, `mail-get-content`, `mail-count-daily`, `mail-folders`, `mail-fetch`, `mail-list`, `mail-preview`, `mail-transform`, `mail-combine`

### Write Operations Mode
```bash
uv run email-client --enable-write-operations
```
Additional tools: `mail-move`, `mail-delete`

`--enable-send-operations` adds `mail-send`. `--enable-file-operations` adds `mail-download-attachment` and `mail-export`.

### Security Features
- **Default safety**: Write operations disabled by default
- **Explicit enablement**: Requires `--enable-write-operations` flag
- **Tool visibility**: Destructive tools only appear when explicitly enabled
- **Clear messaging**: Attempts to use disabled tools show helpful error messages

### Available MCP Tools

All tools are prefixed with `mail-` for easy identification. Use `uv run email-client --describe` to see the full list with detailed parameter descriptions.

#### Core Tools (Always Available)
- `mail-search`: Search emails and create data collections
- `mail-get-content`: Get full content of a specific email
- `mail-count-daily`: Count emails received for each day in a date range
- `mail-folders`: List all available email folders
- `mail-fetch`: Retrieve email data from a collection
- `mail-list`: List all email data collections
- `mail-preview`: Preview collection structure and sample records
- `mail-transform`: Apply declarative, allowlisted collection transformations
- `mail-combine`: Combine two email collections

### Stable Gmail Backlinks

Single and bulk `mail-get-content` responses include these additive fields:

- `message_id`: RFC-822 `Message-ID`, including angle brackets, or `null`
- `gmail_msgid`: Gmail's stable message identifier as a decimal string, or `null`
- `gmail_thrid`: Gmail's stable thread identifier as a decimal string, or `null`
- `gmail_url`: an account-pinned Gmail permalink, with an RFC-822 search URL as the non-Gmail fallback

Gmail identifiers remain stable when a message moves between folders, unlike the folder-scoped IMAP UID exposed as `id`. The configured `EMAIL_ADDRESS` is used as the permalink's `authuser`. On Gmail, `mail-search` collections also include `gmail_msgid`, allowing backlinks to be built without fetching every message body. Metadata fetches use `BODY.PEEK` and do not mark messages as read.

`mail-transform` does not execute Python. It accepts one operation name and a `parameters` object. Supported operations are `select_columns`, `drop_columns`, `rename_columns`, `sort`, `filter`, `head`, `tail`, `drop_duplicates`, `convert_datetime`, and `group_count`.

Example parameters:

```json
{
  "collection_id": "collection-id",
  "operation": "filter",
  "parameters": {
    "column": "sender",
    "operator": "contains",
    "value": "@example.com",
    "case_sensitive": false
  }
}
```

#### Write Operation Tools (Requires `--enable-write-operations`)

##### `mail-move`
- **Description**: Move one or more emails from one folder to another
- **Parameters**: 
  - `email_ids` (required): Array of email IDs to move
  - `destination_folder` (required): Target folder
  - `source_folder` (optional): Source folder (defaults to 'inbox')

##### `mail-delete`
- **Description**: Delete one or more emails (move to trash by default, or permanently)
- **Parameters**:
  - `email_ids` (required): Array of email IDs to delete
  - `folder` (optional): Source folder (defaults to 'inbox')
  - `permanent` (optional): If true, permanently delete; if false, move to trash

## Testing

Run the isolated unit suite and static checks with:

```bash
uv run pytest -q
uv run ruff check .
uv run mypy src
```

The project includes an integration test suite that validates EmailClient functionality against real email servers. These tests are designed for manual execution, not continuous integration.

### Running Integration Tests

```bash
# Make sure your .env file is configured with valid email credentials
uv run python run_integration_tests.py
```

### What the Tests Do

The integration test suite performs the following validations:

1. **Send Email Test**: Sends a test email with rich content to your configured email address
2. **Search Emails Test**: Searches for emails using date ranges and keywords
3. **Email Content Test**: Retrieves and validates full email content (10+ validation checks)
4. **Daily Count Test**: Counts emails received on specific dates
5. **List Folders Test**: Discovers all available email folders
6. **Move Email Test**: Tests moving emails between folders
7. **Delete Functionality Test**: Validates delete email capabilities
8. **Delete Multiple Emails Test**: Validates array-based delete functionality
9. **Move Multiple Emails Test**: Validates array-based move functionality
10. **Sent Folder Test**: Validates sent emails folder functionality
11. **Move to Trash Test**: Moves test email to trash folder (safe cleanup)
12. **Permanent Delete Test**: Tests permanent deletion functionality

### Test Features

- 🏷️ All test emails include `[TEST-EMAIL]` prefix for easy identification
- 🔍 Unique timestamp IDs prevent conflicts between test runs
- 📊 Clear pass/fail reporting with detailed output
- 🧹 Test emails are moved to trash if tests pass (can be restored)
- 📝 Comprehensive logging for debugging
- ✅ 10+ content validation checks (special characters, Unicode, formatting)

### Requirements for Testing

- Valid `.env` file with working email credentials
- Network access to your email servers (IMAP/SMTP)
- Email account that can send emails to itself
- Gmail users need app-specific passwords enabled

### Example Test Output

```
📧 EmailClient Integration Test Suite
=====================================
🔄 Testing: Send email to self...
✅ PASS: Send email to self
🔄 Testing: Search emails for today...
✅ PASS: Search today's emails
    Found 15 emails for 2024-01-15
✅ PASS: Get email content
    All 10 validation checks passed
✅ PASS: List folders
    Found 12 folders including inbox
✅ PASS: Move email
    Email successfully moved to [Gmail]/Drafts (no longer in inbox)
✅ PASS: Delete email functionality
    Delete email method available and properly configured
✅ PASS: Move email to trash
    Email successfully moved to trash (no longer in inbox)
✅ PASS: Permanent delete email
    Email was already moved to trash (expected)
...
Results: 13/13 tests passed
🎉 ALL TESTS PASSED!
```

## Project Structure

```
claude-post/
├── pyproject.toml
├── README.md
├── LICENSE
├── .env                    # Not included in repo
├── run_integration_tests.py # Test runner script
├── src/
│   └── email_client/
│       ├── __init__.py
│       ├── config.py       # Configuration management
│       ├── email_client.py # Email operations
│       └── server.py       # MCP server implementation
└── tests/
    ├── __init__.py
    └── test_email_integration.py # Integration test suite
```

## Security Notes

- Use app-specific passwords instead of your main account password
- For Gmail users:
  1. Enable 2-Step Verification in your Google Account
  2. Generate an App Password for this application
  3. Use the App Password in your `.env` file

## Logging

The application writes operational logs to stderr, which keeps stdout available for the MCP protocol. Tool arguments, email bodies, recipients, and authentication exchanges are not logged.

## License

This project is licensed under the MIT License - see the LICENSE file for details.
