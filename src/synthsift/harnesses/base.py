"""Base class + registry for harness transcript parsers.

Adding a harness
----------------
1. Create ``synthsift/harnesses/<name>.py``.
2. Subclass :class:`HarnessParser`, set ``name`` (and optionally ``aliases``),
   implement :meth:`HarnessParser.parse` (and optionally :meth:`sniff`).
3. Decorate the class with ``@register``.

Modules in this package are imported automatically, so nothing else needs to
change.  The zip layout ``host/user/<harness>/<file>`` routes a file to the
parser whose ``name``/``aliases`` match ``<harness>``; files in unknown harness
folders are offered to every implemented parser's :meth:`sniff`.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from ..models import Conversation


class ParserNotImplemented(NotImplementedError):
    """Raised by stub parsers that are registered but not written yet."""


class HarnessParser(ABC):
    #: canonical name, also the expected zip folder name
    name: ClassVar[str] = ""
    #: human friendly label for the UI
    label: ClassVar[str] = ""
    #: other folder names that should route to this parser
    aliases: ClassVar[tuple[str, ...]] = ()
    #: file extensions this parser accepts
    extensions: ClassVar[tuple[str, ...]] = (".json", ".jsonl")
    #: False for placeholder shells
    implemented: ClassVar[bool] = True
    #: one-line description of the on-disk format
    description: ClassVar[str] = ""

    @abstractmethod
    def parse(self, raw: bytes, filename: str) -> list[Conversation]:
        """Turn one transcript file into one or more conversations."""

    def sniff(self, raw: bytes, filename: str) -> float:
        """Confidence 0..1 that ``raw`` is in this harness' format.

        Only used when the zip folder name does not identify the harness.
        """
        return 0.0

    # ---- helpers shared by parsers -------------------------------------
    @staticmethod
    def load_json(raw: bytes) -> Any:
        text = raw.decode("utf-8-sig", errors="replace")
        return json.loads(text)

    @staticmethod
    def load_jsonl(raw: bytes) -> list[Any]:
        rows = []
        for line in raw.decode("utf-8-sig", errors="replace").splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
        return rows


_REGISTRY: dict[str, type[HarnessParser]] = {}


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def register(cls: type[HarnessParser]) -> type[HarnessParser]:
    if not cls.name:
        raise ValueError(f"{cls.__name__} must define a name")
    _REGISTRY[cls.name] = cls
    return cls


def all_parsers() -> list[type[HarnessParser]]:
    return sorted(_REGISTRY.values(), key=lambda c: (not c.implemented, c.name))


def get_parser(folder: str) -> type[HarnessParser] | None:
    key = _norm(folder)
    for cls in _REGISTRY.values():
        if key == _norm(cls.name) or key in {_norm(a) for a in cls.aliases}:
            return cls
    return None


def sniff_parser(raw: bytes, filename: str) -> type[HarnessParser] | None:
    best, best_score = None, 0.0
    for cls in _REGISTRY.values():
        if not cls.implemented:
            continue
        try:
            score = cls().sniff(raw, filename)
        except Exception:  # noqa: BLE001 - a broken sniff must not break ingest
            score = 0.0
        if score > best_score:
            best, best_score = cls, score
    return best if best_score >= 0.5 else None
