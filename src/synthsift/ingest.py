"""Read an uploaded zip (``host/user/harness/<transcript>.json``) into
normalized :class:`~synthsift.models.Conversation` objects."""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from .harnesses import ParserNotImplemented, get_parser, sniff_parser
from .models import Conversation

TRANSCRIPT_EXTENSIONS = (".json", ".jsonl", ".ndjson", ".zst", ".gz", ".sqlite", ".db")
# archived transcripts keep ".jsonl" mid-name, e.g. "<id>.jsonl.deleted.2026-01-01T10-00-00Z"
_ARCHIVED = re.compile(r"\.jsonl\.[^/]+$", re.I)
_HOME = re.compile(r"^(?:/home|/Users|C:\\Users)[/\\]([^/\\]+)", re.I)


def is_transcript_name(name: str) -> bool:
    low = name.lower()
    if low.endswith((".lock", "-wal", "-shm", "-journal")):
        return False
    return low.endswith(TRANSCRIPT_EXTENSIONS) or bool(_ARCHIVED.search(low))


@dataclass
class IngestReport:
    conversations: list[Conversation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    files: int = 0
    skipped: int = 0


def locate(path: str) -> tuple[str, str, str | None, str]:
    """Split a zip member path into (host, user, harness folder, session name).

    The expected layout is ``host/user/harness/file``; extra wrapper folders
    before it are ignored, and extra folders after the harness folder become
    part of the session name.  When no folder matches a registered harness the
    positional layout is assumed and the harness is left to sniffing.
    """
    parts = [p for p in PurePosixPath(path).parts if p not in ("", ".")]
    filename = parts[-1]
    dirs = parts[:-1]
    for i in range(len(dirs) - 1, -1, -1):
        if get_parser(dirs[i]) is not None:
            host = dirs[i - 2] if i >= 2 else "unknown-host"
            user = dirs[i - 1] if i >= 1 else "unknown-user"
            session = "/".join([*dirs[i + 1:], filename])
            return host, user, dirs[i], session
    host = dirs[-3] if len(dirs) >= 3 else "unknown-host"
    user = dirs[-2] if len(dirs) >= 2 else "unknown-user"
    harness = dirs[-1] if dirs else None
    return host, user, harness, filename


def _is_junk(name: str) -> bool:
    """Directories, macOS metadata and hidden files – except agent state folders
    such as ``.claude`` or ``.openclaw`` that are hidden by design."""
    p = PurePosixPath(name)
    return name.endswith("/") or any(
        part == "__MACOSX" or (part.startswith(".") and get_parser(part) is None) for part in p.parts
    )


def conv_id(dataset: str, path: str, index: int) -> str:
    return "c" + hashlib.sha1(f"{dataset}\0{path}\0{index}".encode()).hexdigest()[:10]


def read_zip(data: bytes, dataset: str) -> IngestReport:
    report = IngestReport()
    not_implemented: dict[str, int] = {}
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        report.warnings.append(f"not a valid zip file: {exc}")
        return report
    with zf:
        names = set(zf.namelist())
        for info in sorted(zf.infolist(), key=lambda i: i.filename):
            name = info.filename
            if _is_junk(name):
                continue
            if not is_transcript_name(name):
                report.skipped += 1
                continue
            report.files += 1
            raw = zf.read(info)
            host, user, folder, session = locate(name)
            filename = PurePosixPath(name).name
            parser_cls = get_parser(folder) if folder else None
            if parser_cls is None:
                parser_cls = sniff_parser(raw, filename)
                if parser_cls is None:
                    report.warnings.append(f"{name}: unknown harness '{folder}' and format not recognised")
                    continue
            parser = parser_cls()
            parser.companions = {sfx: zf.read(name + sfx) for sfx in ("-wal",) if name + sfx in names}
            try:
                convs = parser.parse(raw, filename)
            except ParserNotImplemented:
                not_implemented[parser_cls.name] = not_implemented.get(parser_cls.name, 0) + 1
                continue
            except Exception as exc:  # noqa: BLE001 - report per file, keep going
                report.warnings.append(f"{name}: {parser_cls.name} parser failed: {type(exc).__name__}: {exc}")
                continue
            report.warnings.extend(f"{name}: {w}" for w in parser.warnings)
            for i, conv in enumerate(convs):
                conv.id = conv_id(dataset, name, i)
                conv.host, conv.user, conv.harness = host, user, parser_cls.name
                if user == "unknown-user":  # e.g. a zip of ~/.claude itself: take the user from the project path
                    m = _HOME.match(str(conv.meta.get("cwd") or ""))
                    if m:
                        conv.user = m.group(1)
                conv.session, conv.source_path, conv.index_in_session = session, name, i
                conv.meta.setdefault("dataset", dataset)
                if not conv.messages:
                    report.warnings.append(f"{name}: conversation {i} has no messages")
                    continue
                report.conversations.append(conv)
    for harness, n in sorted(not_implemented.items()):
        report.warnings.append(
            f"{n} file(s) in '{harness}' folders skipped: the {harness} parser is a placeholder "
            f"(implement synthsift/harnesses/{harness}.py)"
        )
    return report
