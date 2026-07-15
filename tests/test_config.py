"""Tests for lazy, validated email configuration."""

from __future__ import annotations

import pytest

from email_client.config import EmailConfig, get_required_env


def test_get_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_REQUIRED", "value")
    assert get_required_env("TEST_REQUIRED") == "value"


def test_get_required_env_rejects_missing_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_REQUIRED", raising=False)
    with pytest.raises(ValueError, match="TEST_REQUIRED"):
        get_required_env("TEST_REQUIRED")


def test_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMAIL_ADDRESS", "person@example.com")
    monkeypatch.setenv("EMAIL_PASSWORD", "secret")
    for key in ("IMAP_SERVER", "IMAP_PORT", "SMTP_SERVER", "SMTP_PORT", "SMTP_SECURITY"):
        monkeypatch.delenv(key, raising=False)

    config = EmailConfig.from_env()

    assert config.imap_server == "imap.gmail.com"
    assert config.imap_port == 993
    assert config.smtp_port == 587
    assert config.smtp_security == "starttls"


def test_port_465_defaults_to_implicit_tls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMAIL_ADDRESS", "person@example.com")
    monkeypatch.setenv("EMAIL_PASSWORD", "secret")
    monkeypatch.setenv("SMTP_PORT", "465")
    monkeypatch.delenv("SMTP_SECURITY", raising=False)
    assert EmailConfig.from_env().smtp_security == "ssl"


@pytest.mark.parametrize("value", ["invalid", "0", "65536"])
def test_invalid_smtp_port(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("EMAIL_ADDRESS", "person@example.com")
    monkeypatch.setenv("EMAIL_PASSWORD", "secret")
    monkeypatch.setenv("SMTP_PORT", value)
    with pytest.raises(ValueError, match="SMTP_PORT"):
        EmailConfig.from_env()


def test_invalid_security_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMAIL_ADDRESS", "person@example.com")
    monkeypatch.setenv("EMAIL_PASSWORD", "secret")
    monkeypatch.setenv("SMTP_SECURITY", "plaintext")
    with pytest.raises(ValueError, match="SMTP_SECURITY"):
        EmailConfig.from_env()


def test_invalid_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMAIL_ADDRESS", "person@example.com")
    monkeypatch.setenv("EMAIL_PASSWORD", "secret")
    monkeypatch.setenv("EMAIL_CONNECTION_TIMEOUT", "0")
    with pytest.raises(ValueError, match="EMAIL_CONNECTION_TIMEOUT"):
        EmailConfig.from_env()
