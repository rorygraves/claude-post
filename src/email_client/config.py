"""Validated, injectable configuration for email transports."""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv

load_dotenv()

SMTP_SECURITY_MODES = {"starttls", "ssl"}

DEFAULT_ACCOUNTS_FILE = "accounts.toml"
DEFAULT_ACCOUNT_ALIAS = "default"
_ALIAS_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_ENV_REF_PATTERN = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


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


def _resolve_secret(value: Any, *, field: str, alias: str) -> str:
    """Return a string field, resolving a ``${ENV_VAR}`` reference if present."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"Account '{alias}' is missing required field '{field}'")
    match = _ENV_REF_PATTERN.match(value)
    if match is None:
        return value
    env_name = match.group(1)
    resolved = os.getenv(env_name)
    if not resolved:
        raise ValueError(
            f"Account '{alias}' field '{field}' references environment variable "
            f"{env_name}, which is not set. Define it in your environment or .env file."
        )
    return resolved


def _account_from_table(alias: str, table: Mapping[str, Any]) -> EmailConfig:
    """Build an EmailConfig from one ``[accounts.<alias>]`` TOML table."""
    if not isinstance(table, Mapping):
        # ValueError (not TypeError) keeps all config-validation failures one type.
        raise ValueError(f"Account '{alias}' must be a table of settings")  # noqa: TRY004
    smtp_port = int(table.get("smtp_port", 587))
    if not 1 <= smtp_port <= 65535:
        raise ValueError(f"Account '{alias}' smtp_port must be between 1 and 65535, got {smtp_port}")
    imap_port = int(table.get("imap_port", 993))
    if not 1 <= imap_port <= 65535:
        raise ValueError(f"Account '{alias}' imap_port must be between 1 and 65535, got {imap_port}")
    security_default = "ssl" if smtp_port == 465 else "starttls"
    smtp_security = str(table.get("smtp_security", security_default)).lower()
    if smtp_security not in SMTP_SECURITY_MODES:
        allowed = ", ".join(sorted(SMTP_SECURITY_MODES))
        raise ValueError(f"Account '{alias}' smtp_security must be one of: {allowed}")
    timeout = float(table.get("connection_timeout", 30.0))
    if timeout <= 0:
        raise ValueError(f"Account '{alias}' connection_timeout must be positive, got {timeout}")
    return EmailConfig(
        email_address=_resolve_secret(table.get("email_address"), field="email_address", alias=alias),
        email_password=_resolve_secret(table.get("password"), field="password", alias=alias),
        imap_server=str(table.get("imap_server", "imap.gmail.com")),
        imap_port=imap_port,
        smtp_server=str(table.get("smtp_server", "smtp.gmail.com")),
        smtp_port=smtp_port,
        smtp_security=smtp_security,  # type: ignore[arg-type]
        connection_timeout=timeout,
    )


@dataclass(frozen=True, slots=True)
class AccountsConfig:
    """A validated set of named email accounts with one designated primary."""

    accounts: dict[str, EmailConfig]
    primary_alias: str

    @property
    def primary(self) -> EmailConfig:
        return self.accounts[self.primary_alias]

    def get(self, alias: str | None) -> EmailConfig:
        """Return the account for ``alias``, or the primary when ``alias`` is None."""
        resolved = alias or self.primary_alias
        if resolved not in self.accounts:
            available = ", ".join(sorted(self.accounts))
            raise ValueError(f"Unknown account '{resolved}'. Configured accounts: {available}")
        return self.accounts[resolved]


def _accounts_file_path() -> Path:
    return Path(os.getenv("ACCOUNTS_FILE", DEFAULT_ACCOUNTS_FILE))


def load_accounts_config() -> AccountsConfig:
    """Load every configured account.

    Reads ``accounts.toml`` (path overridable via ``ACCOUNTS_FILE``) when present;
    otherwise falls back to the single-account ``EMAIL_*`` environment configuration
    aliased ``default``, so existing single-mailbox setups keep working unchanged.
    """
    path = _accounts_file_path()
    if not path.is_file():
        return AccountsConfig(
            accounts={DEFAULT_ACCOUNT_ALIAS: load_email_config()}, primary_alias=DEFAULT_ACCOUNT_ALIAS
        )

    with path.open("rb") as handle:
        document = tomllib.load(handle)
    raw_accounts = document.get("accounts")
    if not isinstance(raw_accounts, Mapping) or not raw_accounts:
        raise ValueError(f"{path} must define at least one [accounts.<alias>] table")

    accounts: dict[str, EmailConfig] = {}
    primary_alias: str | None = None
    for alias, table in raw_accounts.items():
        if not _ALIAS_PATTERN.match(alias):
            raise ValueError(f"Invalid account alias '{alias}'. Use lowercase letters, digits, hyphen, or underscore.")
        accounts[alias] = _account_from_table(alias, table)
        if isinstance(table, Mapping) and table.get("primary") is True:
            if primary_alias is not None:
                raise ValueError(f"Multiple accounts marked primary: '{primary_alias}' and '{alias}'")
            primary_alias = alias

    if primary_alias is None:
        primary_alias = next(iter(accounts))
    return AccountsConfig(accounts=accounts, primary_alias=primary_alias)
