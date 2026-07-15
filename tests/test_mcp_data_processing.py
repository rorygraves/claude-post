"""MCP server integration tests for collection operations."""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from email_client.data_processing import DataStore
from email_client.server import EmailMCPServer


@pytest.fixture
def server_and_id() -> tuple[EmailMCPServer, str]:
    store = DataStore()
    metadata = store.create(pd.DataFrame({"sender": ["a@example.com", "b@example.com"], "score": [2, 1]}))
    return EmailMCPServer(email_client=MagicMock(), datastore=store), metadata["id"]


@pytest.mark.asyncio
async def test_transform_fetch_preview_and_list(server_and_id: tuple[EmailMCPServer, str]) -> None:
    server, collection_id = server_and_id
    transformed = await server.transform(collection_id, "sort", {"by": "score"})
    fetched = await server.fetch(collection_id)
    preview = await server.preview(collection_id, rows=1)
    collections = await server.list_collections()
    assert transformed["shape"]["rows"] == 2
    assert fetched["data"][0]["score"] == 1
    assert preview["preview"][0]["score"] == 1
    assert collections[0]["id"] == collection_id


@pytest.mark.asyncio
async def test_combine(server_and_id: tuple[EmailMCPServer, str]) -> None:
    server, target_id = server_and_id
    source = server.datastore.create(pd.DataFrame({"sender": ["c@example.com"], "score": [3]}))
    result = await server.combine(target_id, source["id"])
    assert result["shape"]["rows"] == 3


@pytest.mark.asyncio
async def test_transform_rejects_legacy_python(server_and_id: tuple[EmailMCPServer, str]) -> None:
    server, collection_id = server_and_id
    with pytest.raises(ValueError, match="Unsupported transform"):
        await server.transform(collection_id, 'pd.DataFrame(open(".env"))')  # type: ignore[arg-type]
