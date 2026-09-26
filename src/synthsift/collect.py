"""Collect agent transcripts from this machine into an upload zip.

    synthsift collect                      # current user, this host
    synthsift collect --all-users -o ir.zip  # every home directory (run as root)

The zip uses the ``host/user/<harness>/…`` layout SynthSift expects, so it can
be uploaded as is.  Nothing is modified on disk: live SQLite databases are read
through SQLite's online backup API, which gives a consistent snapshot even
while the agent is writing.
"""

from __future__ import annotations

import getpass
import os
import socket
import sqlite3
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .ingest import is_transcript_name

OPENCLAW_DIRS = (".openclaw", ".clawdbot", ".moltbot")  # current name first, then the older ones


@dataclass
class Found:
    harness: str
    user: str
    path: Path
    arcpath: str  # path below host/user/<harness>/
    kind: str  # "session", "subagent", "database", "archive", "legacy"


@dataclass
class Report:
    files: list[Found] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    bytes: int = 0


def _recent(path: Path, since: float | None) -> bool:
    try:
        return since is None or path.stat().st_mtime >= since
    except OSError:
        return False


def find_claude_code(claude_dir: Path, user: str, since: float | None = None) -> list[Found]:
    """``<claude_dir>/projects/<project>/<session>.jsonl`` plus sub-agent files."""
    projects = claude_dir / "projects"
    out: list[Found] = []
    if not projects.is_dir():
        return out
    for p in sorted(projects.rglob("*.jsonl")):
        if p.is_file() and _recent(p, since):
            rel = p.relative_to(projects).as_posix()
            out.append(Found("claude_code", user, p, rel, "subagent" if "/subagents/" in f"/{rel}" else "session"))
    return out


def find_openclaw(state_dir: Path, user: str, since: float | None = None) -> list[Found]:
    """Per-agent SQLite databases, legacy / archived JSONL and cold-storage files."""
    out: list[Found] = []
    agents = state_dir / "agents"
    if agents.is_dir():
        for agent in sorted(p for p in agents.iterdir() if p.is_dir()):
            db = agent / "agent" / "openclaw-agent.sqlite"
            if db.is_file() and (_recent(db, since) or _recent(Path(f"{db}-wal"), since)):
                out.append(Found("openclaw", user, db, db.relative_to(state_dir).as_posix(), "database"))
            sessions = agent / "sessions"
            if sessions.is_dir():
                for p in sorted(sessions.rglob("*")):
                    if p.is_file() and p.name != "sessions.json" and is_transcript_name(p.name) and _recent(p, since):
                        kind = "archive" if (".deleted." in p.name or ".reset." in p.name or "/cold/" in p.as_posix()) else "legacy"
                        out.append(Found("openclaw", user, p, p.relative_to(state_dir).as_posix(), kind))
    legacy = state_dir / "sessions"  # very old single-agent layout
    if legacy.is_dir():
        for p in sorted(legacy.glob("*.jsonl*")):
            if p.is_file() and is_transcript_name(p.name) and _recent(p, since):
                out.append(Found("openclaw", user, p, p.relative_to(state_dir).as_posix(), "legacy"))
    return out


def homes(all_users: bool) -> list[tuple[str, Path]]:
    """(user name, home directory) pairs to search."""
    if not all_users:
        return [(getpass.getuser(), Path.home())]
    found: dict[Path, str] = {}
    for base in (Path("/home"), Path("/Users")):
        if base.is_dir():
            for h in base.iterdir():
                if h.is_dir() and not h.name.startswith((".", "Shared")):
                    found[h] = h.name
    if Path("/root").is_dir():
        found[Path("/root")] = "root"
    found.setdefault(Path.home(), getpass.getuser())
    return sorted(((u, h) for h, u in found.items()), key=lambda x: x[0])


def discover(all_users: bool = False, since_days: float | None = None, claude_dir: Path | None = None,
             openclaw_dir: Path | None = None, harnesses: tuple[str, ...] = ("claude_code", "openclaw")) -> list[Found]:
    since = time.time() - since_days * 86400 if since_days else None
    out: list[Found] = []
    me = getpass.getuser()
    for user, home in homes(all_users):
        mine = user == me
        if "claude_code" in harnesses:
            d = claude_dir if (claude_dir and mine) else None
            if d is None:
                env = os.environ.get("CLAUDE_CONFIG_DIR") if mine else None
                d = Path(env).expanduser() if env else home / ".claude"
            out += find_claude_code(d, user, since)
        if "openclaw" in harnesses:
            dirs: list[Path] = []
            if openclaw_dir and mine:
                dirs = [openclaw_dir]
            else:
                env = os.environ.get("OPENCLAW_STATE_DIR") if mine else None
                dirs = [Path(env).expanduser()] if env else [home / d for d in OPENCLAW_DIRS]
            for d in dirs:
                out += find_openclaw(d, user, since)
    return out


def _snapshot(db: Path) -> bytes:
    """A consistent copy of a (possibly live, WAL-mode) SQLite database."""
    with tempfile.TemporaryDirectory() as tmp:
        dst_path = Path(tmp) / "snapshot.sqlite"
        src = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
        dst = sqlite3.connect(dst_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        return dst_path.read_bytes()


def write_zip(found: list[Found], out: Path, host: str) -> Report:
    report = Report()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in found:
            arc = f"{host}/{f.user}/{f.harness}/{f.arcpath}"
            try:
                if f.kind == "database":
                    try:
                        data = _snapshot(f.path)
                    except sqlite3.Error:
                        # read-only snapshot failed (e.g. no access to the -shm file): copy the files instead
                        data = f.path.read_bytes()
                        wal = Path(f"{f.path}-wal")
                        if wal.is_file():
                            zf.writestr(arc + "-wal", wal.read_bytes())
                else:
                    data = f.path.read_bytes()
            except OSError as exc:
                report.skipped.append(f"{f.path}: {exc.strerror or exc}")
                continue
            zf.writestr(arc, data)
            report.files.append(f)
            report.bytes += len(data)
    return report


def default_host() -> str:
    return socket.gethostname().split(".")[0] or "unknown-host"
