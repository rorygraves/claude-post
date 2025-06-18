from . import server


def main() -> None:
    """Main entry point for the package."""
    server.main()

__all__ = ['main', 'server']
