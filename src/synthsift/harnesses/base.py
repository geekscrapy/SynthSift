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

A parser that needs more than the one file can name other zip members to read
alongside it (:meth:`HarnessParser.companion_paths`), and one whose agent keeps
the same conversation in several files can skip the lesser copies
(:meth:`HarnessParser.superseded_by`).
"""

from __future__ import annotations

import gzip
import json
import re
from abc import ABC, abstractmethod
from datetime import datetime, timezone
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
    #: False for placeholder shells
    implemented: ClassVar[bool] = True
    #: one-line description of the on-disk format
    description: ClassVar[str] = ""
    #: tool names whose calls start a sub-agent (counted as sub-agent calls on the dashboard)
    subagent_tools: ClassVar[tuple[str, ...]] = ()
    #: hidden folders (``.name``) the agent keeps transcripts in, which ingest must not skip as junk
    hidden_folders: ClassVar[tuple[str, ...]] = ()
    #: transcript file names without a transcript extension (e.g. ``overview.txt``)
    file_names: ClassVar[tuple[str, ...]] = ()

    def __init__(self) -> None:
        #: other files from the same upload that belong to the one being parsed, keyed as
        #: :meth:`companion_paths` names them.  Filled in by the ingester before :meth:`parse`.
        self.companions: dict[str, bytes] = {}
        #: the file's path inside the upload, set by the ingester
        self.source_path: str = ""
        #: non-fatal problems met while parsing, reported back to the user
        self.warnings: list[str] = []

    @abstractmethod
    def parse(self, raw: bytes, filename: str) -> list[Conversation]:
        """Turn one transcript file into one or more conversations."""

    @classmethod
    def companion_paths(cls, path: str) -> dict[str, str]:
        """Other zip members to read with ``path``: {key in :attr:`companions`: member path}.

        Missing members are left out.  By default a SQLite database's ``-wal`` file.
        """
        return {"-wal": path + "-wal"}

    @classmethod
    def owns(cls, path: str) -> bool:
        """True when ``path``'s shape alone says it is this agent's transcript, whatever folder holds it
        (e.g. a zip of ``~/.gemini`` holds both Gemini CLI and Antigravity files)."""
        return False

    @classmethod
    def superseded_by(cls, path: str) -> tuple[str, ...]:
        """Zip members holding a fuller copy of ``path``'s conversation; when one is in the upload,
        ``path`` is skipped."""
        return ()

    def sniff(self, raw: bytes, filename: str) -> float:
        """Confidence 0..1 that ``raw`` is in this harness' format.

        Only used when the zip folder name does not identify the harness.
        """
        return 0.0

    # ---- helpers shared by parsers -------------------------------------
    @staticmethod
    def load_json(raw: bytes) -> Any:
        return json.loads(raw.decode("utf-8-sig", errors="replace"))

    @staticmethod
    def load_jsonl(raw: bytes) -> list[Any]:
        return [json.loads(line) for line in raw.decode("utf-8-sig", errors="replace").splitlines() if line.strip()]

    @staticmethod
    def read_jsonl(raw: bytes) -> tuple[list[Any], int]:
        """Lenient JSONL: returns the rows and how many lines could not be parsed.

        Live transcripts are often copied mid-write, so a truncated last line
        must not lose the rest of the file.
        """
        rows, bad = [], 0
        for line in raw.decode("utf-8-sig", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                bad += 1
        return rows, bad

    @staticmethod
    def decompress(raw: bytes, filename: str) -> tuple[bytes, str]:
        """Undo ``.zst`` / ``.gz`` compression; returns the data and the inner name."""
        low = filename.lower()
        if low.endswith(".gz") or raw[:2] == b"\x1f\x8b":
            return gzip.decompress(raw), re.sub(r"\.gz$", "", filename, flags=re.I)
        if low.endswith(".zst") or raw[:4] == ZSTD_MAGIC:
            return zstd_decompress(raw), re.sub(r"\.zst$", "", filename, flags=re.I)
        return raw, filename

    @staticmethod
    def iso_time(value: Any) -> str | None:
        """ISO-8601 from an ISO string or a Unix timestamp in seconds or milliseconds."""
        if value is None or value == "":
            return None
        if isinstance(value, (int, float)):
            secs = value / 1000 if value > 1e11 else value
            try:
                return datetime.fromtimestamp(secs, tz=timezone.utc).isoformat().replace("+00:00", "Z")
            except (OverflowError, OSError, ValueError):
                return None
        return str(value)


ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"


def zstd_decompress(raw: bytes) -> bytes:
    try:
        import zstandard
    except ImportError as exc:  # pragma: no cover - zstandard is a declared dependency
        raise RuntimeError("reading .zst files needs the 'zstandard' package") from exc
    # frames written in streaming mode carry no content size, so always stream
    with zstandard.ZstdDecompressor().stream_reader(raw, read_across_frames=True) as reader:
        return reader.read()


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


def hidden_folders() -> set[str]:
    return {f for cls in _REGISTRY.values() for f in cls.hidden_folders}


def extra_file_names() -> set[str]:
    return {f.lower() for cls in _REGISTRY.values() for f in cls.file_names}


def path_owner(path: str) -> type[HarnessParser] | None:
    return next((cls for cls in _REGISTRY.values() if cls.owns(path)), None)


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
