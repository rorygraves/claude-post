"""Security and correctness tests for declarative collection transforms."""

from __future__ import annotations

import pandas as pd
import pytest

from email_client.data_processing.datastore import DataStore, validate_operation_safety


@pytest.fixture
def store_and_id() -> tuple[DataStore, str]:
    store = DataStore()
    metadata = store.create(
        pd.DataFrame(
            {
                "sender": ["alice@example.com", "bob@example.com", "alice@example.com"],
                "score": [3, 1, 2],
                "date": ["2024-01-03", "2024-01-01", "2024-01-02"],
            }
        )
    )
    return store, metadata["id"]


@pytest.mark.parametrize(
    ("operation", "parameters", "rows"),
    [
        ("filter", {"column": "score", "operator": "gt", "value": 1}, 2),
        ("filter", {"column": "sender", "operator": "contains", "value": "ALICE"}, 2),
        ("head", {"rows": 1}, 1),
        ("tail", {"rows": 2}, 2),
        ("drop_duplicates", {"subset": ["sender"]}, 2),
        ("group_count", {"columns": ["sender"]}, 2),
    ],
)
def test_supported_transforms(
    store_and_id: tuple[DataStore, str],
    operation: str,
    parameters: dict[str, object],
    rows: int,
) -> None:
    store, collection_id = store_and_id
    result = store.update(collection_id, operation, parameters)
    assert result["shape"]["rows"] == rows


def test_sort_select_rename_and_drop(store_and_id: tuple[DataStore, str]) -> None:
    store, collection_id = store_and_id
    store.update(collection_id, "sort", {"by": "score"})
    assert store.fetch(collection_id)["data"][0]["score"] == 1
    store.update(collection_id, "select_columns", {"columns": ["sender", "score"]})
    store.update(collection_id, "rename_columns", {"mapping": {"sender": "from"}})
    store.update(collection_id, "drop_columns", {"columns": ["score"]})
    assert store.list_collections()[0]["columns"] == ["from"]


def test_datetime_values_are_json_safe(store_and_id: tuple[DataStore, str]) -> None:
    store, collection_id = store_and_id
    store.update(collection_id, "convert_datetime", {"columns": ["date"]})
    value = store.fetch(collection_id)["data"][0]["date"]
    assert isinstance(value, str)
    assert value.startswith("2024-")


@pytest.mark.parametrize(
    "operation",
    [
        'pd.DataFrame(__builtins__["open"](".env"))',
        'df[df["score"] > 1]',
        "import os",
        "exec",
    ],
)
def test_arbitrary_python_is_rejected(operation: str) -> None:
    with pytest.raises(ValueError, match="Unsupported transform operation"):
        validate_operation_safety(operation)


def test_failed_transform_is_recorded(store_and_id: tuple[DataStore, str]) -> None:
    store, collection_id = store_and_id
    with pytest.raises(ValueError):
        store.update(collection_id, "filter", {"column": "missing", "operator": "eq", "value": 1})
    assert store.get_history(collection_id)[-1]["success"] is False


def test_fetch_limit_zero_returns_zero_rows(store_and_id: tuple[DataStore, str]) -> None:
    store, collection_id = store_and_id
    result = store.fetch(collection_id, limit=0)
    assert result["data"] == []
    assert result["truncated"] is True


def test_collection_reads_are_defensive_copies(store_and_id: tuple[DataStore, str]) -> None:
    store, collection_id = store_and_id
    collection = store.get_collection(collection_id)
    assert collection is not None
    collection["df"].drop(columns=["score"], inplace=True)
    assert "score" in store.list_collections()[0]["columns"]


def test_store_limits() -> None:
    store = DataStore(max_collections=1, max_rows_per_collection=2)
    store.create(pd.DataFrame({"x": [1]}))
    with pytest.raises(ValueError, match="Collection limit"):
        store.create(pd.DataFrame({"x": [2]}))
    with pytest.raises(ValueError, match="maximum"):
        DataStore(max_rows_per_collection=1).create(pd.DataFrame({"x": [1, 2]}))
