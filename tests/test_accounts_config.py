"""Tests for multi-account configuration loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from email_client.config import DEFAULT_ACCOUNT_ALIAS, load_accounts_config

WORK = """
[accounts.work]
email_address = "work@example.com"
password = "work-secret"
primary = true

[accounts.personal]
email_address = "me@gmail.com"
password = "${PERSONAL_PW}"
imap_server = "imap.example.net"
smtp_port = 465
"""


def _write_accounts(tmp_path: Path, monkeypatch, body: str) -> Path:
    path = tmp_path / "accounts.toml"
    path.write_text(body)
    monkeypatch.setenv("ACCOUNTS_FILE", str(path))
    return path


def test_falls_back_to_env_single_account(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ACCOUNTS_FILE", str(tmp_path / "missing.toml"))
    monkeypatch.setenv("EMAIL_ADDRESS", "solo@example.com")
    monkeypatch.setenv("EMAIL_PASSWORD", "pw")
    config = load_accounts_config()
    assert config.primary_alias == DEFAULT_ACCOUNT_ALIAS
    assert config.primary.email_address == "solo@example.com"


def test_loads_named_accounts_and_expands_env(tmp_path, monkeypatch) -> None:
    _write_accounts(tmp_path, monkeypatch, WORK)
    monkeypatch.setenv("PERSONAL_PW", "resolved-secret")
    config = load_accounts_config()

    assert set(config.accounts) == {"work", "personal"}
    assert config.primary_alias == "work"  # explicit primary = true wins
    assert config.get("personal").email_password == "resolved-secret"
    assert config.get("personal").imap_server == "imap.example.net"
    assert config.get("personal").smtp_security == "ssl"  # derived from port 465


def test_missing_env_reference_is_actionable(tmp_path, monkeypatch) -> None:
    _write_accounts(tmp_path, monkeypatch, WORK)
    monkeypatch.delenv("PERSONAL_PW", raising=False)
    with pytest.raises(ValueError, match="PERSONAL_PW"):
        load_accounts_config()


def test_unknown_alias_lists_configured_accounts(tmp_path, monkeypatch) -> None:
    _write_accounts(tmp_path, monkeypatch, WORK)
    monkeypatch.setenv("PERSONAL_PW", "x")
    config = load_accounts_config()
    with pytest.raises(ValueError, match=r"personal, work"):
        config.get("nope")


def test_primary_defaults_to_first_when_unmarked(tmp_path, monkeypatch) -> None:
    body = """
[accounts.one]
email_address = "one@example.com"
password = "a"

[accounts.two]
email_address = "two@example.com"
password = "b"
"""
    _write_accounts(tmp_path, monkeypatch, body)
    config = load_accounts_config()
    assert config.primary_alias == "one"


def test_multiple_primaries_rejected(tmp_path, monkeypatch) -> None:
    body = """
[accounts.one]
email_address = "one@example.com"
password = "a"
primary = true

[accounts.two]
email_address = "two@example.com"
password = "b"
primary = true
"""
    _write_accounts(tmp_path, monkeypatch, body)
    with pytest.raises(ValueError, match="Multiple accounts marked primary"):
        load_accounts_config()


def test_invalid_alias_rejected(tmp_path, monkeypatch) -> None:
    body = """
[accounts."Bad Alias"]
email_address = "x@example.com"
password = "a"
"""
    _write_accounts(tmp_path, monkeypatch, body)
    with pytest.raises(ValueError, match="Invalid account alias"):
        load_accounts_config()
