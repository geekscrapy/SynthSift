#!/usr/bin/env python3
"""Collect LLM agent transcripts from a machine into a SynthSift upload zip.

Standalone: Python 3.8+ standard library only, so this folder can be copied
to any machine and run there without installing SynthSift.

    python3 synthsift_collect.py                          # your own sessions
    sudo python3 synthsift_collect.py --all-users         # every home directory
    python3 synthsift_collect.py --agents claude_code --since-days 7 --dry-run
    python3 synthsift_collect.py --target claude_code=/data/claude --target /mnt/img/home/bob

What is collected comes from the glob lists in ``globs/<agent>.txt`` (one file
per agent; the file name is the SynthSift parser the files are meant for).
Each matched file is stored as ``<host>/<user>/<agent>/<path>`` – the layout
SynthSift's upload expects – and a ``synthsift-manifest.csv`` records where
every file came from, its size, modification time and SHA-256.

Nothing on disk is modified.  SQLite databases are read with SQLite's online
backup API, which gives a consistent copy even while the agent is writing.
"""

from __future__ import annotations

import argparse
import csv
import fnmatch
import getpass
import glob
import hashlib
import io
import os
import platform
import re
import socket
import sqlite3
import sys
import tempfile
import time
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Mapping, Optional, Sequence, Tuple

VERSION = "1.0"
HERE = Path(__file__).resolve().parent
DEFAULT_GLOBS = HERE / "globs"
MANIFEST = "synthsift-manifest.csv"
SQLITE_MAGIC = b"SQLite format 3\x00"
_VAR = re.compile(r"\$\{(\w+)\}")
_WILD = re.compile(r"[*?\[]")
# variables that can be worked out for any user from their home directory
_PER_USER_VARS = {"HOME": "", "USERPROFILE": "", "LOCALAPPDATA": "AppData/Local", "APPDATA": "AppData/Roaming"}
_NOT_USERS = {"shared", "public", "default", "default user", "all users", "defaultapppool", "guest"}


# --------------------------------------------------------------------- rules
@dataclass
class Rules:
    """The glob list of one agent."""

    agent: str
    patterns: List[str] = field(default_factory=list)
    excludes: List[str] = field(default_factory=list)


def load_rules(globs_dir: Path = DEFAULT_GLOBS, agents: Optional[Iterable[str]] = None) -> List[Rules]:
    wanted = set(agents) if agents else None
    out = []
    for f in sorted(Path(globs_dir).glob("*.txt")):
        if wanted is not None and f.stem not in wanted:
            continue
        rules = Rules(f.stem)
        for raw in f.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            (rules.excludes if line.startswith("!") else rules.patterns).append(line.lstrip("!").strip())
        out.append(rules)
    if wanted is not None:
        missing = wanted - {r.agent for r in out}
        if missing:
            raise SystemExit(f"no glob list for: {', '.join(sorted(missing))} (looked in {globs_dir})")
    return out


def expand(pattern: str, home: Path, env: Mapping[str, str], current: bool) -> Optional[Tuple[str, str]]:
    """Resolve ``~`` and ``${VAR}``; returns (base directory, glob below it) or
    None when the pattern uses a variable that is not set for this user."""

    def var(m: "re.Match[str]") -> str:
        name = m.group(1)
        if not current and name in _PER_USER_VARS:
            sub = _PER_USER_VARS[name]
            return str(home / sub) if sub else str(home)
        value = env.get(name) if current else None
        if not value:
            raise KeyError(name)
        return value

    try:
        p = _VAR.sub(var, pattern)
    except KeyError:
        return None
    if p == "~" or p.startswith(("~/", "~\\")):
        p = str(home) + p[1:]
    p = p.replace("\\", "/")
    if "/./" in p:
        base, rest = p.split("/./", 1)
    else:  # keep everything after the last directory without wildcards
        parts = p.split("/")
        i = next((n for n, part in enumerate(parts) if _WILD.search(part)), len(parts) - 1)
        base, rest = "/".join(parts[:i]) or "/", "/".join(parts[i:])
    return base.rstrip("/") or "/", rest


# ------------------------------------------------------------------- finding
@dataclass
class Match:
    agent: str
    user: str
    path: Path
    rel: str  # path kept in the zip, below <host>/<user>/<agent>/
    base: str
    pattern: str


def homes(all_users: bool) -> List[Tuple[str, Path, bool]]:
    """(user, home directory, is the current user) to search."""
    me, my_home = getpass.getuser(), Path.home()
    if not all_users:
        return [(me, my_home, True)]
    found = {my_home.resolve(): (me, my_home, True)}
    roots = [Path("/home"), Path("/Users")]
    if os.name == "nt":
        roots.append(Path(os.environ.get("SystemDrive", "C:") + "\\") / "Users")
    for root in roots:
        try:
            entries = list(root.iterdir())
        except OSError:
            continue
        for h in entries:
            if h.is_dir() and not h.name.startswith(".") and h.name.lower() not in _NOT_USERS:
                found.setdefault(h.resolve(), (h.name, h, False))
    for special, name in ((Path("/root"), "root"), (Path("/var/root"), "root")):
        if special.is_dir():
            found.setdefault(special.resolve(), (name, special, False))
    return sorted(found.values(), key=lambda x: x[0])


def _mtime(path: Path) -> float:
    try:
        t = path.stat().st_mtime
    except OSError:
        return 0.0
    wal = Path(f"{path}-wal")
    return max(t, wal.stat().st_mtime) if wal.is_file() else t


def file_patterns(r: Rules) -> List[str]:
    """The transcript file names an agent's glob list looks for (``*.jsonl``, ``state.db`` …)."""
    return sorted({p.replace("\\", "/").rsplit("/", 1)[-1] for p in r.patterns})


def _owner(path: Path) -> str:
    try:
        import pwd  # not on Windows

        return pwd.getpwuid(path.stat().st_uid).pw_name
    except (ImportError, KeyError, OSError):
        return getpass.getuser()


def targets(specs: Sequence[str], rules: List[Rules]) -> Tuple[List[Tuple[str, Path, bool]], List[Tuple[str, str, Path]]]:
    """``--target`` values → (home directories to search like any other, (user, agent, folder) agent folders).

    ``AGENT=DIR``: DIR holds that agent's transcripts, anywhere below it.  ``DIR``: a home directory
    (a mounted disk, a restored backup …), searched with every agent's usual ``~/`` locations.
    """
    known = {r.agent for r in rules}
    my_home = Path.home().resolve()
    home_dirs: List[Tuple[str, Path, bool]] = []
    agent_dirs: List[Tuple[str, str, Path]] = []
    for spec in specs:
        agent, sep, raw = spec.partition("=")
        if not sep or agent not in known:
            if sep and not os.path.exists(os.path.expanduser(spec)):
                raise SystemExit(f"--target {spec}: '{agent}' is not an agent being collected "
                                 f"({', '.join(sorted(known))})")
            agent, raw = "", spec
        path = Path(os.path.expanduser(raw))
        if not path.is_dir():
            raise SystemExit(f"--target {spec}: {path} is not a folder")
        if agent:
            agent_dirs.append((_owner(path), agent, path))
        else:
            mine = path.resolve() == my_home  # your own home: your ${VARIABLES} apply as usual
            home_dirs.append((getpass.getuser() if mine else path.resolve().name, path, mine))
    return home_dirs, agent_dirs


def find(rules: List[Rules], who: List[Tuple[str, Path, bool]], env: Mapping[str, str] = os.environ,
         since: Optional[float] = None, dirs: Sequence[Tuple[str, str, Path]] = ()) -> List[Match]:
    """Files to collect from home directories (``who``) and agent folders (``dirs``, from ``--target``)."""
    out: List[Match] = []
    seen = set()  # (agent, real path): the same file reached twice (two patterns, a symlink, a target) is kept once

    def take(r: Rules, user: str, base: str, pattern: str, source: str) -> None:
        for hit in sorted(glob.glob(os.path.join(base, pattern), recursive=True)):
            path = Path(hit)
            if not path.is_file() or any(fnmatch.fnmatch(path.name, x) for x in r.excludes):
                continue
            key = (r.agent, os.path.realpath(hit))
            rel = os.path.relpath(hit, base).replace(os.sep, "/")
            if key in seen or rel.startswith("../") or (since is not None and _mtime(path) < since):
                continue
            seen.add(key)
            out.append(Match(r.agent, user, path, rel, base, source))

    for user, home, current in who:
        for r in rules:
            for pattern in r.patterns:
                spec = expand(pattern, home, env, current)
                if spec is not None:
                    take(r, user, *spec, pattern)
    by_agent = {r.agent: r for r in rules}
    for user, agent, folder in dirs:
        base = str(folder).rstrip("/\\") or "/"
        for name in file_patterns(by_agent[agent]):
            take(by_agent[agent], user, base, f"**/{name}", f"--target {agent}={folder}")
    return out


# ------------------------------------------------------------------- writing
@dataclass
class Report:
    written: List[dict] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    bytes: int = 0


def _is_sqlite(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(16) == SQLITE_MAGIC
    except OSError:
        return False


def _snapshot(db: Path, dest: Path) -> None:
    src = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=10)
    dst = sqlite3.connect(str(dest))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_zip(matches: List[Match], out: Path, host: str) -> Report:
    rep = Report()
    used = set()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, allowZip64=True, strict_timestamps=False) as zf, \
            tempfile.TemporaryDirectory() as tmp:
        for m in matches:
            arc = f"{host}/{m.user}/{m.agent}/{m.rel}"
            if arc in used:  # e.g. ~/.openclaw and ~/.clawdbot both holding agents/main/…
                label = os.path.basename(m.base).lstrip(".") or "root"
                arc = f"{host}/{m.user}/{m.agent}/from-{label}/{m.rel}"
                n = 2
                while arc in used:
                    arc = f"{host}/{m.user}/{m.agent}/from-{label}-{n}/{m.rel}"
                    n += 1
            try:
                stat = m.path.stat()
                method, src = "copy", m.path
                if _is_sqlite(m.path):
                    snap = Path(tmp) / f"snap-{len(rep.written)}.sqlite"
                    try:
                        _snapshot(m.path, snap)
                        method, src = "sqlite-backup", snap
                    except sqlite3.Error:
                        wal = Path(f"{m.path}-wal")
                        if wal.is_file():  # copy the write-ahead log too, SynthSift reads it
                            zf.write(wal, arc + "-wal")
                            method = "copy+wal"
                zf.write(src, arc)
                size = src.stat().st_size
                digest = _sha256(src)
            except (OSError, ValueError, zipfile.BadZipFile) as exc:
                rep.skipped.append(f"{m.path}: {getattr(exc, 'strerror', None) or exc}")
                continue
            used.add(arc)
            rep.bytes += size
            rep.written.append({
                "agent": m.agent, "user": m.user, "zip_path": arc, "source_path": str(m.path), "bytes": size,
                "modified_utc": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(timespec="seconds"),
                "sha256": digest, "method": method, "pattern": m.pattern,
            })
        buf = io.StringIO()
        cols = ["agent", "user", "zip_path", "source_path", "bytes", "modified_utc", "sha256", "method", "pattern"]
        w = csv.DictWriter(buf, fieldnames=cols)
        w.writeheader()
        w.writerows(rep.written)
        zf.writestr(MANIFEST, buf.getvalue())
        zf.comment = (f"synthsift-collect {VERSION} host={host} collected={datetime.now(timezone.utc).isoformat(timespec='seconds')} "
                      f"by={getpass.getuser()} os={platform.system()}").encode()[:65535]
    return rep


# ------------------------------------------------------------------------ CLI
def default_host() -> str:
    return socket.gethostname().split(".")[0] or "unknown-host"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="synthsift_collect.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("-o", "--output", help="zip to write (default: synthsift-collect-<host>-<time>.zip)")
    ap.add_argument("--host", help="host name to file the transcripts under (default: this machine's)")
    ap.add_argument("--all-users", action="store_true", help="search every home directory, not just yours")
    ap.add_argument("--target", action="append", default=[], metavar="[AGENT=]DIR",
                    help="search here instead of the default locations (repeat for more folders): AGENT=DIR is a "
                         "folder holding that agent's transcripts anywhere below it; a bare DIR is a home directory "
                         "(e.g. a mounted disk). Add --all-users to search every home directory as well")
    ap.add_argument("--agents", help="comma-separated agents to collect (default: every glob list)")
    ap.add_argument("--since-days", type=float, help="only files changed in the last N days")
    ap.add_argument("--globs", default=str(DEFAULT_GLOBS), help="folder with the <agent>.txt glob lists")
    ap.add_argument("--dry-run", action="store_true", help="list what would be collected, write nothing")
    ap.add_argument("--list-agents", action="store_true", help="show the agents and their patterns, then exit")
    args = ap.parse_args(argv)

    rules = load_rules(Path(args.globs), args.agents.split(",") if args.agents else None)
    if args.list_agents:
        for r in rules:
            print(f"{r.agent}:")
            for p in r.patterns:
                print(f"    {p}")
            for x in r.excludes:
                print(f"    !{x}")
            if r.patterns:
                print(f"    --target {r.agent}=DIR finds: {', '.join(file_patterns(r))}")
        return 0
    host = args.host or default_host()
    since = time.time() - args.since_days * 86400 if args.since_days else None
    home_targets, agent_dirs = targets(args.target, rules)
    who = homes(args.all_users) if args.all_users or not args.target else []
    known = {h.resolve() for _, h, _ in who}
    who += [t for t in home_targets if t[1].resolve() not in known]
    matches = find(rules, who, os.environ, since, agent_dirs)
    if args.dry_run or not matches:
        for m in matches:
            print(f"{host}/{m.user}/{m.agent}/{m.rel}\t{m.path}")
        if not matches:
            print("nothing matched the glob lists in " + args.globs, file=sys.stderr)
            if args.target:
                print("hint: for a folder holding one agent's transcripts use --target AGENT=DIR "
                      "(a bare DIR is searched as a home directory)", file=sys.stderr)
            elif not args.all_users:
                print("hint: --all-users searches every home directory (needs permission to read them); "
                      "--target searches other folders", file=sys.stderr)
        return 0 if matches or args.dry_run else 1
    out = Path(args.output or f"synthsift-collect-{host}-{time.strftime('%Y%m%d-%H%M%S')}.zip")
    rep = write_zip(matches, out, host)
    per_agent = Counter(row["agent"] for row in rep.written)
    users = sorted({row["user"] for row in rep.written})
    summary = ", ".join(f"{n} {a}" for a, n in sorted(per_agent.items()))
    print(f"wrote {out} ({rep.bytes / 1e6:.1f} MB): {summary} file(s) for {', '.join(users)} on {host}", file=sys.stderr)
    for s in rep.skipped:
        print(f"skipped {s}", file=sys.stderr)
    print("upload it in SynthSift", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
