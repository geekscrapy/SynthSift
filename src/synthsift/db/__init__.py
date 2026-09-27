"""Pluggable storage.  ``open_storage(path)`` returns the configured backend."""

from __future__ import annotations

from pathlib import Path

from .base import Batch, Column, Storage, Table, table  # noqa: F401


def open_storage(path: Path | str | None, backend: str = "duckdb") -> Storage:
    if backend == "duckdb":
        from .duckdb_store import DuckDBStorage

        return DuckDBStorage(path)
    raise ValueError(f"unknown storage backend {backend!r}")
