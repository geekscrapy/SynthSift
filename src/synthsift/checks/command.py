"""What a tool call's command line does, worked out once per turn for every check (``ctx.command``).

Turns "a file, a domain and a command were mentioned" into facts a check can test: which entities are part of
the command itself, which files it writes, whether it uploads, downloads, pipes remote content into an
interpreter, runs a command on another host, or dumps bulk data. Everything is structural – no list of named
offensive tools – so a check reasons about the direction data moves.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from typing import Any

from .base import Mention

# Flags that mark a filename as a *write/upload* target rather than a source.
_WRITE_FLAG = re.compile(r"(?:^|\s)(?:-o|-O|--output)(?:=|\s+)(\S+)")
# curl -T / --upload-file <file>: the file is read and sent, not written
_UPLOAD_FILE = re.compile(r"(?:^|\s)(?:-T|--upload-file)(?:=|\s+)['\"]?([^\s'\"]+)")
# -d @file, --data-binary @file, -F 'field=@file' (quoted or not)
_UPLOAD_DATA = re.compile(
    r"(?:^|\s)(?:-d|--data(?:-binary|-raw|-urlencode|-ascii)?|-F|--form)(?:=|\s+)['\"]?(?:[\w.\[\]-]+=)?@?([^\s'\"]+)")
_REDIRECT = re.compile(r"(?<!\d)>>?\s*(\S+)")
_METHOD_WRITE = re.compile(r"(?:^|\s)-X\s*(?:POST|PUT|PATCH)\b", re.I)
# A fetch whose output is piped straight into an interpreter (fetch-and-run).
# (`python -c '…'`, `perl -e …`, `python -m json.tool` read stdin as data, not code)
_FETCH_TO_INTERP = re.compile(
    r"\b(?:curl|wget|fetch|iwr|invoke-webrequest)\b[^\n|]*\|\s*(?:sudo\s+)?"
    r"(?:ba?sh|z?sh|dash|python[0-9.]*|perl|ruby|node|php|pwsh|powershell)\b(?!\s+-[cemE]\b)", re.I)
# data piped *into* a network client, e.g. `tar c … | curl -T - host`, `… | nc host 9000`
_PIPE_TO_NET = re.compile(r"\|\s*(?:sudo\s+)?(?:curl|wget|nc|ncat|netcat|socat|ssh|http|xh)\b", re.I)
_REMOTE_EXEC = re.compile(r"\b(?:ssh|sshpass)\b\s+\S+", re.I)
_DOWNLOADER = re.compile(r"\b(?:curl|wget|fetch|git\s+clone|iwr|invoke-webrequest|scp|rsync)\b", re.I)
_LOOPBACK = re.compile(r"^(?:https?://)?(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[?::1\]?)\b")
_BULK_READ = re.compile(r"\bSELECT\b[^\n]+\bFROM\b|\b(?:mysqldump|pg_dump(?:all)?|mongoexport|mongodump|sqlite3)\b|"
                        r"\btar\s+-?\w*c|\bzip\s+-r\b", re.I)

# Bulky text arguments of file-writing, editing and messaging tools (Write, Edit,
# apply_patch, message, Antigravity's write_to_file / replace_file_content …): the
# text is data being written, not a command.  Compared in lower case.
_BULK_ARGS = {"content", "contents", "new_string", "old_string", "old_str", "new_str", "file_text", "text", "body",
              "patch", "edits", "new_source", "diff", "description", "prompt", "message", "instructions", "instruction",
              "codecontent", "replacementchunks", "replacementcontent", "targetcontent", "task"}


def command_text(arguments: dict[str, Any] | None, tool_name: str | None) -> str:
    """Best-effort single command string from a tool call's arguments."""
    if not arguments:
        return tool_name or ""
    preferred = ("command", "cmd", "commandline", "commands", "script", "code", "query", "sql", "input", "args", "arg")
    by_key = {str(k).lower(): v for k, v in arguments.items()}  # Antigravity capitalises: CommandLine
    for key in preferred:
        v = by_key.get(key)
        if isinstance(v, (str, list)):
            return " ".join(map(str, v)) if isinstance(v, list) else str(v)
    parts = []
    for k, v in arguments.items():
        if str(k).lower() in _BULK_ARGS:
            continue
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, (list, dict)):
            parts.append(str(v))
    return " ".join(parts)


_HEREDOC = re.compile(r"<<-?[ \t]*(['\"]?)([A-Za-z_]\w*)\1([^\n]*)\n(.*?)(?:\n[ \t]*\2[ \t]*(?=\n|$)|\Z)", re.S)
_SHELL_FEED = re.compile(r"(?:^|[\s|;&(])(?:sudo\s+(?:-\S+\s+)*)?(?:ba|z|k|da|fi)?sh\b|\bssh\b")


def strip_heredocs(cmd: str) -> str:
    """Drop here-document bodies that are data – a Python script, a file being
    written – so their text is not read as shell.  Bodies fed to a shell
    (``bash <<EOF``, ``… <<EOF | sh``, ``ssh host <<EOF``) are kept."""
    if "<<" not in cmd:
        return cmd

    def repl(m: re.Match) -> str:
        head = cmd[cmd.rfind("\n", 0, m.start()) + 1:m.start()]
        if _SHELL_FEED.search(head) or _SHELL_FEED.search(m.group(3)):
            return m.group(0)
        return f"<<{m.group(2)}{m.group(3)}"

    return _HEREDOC.sub(repl, cmd)


_SEGMENTS = re.compile(r"\|\|?|&&|;|\n")
_HTTP_CLIENTS = {"curl", "wget", "http", "https", "xh", "httpie", "aria2c", "iwr", "irm", "invoke-webrequest",
                 "invoke-restmethod"}


def base_command(cmd: str) -> str:
    """The program a command line runs (``sudo``, ``env``, ``VAR=x`` … skipped)."""
    try:
        toks = shlex.split(cmd, posix=True)
    except ValueError:
        toks = cmd.split()
    for tok in toks:
        if "=" in tok and not tok.startswith("-"):
            continue  # leading VAR=value assignment
        if tok in ("sudo", "doas", "env", "time", "nohup", "exec", "command"):
            continue
        return tok.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return ""


def http_segments(cmd: str) -> str:
    """The parts of a command line run by an HTTP client (upload flags only mean
    something there: ``grep -F`` or ``cut -d`` are not uploads)."""
    return "\n".join(seg for seg in _SEGMENTS.split(cmd) if base_command(seg.strip()).lower() in _HTTP_CLIENTS)


def write_targets(cmd: str) -> set[str]:
    """Files a command line writes: ``-o`` / ``--output`` and redirections."""
    out: set[str] = set()
    for rx in (_WRITE_FLAG, _REDIRECT):
        out.update(m.group(1).strip("'\"") for m in rx.finditer(cmd))
    return {t for t in out if t and not t.startswith("-")}


def _matches(name: str, targets: set[str]) -> bool:
    base = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return any(t == name or t.endswith(name) or name.endswith(t) or t.rsplit("/", 1)[-1] == base for t in targets)


@dataclass
class CommandFacts:
    """A parsed tool call. ``mentions`` and the entity lists only hold entities that are part of the command itself
    (not a here-doc body, a description or file content being written)."""

    text: str                      # the command line, here-doc data bodies dropped
    base: str                      # the program it runs (or the tool's name)
    mentions: list[Mention] = field(default_factory=list)
    remotes: list[Mention] = field(default_factory=list)       # hosts, URLs, IPs … (loopback left out)
    locals: list[Mention] = field(default_factory=list)        # files (device files left out)
    secrets: list[Mention] = field(default_factory=list)       # credential-shaped entities
    writes: set[str] = field(default_factory=set)               # files it writes (raw targets)
    local_writes: list[Mention] = field(default_factory=list)  # files it writes
    local_reads: list[Mention] = field(default_factory=list)   # files it reads
    uploads: bool = False          # sends data: curl -d @f / -T / -X POST, object-store copy, scp / rsync up
    downloads: bool = False        # fetches to disk (or clones)
    fetch_and_run: bool = False    # pipes fetched content into an interpreter
    remote_exec: bool = False      # runs a command on another host (ssh)
    bulk_read: bool = False        # reads bulk data with no file entity: a DB query or dump, an archive
    pipes_to_network: bool = False  # pipes data into a network client

    @property
    def sources(self) -> list[Mention]:
        """Data that could leave the host: files it reads and secrets it uses."""
        return self.local_reads + self.secrets

    @property
    def sends(self) -> bool:
        """It moves data out: an upload, or data piped into a network client."""
        return self.uploads or self.pipes_to_network

    @classmethod
    def of(cls, event: Any, mentions: list[Mention]) -> "CommandFacts":
        cmd = strip_heredocs(command_text(event.arguments, event.tool_name))
        base = base_command(cmd) or (event.tool_name or "")
        own = [m for m in mentions if m.text in cmd]
        remotes = [m for m in own if m.remote and not _LOOPBACK.match(m.key)]
        locals_ = [m for m in own if m.local and not m.key.startswith("/dev/")]
        writes = write_targets(cmd)
        local_writes = [m for m in locals_ if _matches(m.text, writes) or _matches(m.key, writes)]
        local_reads = [m for m in locals_ if m not in local_writes]
        lower = cmd.lower()
        object_store_up = bool(re.search(r"\b(?:aws\s+s3|gsutil|az\s+storage)\b[^\n]*\b(?:cp|sync|mv|put)\b", lower)) \
            and bool(re.search(r"\b(?:s3|gs)://", lower))
        copy_up = base in ("scp", "rsync", "sftp") and bool(re.search(r"\S+@\S+:|:\S", cmd)) and bool(local_reads)
        http = http_segments(cmd)
        return cls(
            text=cmd, base=base, mentions=own, remotes=remotes, locals=locals_, secrets=[m for m in own if m.secret],
            writes=writes, local_writes=local_writes, local_reads=local_reads,
            uploads=bool(_UPLOAD_DATA.search(http)) or bool(_UPLOAD_FILE.search(http)) or bool(_METHOD_WRITE.search(http))
            or object_store_up or copy_up,
            downloads=bool(_DOWNLOADER.search(cmd)) and (bool(writes) or "clone" in lower),
            fetch_and_run=bool(_FETCH_TO_INTERP.search(cmd)),
            remote_exec=bool(_REMOTE_EXEC.match(cmd.strip())) or base in ("ssh", "sshpass"),
            bulk_read=bool(_BULK_READ.search(cmd)),
            pipes_to_network=bool(_PIPE_TO_NET.search(cmd)),
        )
