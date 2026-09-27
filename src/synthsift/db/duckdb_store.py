"""DuckDB implementation of :class:`~synthsift.db.base.Storage`.

One database file per workspace (``<data dir>/synthsift.duckdb``).  DuckDB is
an embedded, columnar database: bulk inserts arrive as Arrow tables (no
per-row Python), large joins such as matching millions of IOCs run in
vectorised C++, and the file survives restarts.

Thread safety: every thread gets its own cursor on the shared connection,
which DuckDB supports.  Only one process may open the file for writing.

Lookups by a list of keys (``select_in`` / ``delete_in``) register the keys
as an Arrow table and semi-join against it, which is much faster than
binding a long list parameter.
"""

from __future__ import annotations

import itertools
import json
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Sequence

import duckdb
import pyarrow as pa

from .base import Batch, Storage, Table

_SQL_TYPES = {"text": "VARCHAR", "int": "INTEGER", "bigint": "BIGINT", "float": "DOUBLE", "bool": "BOOLEAN",
              "json": "VARCHAR", "int[]": "INTEGER[]", "text[]": "VARCHAR[]"}
_ARROW_TYPES = {"text": pa.string(), "int": pa.int32(), "bigint": pa.int64(), "float": pa.float64(),
                "bool": pa.bool_(), "json": pa.string(), "int[]": pa.list_(pa.int32()), "text[]": pa.list_(pa.string())}


_seq = itertools.count()


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


class DuckDBStorage(Storage):
    def __init__(self, path: Path | str | None):
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path) if path else ":memory:")
        self._local = threading.local()
        self._write_lock = threading.RLock()
        self.execute("CREATE TABLE IF NOT EXISTS kv_state (key VARCHAR PRIMARY KEY, value VARCHAR)")

    # one cursor per thread
    @property
    def con(self) -> duckdb.DuckDBPyConnection:
        cur = getattr(self._local, "cur", None)
        if cur is None:
            cur = self._con.cursor()
            self._local.cur = cur
        return cur

    # ------------------------------------------------------------ schema
    def ensure(self, tbl: Table) -> None:
        cols = ", ".join(f"{_q(c.name)} {_SQL_TYPES[c.type]}" for c in tbl.columns)
        with self._write_lock:
            self.con.execute(f"CREATE TABLE IF NOT EXISTS {_q(tbl.name)} ({cols})")
            existing = {r[0] for r in self.con.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = ?", (tbl.name,)).fetchall()}
            for c in tbl.columns:  # additive migrations: new columns on existing tables
                if c.name not in existing:
                    self.con.execute(f"ALTER TABLE {_q(tbl.name)} ADD COLUMN {_q(c.name)} {_SQL_TYPES[c.type]}")
            # ``tbl.index`` is only a hint here: DuckDB answers our lookups with hash joins and
            # zone maps, and ART indexes would double the file size and slow bulk inserts.

    def drop(self, name: str) -> None:
        with self._write_lock:
            self.con.execute(f"DROP TABLE IF EXISTS {_q(name)}")

    def tables(self) -> list[str]:
        return [r[0] for r in self.con.execute("SELECT table_name FROM information_schema.tables").fetchall()]

    # ------------------------------------------------------------ writes
    def insert(self, batch: Batch) -> int:
        n = len(batch)
        if not n:
            return 0
        tbl = batch.table
        arrays = []
        for c in tbl.columns:
            values = batch.data.get(c.name, [None] * n)
            if c.type == "json":
                values = [v if v is None or isinstance(v, str) else json.dumps(v, ensure_ascii=False) for v in values]
            arrays.append(pa.array(values, type=_ARROW_TYPES[c.type]))
        arrow = pa.Table.from_arrays(arrays, names=tbl.names)
        cols = ", ".join(_q(c) for c in tbl.names)
        with self._write_lock:
            cur = self.con
            cur.register("_synthsift_batch", arrow)
            try:
                cur.execute(f"INSERT INTO {_q(tbl.name)} ({cols}) SELECT {cols} FROM _synthsift_batch")
            finally:
                cur.unregister("_synthsift_batch")
        return n

    def delete(self, name: str, where: str = "", params: Sequence[Any] = ()) -> None:
        with self._write_lock:
            self.con.execute(f"DELETE FROM {_q(name)}" + (f" WHERE {where}" if where else ""), list(params))

    @contextmanager
    def _values(self, values: Sequence[Any]):
        """Register ``values`` as a one-column table ``v`` – joining against it is far faster
        than binding a long list parameter."""
        name = f"_synthsift_values_{next(_seq)}"
        cur = self.con
        cur.register(name, pa.table({"v": list(values)}))
        try:
            yield name
        finally:
            cur.unregister(name)

    def delete_in(self, name: str, column: str, values: Sequence[Any]) -> None:
        if not values:
            return
        with self._write_lock, self._values(values) as vals:
            self.con.execute(f"DELETE FROM {_q(name)} WHERE {_q(column)} IN (SELECT v FROM {vals})")

    # ------------------------------------------------------------- reads
    def query(self, sql: str, params: Sequence[Any] = ()) -> list[tuple]:
        return self.con.execute(sql, list(params)).fetchall()

    def select_in(self, name: str, column: str, values: Sequence[Any], columns: Sequence[str] | None = None) -> list[tuple]:
        if not values:
            return []
        cols = ", ".join(f"x.{_q(c)}" for c in columns) if columns else "x.*"
        with self._values(values) as vals:
            return self.con.execute(f"SELECT {cols} FROM {_q(name)} x SEMI JOIN {vals} ON x.{_q(column)} = {vals}.v").fetchall()

    def count(self, name: str) -> int:
        return self.con.execute(f"SELECT count(*) FROM {_q(name)}").fetchone()[0]

    # ------------------------------------------------------------- misc
    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        with self._write_lock:
            self.con.execute(sql, list(params))

    @contextmanager
    def transaction(self):
        with self._write_lock:
            cur = self.con
            cur.execute("BEGIN TRANSACTION")
            try:
                yield self
            except BaseException:
                cur.execute("ROLLBACK")
                raise
            cur.execute("COMMIT")

    def close(self) -> None:
        try:
            self._con.close()
        except duckdb.Error:
            pass
