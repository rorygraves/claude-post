import argparse
import asyncio

from . import server


def main() -> None:
    """Main entry point for the package."""
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="EmailClient MCP Server - Email management through Claude",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  email-client                           # Read-only mode (default)
  email-client --enable-write-operations # Enable move/delete operations

Security:
  By default, only read operations (search, read, list) are enabled.
  Use --enable-write-operations to enable move-email and delete-email tools.
        """
    )
    
    parser.add_argument(
        "--enable-write-operations",
        action="store_true",
        help="Enable write operations (move-email, delete-email). "
             "By default, only read operations are available for safety."
    )
    
    args = parser.parse_args()
    
    # Run the server with parsed arguments
    asyncio.run(server.main(enable_write_operations=args.enable_write_operations))

__all__ = ['main', 'server']
