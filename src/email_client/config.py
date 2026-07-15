"""Validated, injectable configuration for email transports."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from dotenv import load_dotenv

load_dotenv()

SMTP_SECURITY_MODES = {"starttls", "ssl"}


def get_required_env(key: str) -> str:
    """Return a non-empty environment variable or raise an actionable error."""
    value = os.getenv(key)
    if value:
        return value
    raise ValueError(
        f"Missing required environment variable {key}. "
        "Create a .env file in the project root and configure your email account. "
        "Gmail users should enable 2-Step Verification and use an App Password. "
        f"Example: {key}=your_value_here"
    )


def _get_port(key: str, default: int) -> int:
    raw_value = os.getenv(key, str(default))
    try:
        port = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{key} must be a valid integer, got: {raw_value}") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"{key} must be between 1 and 65535, got {port}")
    return port


def _get_positive_float(key: str, default: float) -> float:
    raw_value = os.getenv(key, str(default))
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ValueError(f"{key} must be a valid number, got: {raw_value}") from exc
    if value <= 0:
        raise ValueError(f"{key} must be positive, got {value}")
    return value


@dataclass(frozen=True, slots=True)
class EmailConfig:
    """Complete runtime configuration for IMAP and SMTP connections."""

    email_address: str
    email_password: str
    imap_server: str = "imap.gmail.com"
    imap_port: int = 993
    smtp_server: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_security: Literal["starttls", "ssl"] = "starttls"
    connection_timeout: float = 30.0

    @classmethod
    def from_env(cls) -> EmailConfig:
        smtp_port = _get_port("SMTP_PORT", 587)
        security_default = "ssl" if smtp_port == 465 else "starttls"
        smtp_security = os.getenv("SMTP_SECURITY", security_default).lower()
        if smtp_security not in SMTP_SECURITY_MODES:
            allowed = ", ".join(sorted(SMTP_SECURITY_MODES))
            raise ValueError(f"SMTP_SECURITY must be one of: {allowed}")
        return cls(
            email_address=get_required_env("EMAIL_ADDRESS"),
            email_password=get_required_env("EMAIL_PASSWORD"),
            imap_server=os.getenv("IMAP_SERVER", "imap.gmail.com"),
            imap_port=_get_port("IMAP_PORT", 993),
            smtp_server=os.getenv("SMTP_SERVER", "smtp.gmail.com"),
            smtp_port=smtp_port,
            smtp_security=smtp_security,  # type: ignore[arg-type]
            connection_timeout=_get_positive_float("EMAIL_CONNECTION_TIMEOUT", 30.0),
        )


def load_email_config() -> EmailConfig:
    """Load and validate email configuration at the point it is needed."""
    return EmailConfig.from_env()
