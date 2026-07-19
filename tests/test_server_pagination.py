"""Framework response and schema regression tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from email_client.server import EmailMCPServer
from mcp_framework.base import _json_default
from mcp_framework.schema_generator import extract_parameter_schema, parse_docstring_params


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


def test_transform_parameters_description_documents_each_operation() -> None:
    server = EmailMCPServer()
    # Mirror how base.py overlays docstring descriptions onto the generated schema.
    descriptions = parse_docstring_params(server.transform.__doc__)
    params_doc = descriptions["parameters"]

    # Every allowlisted operation's parameter shape must be documented...
    for operation in (
        "select_columns",
        "drop_columns",
        "rename_columns",
        "sort",
        "filter",
        "head",
        "tail",
        "drop_duplicates",
        "convert_datetime",
        "group_count",
    ):
        assert operation in params_doc, f"{operation} missing from parameters docs"
    # ...with the key shapes callers get wrong the most.
    assert "columns" in params_doc
    assert "operator" in params_doc and "contains" in params_doc
    # And the docstring parser must not have leaked operation names as bogus parameters.
    assert set(descriptions) <= {"collection_id", "operation", "parameters"}


def test_server_can_be_discovered_without_loading_credentials(monkeypatch) -> None:
    monkeypatch.delenv("EMAIL_ADDRESS", raising=False)
    monkeypatch.delenv("EMAIL_PASSWORD", raising=False)
    server = EmailMCPServer()
    assert "mail-search" in server._tools
