"""Pytest configuration for the test suite."""

import sys
import os

# Add the src directory to the Python path so tests can import from it
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))