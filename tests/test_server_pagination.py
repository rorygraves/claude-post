"""Framework response and schema regression tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from email_client.server import EmailMCPServer
from mcp_framework.base import _json_default
from mcp_framework.schema_generator import extract_parameter_schema


def test_json_default_supports_datetime_and_pandas_scalars() -> None:
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    assert _json_default(now) == "2024-01-01T00:00:00+00:00"
    assert _json_default(pd.Timestamp("2024-01-01")) == "2024-01-01T00:00:00"


def test_transform_schema_exposes_allowlisted_operations() -> None:
    server = EmailMCPServer()
    schema = extract_parameter_schema(server.transform)
    operation = schema["properties"]["operation"]
    assert "filter" in operation["enum"]
    assert "parameters" not in schema.get("required", [])


def test_server_can_be_discovered_without_loading_credentials(monkeypatch) -> None:
    monkeypatch.delenv("EMAIL_ADDRESS", raising=False)
    monkeypatch.delenv("EMAIL_PASSWORD", raising=False)
    server = EmailMCPServer()
    assert "mail-search" in server._tools
