"""Transport-level regressions for TLS, UIDs, and targeted deletion."""

from __future__ import annotations

import asyncio
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from threading import Event
from unittest.mock import MagicMock, patch

import pytest

from email_client.config import EmailConfig
from email_client.email_client import (
    EmailClient,
    EmailConnectionError,
    EmailDeletionError,
    _run_blocking,
    escape_imap_string,
)


def _config(*, smtp_security: str = "starttls") -> EmailConfig:
    return EmailConfig(
        email_address="person@example.com",
        email_password="secret",
        smtp_security=smtp_security,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_imap_uses_verified_context_and_timeout() -> None:
    client = EmailClient(_config())
    context = MagicMock()
    mail = MagicMock()
    with (
        patch("email_client.email_client.ssl.create_default_context", return_value=context),
        patch("email_client.email_client.imaplib.IMAP4_SSL", return_value=mail) as imap,
    ):
        assert await client.connect_imap() is mail
    imap.assert_called_once_with(
        "imap.gmail.com",
        993,
        ssl_context=context,
        timeout=30.0,
    )
    mail.login.assert_called_once_with("person@example.com", "secret")


@pytest.mark.asyncio
async def test_close_uses_unselect_and_never_close() -> None:
    client = EmailClient(_config())
    mail = MagicMock(state="SELECTED")
    await client.close_imap_connection(mail)
    mail.unselect.assert_called_once_with()
    mail.close.assert_not_called()
    mail.logout.assert_called_once_with()


@pytest.mark.asyncio
async def test_starttls_uses_verified_context_without_debug_logging() -> None:
    client = EmailClient(_config())
    context = MagicMock()
    smtp = MagicMock()
    smtp.__enter__.return_value = smtp
    smtp.send_message.return_value = {}
    with (
        patch("email_client.email_client.ssl.create_default_context", return_value=context),
        patch("email_client.email_client.smtplib.SMTP", return_value=smtp) as smtp_class,
    ):
        await client._send_via_smtp(MIMEMultipart(), ["to@example.com"], None)
    smtp_class.assert_called_once_with("smtp.gmail.com", 587, timeout=30.0)
    smtp.starttls.assert_called_once_with(context=context)
    smtp.set_debuglevel.assert_not_called()


@pytest.mark.asyncio
async def test_implicit_tls_uses_smtp_ssl() -> None:
    client = EmailClient(_config(smtp_security="ssl"))
    smtp = MagicMock()
    smtp.__enter__.return_value = smtp
    smtp.send_message.return_value = {}
    with patch("email_client.email_client.smtplib.SMTP_SSL", return_value=smtp) as smtp_ssl:
        await client._send_via_smtp(MIMEMultipart(), ["to@example.com"], None)
    assert smtp_ssl.call_args.kwargs["context"] is not None
    smtp.starttls.assert_not_called()


@pytest.mark.asyncio
async def test_move_prefers_uid_move() -> None:
    client = EmailClient(_config())
    mail = MagicMock(capabilities=(b"IMAP4REV1",))
    mail.capability.return_value = ("OK", [b"IMAP4REV1 UIDPLUS MOVE"])
    mail.uid.return_value = ("OK", [b""])
    await client._move_uids(mail, ["10", "11"], '"Archive"')
    mail.capability.assert_called_once_with()
    mail.uid.assert_called_once_with("MOVE", "10,11", '"Archive"')
    mail.expunge.assert_not_called()


@pytest.mark.asyncio
async def test_uidplus_fallback_uses_targeted_uid_expunge() -> None:
    client = EmailClient(_config())
    mail = MagicMock(capabilities=(b"IMAP4REV1",))
    mail.capability.return_value = ("OK", [b"IMAP4REV1 UIDPLUS"])
    mail.uid.return_value = ("OK", [b""])
    await client._move_uids(mail, ["10"], '"Archive"')
    assert [call.args[0] for call in mail.uid.call_args_list] == ["COPY", "STORE", "EXPUNGE"]
    assert mail.uid.call_args_list[-1].args == ("EXPUNGE", "10")
    mail.expunge.assert_not_called()


@pytest.mark.asyncio
async def test_move_refuses_mailbox_wide_expunge() -> None:
    client = EmailClient(_config())
    mail = MagicMock(capabilities=(b"IMAP4REV1",))
    mail.capability.return_value = ("OK", [b"IMAP4REV1"])
    with pytest.raises(EmailDeletionError, match="refusing"):
        await client._move_uids(mail, ["10"], '"Archive"')


@pytest.mark.asyncio
async def test_capability_refresh_failure_is_reported_accurately() -> None:
    client = EmailClient(_config())
    mail = MagicMock(capabilities=(b"IMAP4REV1", b"MOVE"))
    mail.capability.return_value = ("NO", [b"unavailable"])
    with pytest.raises(EmailConnectionError, match="refresh IMAP capabilities"):
        await client._get_capability_set(mail)


@pytest.mark.parametrize("email_id", ["", "0", "-1", "1:*", "1\r\nEXPUNGE"])
def test_uid_validation_rejects_unsafe_ids(email_id: str) -> None:
    with pytest.raises(ValueError):
        EmailClient._validate_email_ids([email_id])


@pytest.mark.parametrize("value", ["subject\r\nLOGOUT", "folder\nEXPUNGE", "name\x00suffix"])
def test_imap_values_reject_command_delimiters(value: str) -> None:
    with pytest.raises(ValueError, match="CR, LF, or NUL"):
        escape_imap_string(value)


def test_mime_headers_are_decoded_and_plain_text_is_preferred() -> None:
    message = MIMEMultipart("alternative")
    message["From"] = "=?utf-8?b?Sm9zw6k=?= <jose@example.com>"
    message["Subject"] = "=?utf-8?b?SMOpbGxv?="
    message.attach(MIMEText("<b>HTML first</b>", "html", "utf-8"))
    message.attach(MIMEText("Plain text", "plain", "utf-8"))
    content = EmailClient(_config())._format_email_content(((b"1", message.as_bytes()),))
    assert content["subject"] == "Héllo"
    assert content["from"].startswith("José")
    assert content["content"] == "Plain text"


@pytest.mark.asyncio
async def test_cancelled_blocking_call_finishes_before_cleanup_continues() -> None:
    started = Event()
    release = Event()
    finished = Event()

    def blocking_operation() -> None:
        started.set()
        release.wait(timeout=1)
        finished.set()

    task = asyncio.create_task(_run_blocking(blocking_operation))
    while not started.is_set():
        await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()
