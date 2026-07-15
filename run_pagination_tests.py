#!/usr/bin/env python3
"""Run the maintained pagination regression tests."""

from pathlib import Path

import pytest


def main() -> int:
    """Run pagination tests through pytest and return its exit status."""
    project_root = Path(__file__).resolve().parent
    test_files = (
        project_root / "tests" / "test_pagination.py",
        project_root / "tests" / "test_server_pagination.py",
    )
    return pytest.main(["-q", *(str(path) for path in test_files)])


if __name__ == "__main__":
    raise SystemExit(main())
