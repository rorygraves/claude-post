"""Configuration module for email client.

This module handles loading and providing configuration values from
environment variables for the email client operations.
"""

import os

from dotenv import load_dotenv

# Load environment variables from .env file for configuration
# This allows secure credential storage outside the codebase
load_dotenv()

# Email Configuration - Global Constants
# Extract and validate configuration once at startup for efficiency
# These values are used throughout the application for email operations
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS", "your.email@gmail.com")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "your-app-specific-password")
IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.gmail.com")
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))  # SMTP with STARTTLS
