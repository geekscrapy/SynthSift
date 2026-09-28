"""Enrichment modules.

Every enrichment step – pattern extraction, spaCy NLP, IOC / keyword
matching, text features, content labels, security findings – is a
:class:`Module`.  A module declares:

* ``tables``: what it writes; each module owns its tables.
* ``requires``: modules whose output it reads.
* ``options``: settings fields, shown in its card on the Settings page.

and implements either

* ``process(paras, deps)`` (``scope = "paragraph"``): a pure function from a
  chunk of paragraphs (and the rows its dependencies wrote for them) to rows
  of its own tables.  The runner calls it only for paragraphs it has not
  seen, possibly in parallel worker processes, and keys every row by the
  paragraph's content hash so results are reused across uploads.
* ``run_corpus(ctx)`` (``scope = "corpus"``): a whole-corpus step
  (SQL joins, cross-paragraph analysis) that reads and writes storage
  directly.

Modules whose output are entity candidates set ``span_table``; the
``entities`` resolver merges every enabled span table by ``priority`` into
the entities the graph shows.  Drop a new file into this package and decorate
the class with ``@register`` to add a module.
"""

from __future__ import annotations

import hashlib
import json
from abc import ABC
from dataclasses import dataclass
from typing import Any, ClassVar

from ..db import Table, table
from ..fields import Field

KINDS = ("extraction", "feature", "label", "analysis")
SECTION = "Modules"


@dataclass(frozen=True)
class ParaIn:
    """What a paragraph-scoped module sees of one paragraph."""

    hash: str
    text: str
    role: str  # user | assistant | system | thought | tool_call | tool_result
    code: bool = False
    arg: str | None = None  # tool_call paragraphs: the argument name


def para_hash(text: str, role: str, code: bool) -> str:
    return hashlib.sha1(f"{role}\x00{int(bool(code))}\x00{text}".encode("utf-8", "surrogatepass")).hexdigest()[:20]


def span_table(name: str, description: str = "") -> Table:
    """Standard table for entity candidates (see ``entities`` for how they merge).

    ``prio``: lower claims first.  ``ord``: emission order within a priority.
    ``alt_group`` / ``alt_rank``: alternatives for one candidate (e.g. a noun
    phrase and its shorter tails) – the resolver takes the first free one.
    ``key_hint``: text to normalise into the entity key instead of the surface
    (e.g. the lemma).  ``literal``: keep the key verbatim (paths, hashes…).
    """
    return table(name, "para_hash", ("start", "int"), ("end", "int"), "text", "category", "source", "key_hint",
                 ("literal", "bool"), ("prio", "int"), ("ord", "int"), ("alt_group", "int"), ("alt_rank", "int"),
                 ("labels", "text[]"), index=["para_hash"], description=description)


class Deps:
    """Rows the dependencies wrote, for the paragraphs of one chunk."""

    def __init__(self, data: dict[str, dict[str, list[tuple]]] | None = None, columns: dict[str, list[str]] | None = None):
        self.data = data or {}
        self.columns = columns or {}

    def rows(self, tbl: str, para: str) -> list[tuple]:
        return self.data.get(tbl, {}).get(para, [])

    def dicts(self, tbl: str, para: str) -> list[dict[str, Any]]:
        cols = self.columns.get(tbl, [])
        return [dict(zip(cols, r)) for r in self.rows(tbl, para)]

    def has(self, tbl: str) -> bool:
        return tbl in self.data


class Module(ABC):
    name: ClassVar[str] = ""
    label: ClassVar[str] = ""
    description: ClassVar[str] = ""
    kind: ClassVar[str] = "extraction"
    #: bump when the output of the same input changes (invalidates stored rows)
    version: ClassVar[str] = "1"
    requires: ClassVar[tuple[str, ...]] = ()
    scope: ClassVar[str] = "paragraph"  # paragraph | corpus
    #: reads conversations / turns through ``ctx.corpus()`` (skipped when the runner has no corpus)
    needs_corpus: ClassVar[bool] = False
    #: may run in worker processes (paragraph scope)
    parallel: ClassVar[bool] = True
    #: paragraphs per work unit
    chunk_size: ClassVar[int] = 1000
    default_enabled: ClassVar[bool] = True
    #: always on (the entity resolver)
    core: ClassVar[bool] = False
    #: no switch of its own: runs when the module named here runs, and its
    #: options are shown in that module's card (e.g. ``ioc_tokens`` for ``ioc``)
    helper_of: ClassVar[str] = ""
    #: position on the Settings page (within its kind)
    order: ClassVar[int] = 100
    tables: ClassVar[tuple[Table, ...]] = ()
    #: this module's own settings fields
    options: ClassVar[tuple[Field, ...]] = ()
    #: other settings its output depends on (shared "text sources" knobs …)
    uses_settings: ClassVar[tuple[str, ...]] = ()
    #: entity candidates for the resolver
    span_table: ClassVar[str | None] = None
    #: "claim": candidates compete for text; "label": they label whatever they overlap
    span_mode: ClassVar[str] = "claim"

    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg

    # ---------------------------------------------------------------- API
    @classmethod
    def switch(cls) -> str:
        return f"mod.{cls.name}"

    @classmethod
    def switched_on(cls, cfg: dict[str, Any]) -> bool:
        """Its own switch (helpers and core modules have none)."""
        if cls.core:
            return True
        if cls.helper_of:
            return False
        return bool(cfg.get(cls.switch(), cls.default_enabled))

    @classmethod
    def option_keys(cls) -> list[str]:
        return [f.key for f in cls.options] + list(cls.uses_settings)

    def dependencies(self, enabled: set[str]) -> list[str]:
        """Modules to run first (may depend on which modules are enabled)."""
        return list(self.requires)

    def fingerprint_extra(self, ctx: Any) -> Any:
        """Extra state that changes the output, e.g. the IOC lists' sizes and times."""
        return None

    def setup(self) -> None:
        """Load models / resources; called once per process before ``process``."""

    def process(self, paras: list[ParaIn], deps: Deps) -> dict[str, list[tuple]]:
        """Paragraph scope: rows per table (without anything else)."""
        raise NotImplementedError

    def run_corpus(self, ctx: Any) -> int:
        """Corpus scope: do the work against ``ctx.storage``; returns rows written."""
        raise NotImplementedError

    # --------------------------------------------------------- utilities
    def opt(self, key: str, default: Any = None) -> Any:
        return self.cfg.get(key, default)

    @classmethod
    def info(cls) -> dict[str, Any]:
        return {"name": cls.name, "label": cls.label, "description": cls.description, "kind": cls.kind,
                "scope": cls.scope, "requires": list(cls.requires), "core": cls.core, "helper_of": cls.helper_of,
                "switch": None if cls.core or cls.helper_of else cls.switch(),
                "default_enabled": cls.default_enabled, "parallel": cls.parallel, "version": cls.version,
                "tables": [{"name": t.name, "columns": t.names, "description": t.description} for t in cls.tables],
                "options": [f.key for f in cls.options], "span_table": cls.span_table}


def fingerprint(mod: type[Module], cfg: dict[str, Any], dep_fps: list[str], extra: Any = None) -> str:
    payload = {"m": mod.name, "v": mod.version, "o": {k: cfg.get(k) for k in mod.option_keys()},
               "d": sorted(dep_fps), "x": extra}
    return hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]


_REGISTRY: dict[str, type[Module]] = {}


def register(cls: type[Module]) -> type[Module]:
    if not cls.name:
        raise ValueError(f"{cls.__name__} needs a name")
    if cls.kind not in KINDS:
        raise ValueError(f"{cls.name}: kind must be one of {KINDS}")
    for f in cls.options:
        f.module = cls.helper_of or cls.name
        f.section = SECTION
    _REGISTRY[cls.name] = cls
    return cls


def registry() -> dict[str, type[Module]]:
    """Every module, in Settings-page order (by kind, then ``order``)."""
    return dict(sorted(_REGISTRY.items(), key=lambda kv: (KINDS.index(kv[1].kind), kv[1].order, kv[0])))


def enabled_modules(cfg: dict[str, Any]) -> set[str]:
    """Switched-on modules plus everything they need."""
    reg = registry()
    todo = [n for n, m in reg.items() if m.switched_on(cfg)]
    on: set[str] = set()
    while todo:
        n = todo.pop()
        if n in on or n not in reg:
            continue
        on.add(n)
        todo.extend(reg[n].requires)
    return on


def switch_field(cls: type[Module]) -> Field | None:
    if cls.core or cls.helper_of:
        return None
    return Field(cls.switch(), f"Run {cls.label}", "bool", cls.default_enabled, "parse", SECTION,
                 cls.description, module=cls.name)
