"""Storage interface.

Everything SynthSift keeps – uploads index, parsed conversations, turns,
paragraphs and every enrichment module's output – goes through this small
interface, so the backend can be swapped.  :mod:`.duckdb_store` is the
implementation used today.

Tables are declared with :class:`Table` (name + typed columns).  Writes are
column batches (``dict[column, list]``) so a backend can bulk-load them
(DuckDB takes them as Arrow tables without per-row Python work).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

# Portable column types; backends map them to their own.
TYPES = ("text", "int", "bigint", "float", "bool", "json", "int[]", "text[]")


@dataclass(frozen=True)
class Column:
    name: str
    type: str = "text"

    def __post_init__(self) -> None:
        if self.type not in TYPES:
            raise ValueError(f"unknown column type {self.type!r} for {self.name}")


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[Column, ...]
    #: columns to index for lookups (e.g. para_hash)
    index: tuple[str, ...] = ()
    description: str = ""

    @property
    def names(self) -> list[str]:
        return [c.name for c in self.columns]


def table(name: str, *cols: str | tuple[str, str], index: Sequence[str] = (), description: str = "") -> Table:
    """``table("x_regex", "para_hash", ("start", "int"), …)`` – plain names are text."""
    columns = tuple(Column(c) if isinstance(c, str) else Column(*c) for c in cols)
    return Table(name, columns, tuple(index), description)


@dataclass
class Batch:
    """Rows for one table, column-wise."""

    table: Table
    data: dict[str, list[Any]] = field(default_factory=dict)

    @classmethod
    def from_rows(cls, tbl: Table, rows: Iterable[Sequence[Any]]) -> "Batch":
        rows = list(rows)
        return cls(tbl, {c: [r[i] for r in rows] for i, c in enumerate(tbl.names)})

    def __len__(self) -> int:
        return len(next(iter(self.data.values()), []))


class Storage(ABC):
    """What SynthSift needs from a database."""

    backend: str = ""

    # ------------------------------------------------------------ schema
    @abstractmethod
    def ensure(self, tbl: Table) -> None:
        """Create the table (and its indexes) if missing."""

    @abstractmethod
    def drop(self, name: str) -> None: ...

    @abstractmethod
    def tables(self) -> list[str]: ...

    # ------------------------------------------------------------ writes
    @abstractmethod
    def insert(self, batch: Batch) -> int:
        """Append rows; returns how many were written."""

    @abstractmethod
    def delete(self, name: str, where: str = "", params: Sequence[Any] = ()) -> None:
        """Delete rows (all rows when ``where`` is empty)."""

    @abstractmethod
    def delete_in(self, name: str, column: str, values: Sequence[Any]) -> None:
        """Delete rows whose ``column`` is one of ``values``."""

    # ------------------------------------------------------------- reads
    @abstractmethod
    def query(self, sql: str, params: Sequence[Any] = ()) -> list[tuple]:
        """Run a read query in the backend's SQL dialect."""

    @abstractmethod
    def select_in(self, name: str, column: str, values: Sequence[Any], columns: Sequence[str] | None = None) -> list[tuple]:
        """Rows whose ``column`` is one of ``values``."""

    @abstractmethod
    def count(self, name: str) -> int: ...

    # ------------------------------------------------------------- misc
    @abstractmethod
    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        """Backend-specific statement (modules that need SQL, e.g. large joins)."""

    @abstractmethod
    def transaction(self): ...

    @abstractmethod
    def close(self) -> None: ...

    # key/value state (module fingerprints, schema version, …)
    def get_state(self, key: str, default: Any = None) -> Any:
        rows = self.query("SELECT value FROM kv_state WHERE key = ?", (key,))
        return rows[0][0] if rows else default

    def set_state(self, key: str, value: Any) -> None:
        self.execute("DELETE FROM kv_state WHERE key = ?", (key,))
        self.execute("INSERT INTO kv_state VALUES (?, ?)", (key, None if value is None else str(value)))
