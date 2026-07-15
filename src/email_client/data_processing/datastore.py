"""In-memory, bounded storage for email search result collections."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from threading import RLock
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

SUPPORTED_OPERATIONS = frozenset(
    {
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
    }
)

FILTER_OPERATORS = frozenset({"eq", "ne", "gt", "ge", "lt", "le", "contains", "in", "not_null", "is_null"})


def validate_operation_safety(operation: str) -> None:
    """Reject anything other than a supported, declarative operation name.

    DataFrame objects expose file, network, and Python introspection APIs, so
    executing user-provided Python cannot be made safe with an AST denylist.
    """
    if operation not in SUPPORTED_OPERATIONS:
        supported = ", ".join(sorted(SUPPORTED_OPERATIONS))
        raise ValueError(f"Unsupported transform operation '{operation}'. Supported operations: {supported}")


def get_descriptive_dtype(series: pd.Series[Any]) -> str:
    """Return a stable, user-facing description of a pandas dtype."""
    dtype = series.dtype
    if pd.api.types.is_datetime64_any_dtype(dtype):
        return "datetime"
    if pd.api.types.is_integer_dtype(dtype):
        return "integer"
    if pd.api.types.is_float_dtype(dtype):
        return "float"
    if pd.api.types.is_bool_dtype(dtype):
        return "boolean"
    if isinstance(dtype, pd.CategoricalDtype):
        return "categorical"
    if pd.api.types.is_object_dtype(dtype):
        sample = series.dropna().head(100)
        if sample.empty:
            return "unknown"
        types_found = {type(value).__name__.lower() for value in sample}
        if types_found == {"str"}:
            return "string"
        if len(types_found) == 1:
            return next(iter(types_found))
        return f"mixed({', '.join(sorted(types_found))})"
    return str(dtype)


def get_descriptive_dtypes(df: pd.DataFrame) -> dict[str, str]:
    """Return descriptive data types for every collection column."""
    return {str(column): get_descriptive_dtype(df[column]) for column in df.columns}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CollectionMetadata:
    """Mutable metadata kept in sync with a stored DataFrame."""

    def __init__(
        self,
        collection_id: str,
        name: str,
        shape: tuple[int, int],
        columns: list[str],
        dtypes: dict[str, str],
        source_folder: str | None = None,
    ) -> None:
        self.id = collection_id
        self.name = name
        self.shape = shape
        self.columns = columns
        self.dtypes = dtypes
        self.source_folder = source_folder
        self.created_at = _utc_now()
        self.last_modified = self.created_at

    def update_from(self, data: pd.DataFrame) -> None:
        self.shape = data.shape
        self.columns = [str(column) for column in data.columns]
        self.dtypes = get_descriptive_dtypes(data)
        self.last_modified = _utc_now()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "shape": {"rows": self.shape[0], "columns": self.shape[1]},
            "columns": self.columns,
            "dtypes": self.dtypes,
            "source_folder": self.source_folder,
            "created_at": self.created_at.isoformat(),
            "last_modified": self.last_modified.isoformat(),
        }


class DataStore:
    """Thread-safe, bounded collection storage for one MCP server process."""

    def __init__(self, *, max_collections: int = 100, max_rows_per_collection: int = 10_000) -> None:
        if max_collections <= 0 or max_rows_per_collection <= 0:
            raise ValueError("DataStore limits must be positive")
        self.max_collections = max_collections
        self.max_rows_per_collection = max_rows_per_collection
        self._collections: dict[str, pd.DataFrame] = {}
        self._metadata: dict[str, CollectionMetadata] = {}
        self._execution_history: dict[str, list[dict[str, Any]]] = {}
        self._lock = RLock()

    def _validate_size(self, data: pd.DataFrame) -> None:
        if len(data) > self.max_rows_per_collection:
            raise ValueError(f"Collection has {len(data)} rows; maximum is {self.max_rows_per_collection}")

    def create(
        self,
        data: pd.DataFrame,
        name: str | None = None,
        source_folder: str | None = None,
    ) -> dict[str, Any]:
        self._validate_size(data)
        with self._lock:
            if len(self._collections) >= self.max_collections:
                raise ValueError(
                    f"Collection limit reached ({self.max_collections}); delete an existing collection first"
                )
            collection_id = str(uuid.uuid4())
            collection_name = name or f"collection_{collection_id[:8]}"
            stored = data.copy(deep=True)
            self._collections[collection_id] = stored
            self._metadata[collection_id] = CollectionMetadata(
                collection_id=collection_id,
                name=collection_name,
                shape=stored.shape,
                columns=[str(column) for column in stored.columns],
                dtypes=get_descriptive_dtypes(stored),
                source_folder=source_folder,
            )
            self._execution_history[collection_id] = []
            logger.info("Created collection %s with shape %s", collection_id, stored.shape)
            return self._metadata[collection_id].to_dict()

    def get_collection(self, collection_id: str) -> dict[str, Any] | None:
        with self._lock:
            if collection_id not in self._collections:
                return None
            metadata = self._metadata[collection_id]
            return {
                "df": self._collections[collection_id].copy(deep=True),
                "source_folder": metadata.source_folder,
                "metadata": metadata.to_dict(),
            }

    @staticmethod
    def _require_columns(df: pd.DataFrame, columns: Sequence[str]) -> list[str]:
        normalized = [str(column) for column in columns]
        missing = [column for column in normalized if column not in df.columns]
        if missing:
            raise ValueError(f"Unknown columns: {', '.join(missing)}")
        return normalized

    def _apply_transform(
        self,
        df: pd.DataFrame,
        operation: str,
        parameters: Mapping[str, Any],
    ) -> pd.DataFrame:
        validate_operation_safety(operation)

        if operation in {"select_columns", "drop_columns"}:
            columns_value = parameters.get("columns")
            if not isinstance(columns_value, list) or not columns_value:
                raise ValueError("parameters.columns must be a non-empty list")
            columns = self._require_columns(df, columns_value)
            return df.loc[:, columns].copy() if operation == "select_columns" else df.drop(columns=columns)

        if operation == "rename_columns":
            mapping = parameters.get("mapping")
            if not isinstance(mapping, dict) or not mapping:
                raise ValueError("parameters.mapping must be a non-empty object")
            if not all(isinstance(key, str) and isinstance(value, str) for key, value in mapping.items()):
                raise ValueError("rename mapping keys and values must be strings")
            self._require_columns(df, list(mapping))
            return df.rename(columns=mapping)

        if operation == "sort":
            by_value = parameters.get("by")
            by = [by_value] if isinstance(by_value, str) else by_value
            if not isinstance(by, list) or not by:
                raise ValueError("parameters.by must be a column name or non-empty list")
            columns = self._require_columns(df, by)
            ascending = parameters.get("ascending", True)
            if not isinstance(ascending, (bool, list)):
                raise ValueError("parameters.ascending must be a boolean or list of booleans")
            return df.sort_values(by=columns, ascending=ascending)

        if operation == "filter":
            column = parameters.get("column")
            operator = parameters.get("operator")
            if not isinstance(column, str):
                raise ValueError("parameters.column must be a string")
            self._require_columns(df, [column])
            if operator not in FILTER_OPERATORS:
                allowed = ", ".join(sorted(FILTER_OPERATORS))
                raise ValueError(f"Unsupported filter operator '{operator}'. Supported operators: {allowed}")
            series = df[column]
            value = parameters.get("value")
            if operator == "eq":
                mask = series == value
            elif operator == "ne":
                mask = series != value
            elif operator == "gt":
                mask = series > value
            elif operator == "ge":
                mask = series >= value
            elif operator == "lt":
                mask = series < value
            elif operator == "le":
                mask = series <= value
            elif operator == "contains":
                if not isinstance(value, str):
                    raise ValueError("contains requires a string value")
                case_sensitive = parameters.get("case_sensitive", False)
                if not isinstance(case_sensitive, bool):
                    raise ValueError("parameters.case_sensitive must be a boolean")
                mask = series.astype("string").str.contains(
                    value,
                    case=case_sensitive,
                    regex=False,
                    na=False,
                )
            elif operator == "in":
                if not isinstance(value, list):
                    raise ValueError("in requires a list value")
                mask = series.isin(value)
            elif operator == "not_null":
                mask = series.notna()
            else:
                mask = series.isna()
            return df.loc[mask].copy()

        if operation in {"head", "tail"}:
            rows = parameters.get("rows", 5)
            if not isinstance(rows, int) or isinstance(rows, bool) or rows < 0:
                raise ValueError("parameters.rows must be a non-negative integer")
            return df.head(rows).copy() if operation == "head" else df.tail(rows).copy()

        if operation == "drop_duplicates":
            subset_value = parameters.get("subset")
            subset: list[str] | None = None
            if subset_value is not None:
                if not isinstance(subset_value, list) or not subset_value:
                    raise ValueError("parameters.subset must be a non-empty list when supplied")
                subset = self._require_columns(df, subset_value)
            keep = parameters.get("keep", "first")
            if keep not in {"first", "last", False}:
                raise ValueError("parameters.keep must be 'first', 'last', or false")
            return df.drop_duplicates(subset=subset, keep=keep)

        if operation == "convert_datetime":
            columns_value = parameters.get("columns")
            if not isinstance(columns_value, list) or not columns_value:
                raise ValueError("parameters.columns must be a non-empty list")
            columns = self._require_columns(df, columns_value)
            errors = parameters.get("errors", "raise")
            if errors not in {"raise", "coerce"}:
                raise ValueError("parameters.errors must be 'raise' or 'coerce'")
            result = df.copy()
            for column in columns:
                result[column] = pd.to_datetime(result[column], errors=errors, utc=True)
            return result

        if operation == "group_count":
            columns_value = parameters.get("columns")
            if not isinstance(columns_value, list) or not columns_value:
                raise ValueError("parameters.columns must be a non-empty list")
            columns = self._require_columns(df, columns_value)
            count_name = parameters.get("count_name", "count")
            if not isinstance(count_name, str) or not count_name:
                raise ValueError("parameters.count_name must be a non-empty string")
            return df.groupby(columns, dropna=False).size().reset_index(name=count_name)

        raise AssertionError(f"Unhandled supported operation: {operation}")

    def update(
        self,
        collection_id: str,
        operation: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if collection_id not in self._collections:
                raise ValueError(f"Collection {collection_id} not found")
            before = self._collections[collection_id]
            metadata = self._metadata[collection_id]
            timestamp = _utc_now().isoformat()
            try:
                result = self._apply_transform(before.copy(deep=True), operation, parameters or {})
                if not isinstance(result, pd.DataFrame):
                    raise TypeError("Transform did not produce a DataFrame")
                self._validate_size(result)
                self._collections[collection_id] = result
                metadata.update_from(result)
                self._execution_history[collection_id].append(
                    {
                        "operation": operation,
                        "parameters": dict(parameters or {}),
                        "timestamp": timestamp,
                        "success": True,
                        "shape_before": before.shape,
                        "shape_after": result.shape,
                    }
                )
                return metadata.to_dict()
            except Exception as exc:
                self._execution_history[collection_id].append(
                    {
                        "operation": operation,
                        "parameters": dict(parameters or {}),
                        "timestamp": timestamp,
                        "success": False,
                        "error": str(exc),
                    }
                )
                logger.warning("Collection transform failed for %s: %s", collection_id, type(exc).__name__)
                raise

    def fetch(
        self,
        collection_id: str,
        limit: int | None = None,
        format: str = "records",
    ) -> dict[str, Any]:
        if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit < 0):
            raise ValueError("limit must be a non-negative integer or null")
        with self._lock:
            if collection_id not in self._collections:
                raise ValueError(f"Collection {collection_id} not found")
            df = self._collections[collection_id]
            metadata = self._metadata[collection_id]
            display_df = df.head(limit) if limit is not None else df
            if format == "records":
                data: Any = json.loads(display_df.to_json(orient="records", date_format="iso"))
            elif format == "dict":
                data = json.loads(display_df.to_json(orient="columns", date_format="iso"))
            elif format == "csv":
                data = display_df.to_csv(index=False)
            elif format == "json":
                data = display_df.to_json(orient="records", date_format="iso")
            else:
                raise ValueError("Unsupported format. Use records, dict, csv, or json")
            return {
                "metadata": metadata.to_dict(),
                "data": data,
                "truncated": limit is not None and len(df) > limit,
                "total_rows": len(df),
            }

    def delete(self, collection_id: str) -> bool:
        with self._lock:
            if collection_id not in self._collections:
                raise ValueError(f"Collection {collection_id} not found")
            del self._collections[collection_id]
            del self._metadata[collection_id]
            del self._execution_history[collection_id]
            logger.info("Deleted collection %s", collection_id)
            return True

    def list_collections(self) -> list[dict[str, Any]]:
        with self._lock:
            return [metadata.to_dict() for metadata in self._metadata.values()]

    def get_history(self, collection_id: str) -> list[dict[str, Any]]:
        with self._lock:
            if collection_id not in self._collections:
                raise ValueError(f"Collection {collection_id} not found")
            return [entry.copy() for entry in self._execution_history[collection_id]]

    def preview(self, collection_id: str, rows: int = 5) -> dict[str, Any]:
        if not isinstance(rows, int) or isinstance(rows, bool) or rows < 0:
            raise ValueError("rows must be a non-negative integer")
        with self._lock:
            if collection_id not in self._collections:
                raise ValueError(f"Collection {collection_id} not found")
            df = self._collections[collection_id]
            return {
                "metadata": self._metadata[collection_id].to_dict(),
                "preview": json.loads(df.head(rows).to_json(orient="records", date_format="iso")),
                "dtypes": get_descriptive_dtypes(df),
            }

    def combine(self, target_collection_id: str, source_collection_id: str) -> dict[str, Any]:
        with self._lock:
            if target_collection_id not in self._collections:
                raise ValueError(f"Target collection {target_collection_id} not found")
            if source_collection_id not in self._collections:
                raise ValueError(f"Source collection {source_collection_id} not found")
            if target_collection_id == source_collection_id:
                raise ValueError("A collection cannot be combined with itself")
            target = self._collections[target_collection_id]
            source = self._collections[source_collection_id]
            if list(target.columns) != list(source.columns):
                raise ValueError(
                    "Collections have different columns: "
                    f"target has {list(target.columns)}, source has {list(source.columns)}"
                )
            combined = pd.concat([target, source], ignore_index=True)
            self._validate_size(combined)
            self._collections[target_collection_id] = combined
            metadata = self._metadata[target_collection_id]
            metadata.update_from(combined)
            self._execution_history[target_collection_id].append(
                {
                    "operation": "combine",
                    "source_collection_id": source_collection_id,
                    "timestamp": _utc_now().isoformat(),
                    "success": True,
                    "shape_before": target.shape,
                    "shape_after": combined.shape,
                }
            )
            return metadata.to_dict()
