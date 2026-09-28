"""Security signals for analysts: dataflow chains, secrets and risky operations.

Everything here is deterministic pattern matching over what the agent (or the
user) actually did. Nothing is executed and no LLM is involved. The design is
deliberately *structural* rather than a shipped catalogue of named tools: it
reasons about the direction data moves, whether secrets are exposed and whether
data could be destroyed. What counts as a "notable command" beyond that is left
to an analyst-editable watchlist in Settings.

Signals produced:

* **Dataflow chains** — source → action → sink links between entities touched
  by one tool call, e.g. ``example.com → /tmp/payload`` (download to disk),
  ``/etc/shadow → example.net`` (a local secret leaving the host) or
  ``db query → s3://bucket`` (bulk data moving off-box). They turn "a file, a
  domain and a command were mentioned" into "this data went to that
  destination via this command", which is what an analyst needs to judge
  ingress and exfiltration.
* **Exposed secrets** — credential-shaped strings (private-key headers, cloud
  key prefixes, ``password=`` assignments, bearer tokens) appearing in
  messages, tool arguments or tool output. Standard secret-scanning; covers
  accidental data loss by the user or the agent.
* **Sensitive resource access** — reads of well-known credential locations
  (``/etc/shadow``, ``~/.ssh/id_rsa``, ``.aws/credentials``, ``.env`` …).
* **Risky operations** — destructive or history-clearing commands that an
  analyst reviews for accidental or intentional data loss.
* **Watchlist** — anything the analyst chose to flag.

The output is a list of :class:`Finding` objects that the graph builder turns
into flagged nodes and ``dataflow`` edges and the UI lists for triage.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from typing import Any

SEVERITIES = ["info", "low", "medium", "high", "critical"]
SEV_RANK = {s: i for i, s in enumerate(SEVERITIES)}

# Analysis buckets shown as legend/filter groups in the UI.
CATEGORIES = {
    "ingress": "Inbound download",
    "egress": "Outbound / exfiltration",
    "execution": "Fetch-and-run",
    "credential_access": "Sensitive resource access",
    "data_exposure": "Exposed secret",
    "destruction": "Destructive / data loss",
    "evasion": "Log / history clearing",
    "watchlist": "Watchlist match",
    "ioc": "IOC list hit",
    "keyword": "Keyword list hit",
}


@dataclass
class Finding:
    conv: str
    event: str
    category: str
    rule: str
    label: str
    severity: str
    detail: str
    entities: list[str] = field(default_factory=list)          # entity keys
    chain: list[tuple[str, str, str]] = field(default_factory=list)  # (src_key, action, dst_key)
    value: str = ""  # what matched: an indicator, keyword or watchlist text

    def to_json(self) -> dict[str, Any]:
        return {
            "conv": self.conv, "event": self.event, "category": self.category, "rule": self.rule,
            "label": self.label, "severity": self.severity, "detail": self.detail,
            "entities": self.entities, "chain": [list(c) for c in self.chain], "value": self.value,
        }


# --------------------------------------------------------------------------
# Entity roles: which extracted categories are remote destinations vs. local
# data, derived from the ordinary NLP categories (no security-specific NER).
REMOTE_CATEGORIES = {"url", "domain", "ip", "host", "email", "cloud"}
LOCAL_CATEGORIES = {"file_path"}
SECRET_CATEGORIES = {"credential"}

# Well-known credential / secret file locations (basename or path fragment).
SENSITIVE_PATHS = re.compile(
    r"(?:^|[\\/])(?:shadow|gshadow|sam|ntds\.dit|id_rsa|id_dsa|id_ecdsa|id_ed25519|"
    r"\.aws[\\/]credentials|\.ssh[\\/][\w.-]+|\.env(?:\.\w+)?|\.netrc|\.pgpass|\.git-credentials|"
    r"\.npmrc|\.kube[\\/]config|\.docker[\\/]config\.json|credentials\.json|service[-_]account\.json|"
    r"secrets?\.(?:ya?ml|json|txt)|wp-config\.php|web\.config|htpasswd)\b|/etc/passwd\b",
    re.I,
)

# Standard secret-shaped strings (as used by defensive secret scanners).
SECRET_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"), "critical"),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "high"),
    ("gcp_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), "high"),
    ("github_token", re.compile(r"\bgh[pousr]_[0-9A-Za-z]{36,}\b"), "high"),
    ("slack_token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b"), "high"),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{20,}\b"), "medium"),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{6,}\b"), "medium"),
    ("password_assignment", re.compile(
        r"(?i)\b(?:password|passwd|pwd|secret|api[_-]?key|token|access[_-]?key)\b\s*[=:]\s*"
        r"['\"]?[^\s'\"]{6,}['\"]?"), "medium"),
]

# Destructive / evasion command shapes (ordinary admin operations, flagged so an
# analyst can review accidental or intentional data loss). Structural, not a
# catalogue of attack tooling.
RISKY_OPS: list[tuple[str, str, str, re.Pattern[str]]] = [
    ("destruction", "Recursive force delete", "high",
     re.compile(r"\brm\s+(?:-\w*\s+)*-(?:\w*r\w*f|\w*f\w*r)\w*\b|\brm\s+-[rf]\w*\s+-[rf]\w*")),
    ("destruction", "Disk / device overwrite", "critical",
     re.compile(r"\bdd\b[^\n]*\bof=/dev/|\bmkfs\.\w+|\b>\s*/dev/sd[a-z]")),
    ("destruction", "Drop or truncate database", "high",
     re.compile(r"\b(?:DROP\s+(?:TABLE|DATABASE|SCHEMA)|TRUNCATE\s+TABLE)\b", re.I)),
    ("destruction", "Unscoped delete", "medium",
     re.compile(r"\bDELETE\s+FROM\s+\w+\s*(?:;|$)", re.I)),
    ("destruction", "Recursive object-store delete", "high",
     re.compile(r"\b(?:aws\s+s3|gsutil|az\s+storage)\b[^\n]*\b(?:rm|rb|delete)\b[^\n]*(?:--recursive|-r\b|/\*)")),
    ("destruction", "Force history rewrite / push", "medium",
     re.compile(r"\bgit\s+push\b[^\n]*(?:--force\b|-f\b|\+\w)")),
    ("destruction", "Recursive world-writable permissions", "medium",
     re.compile(r"\bchmod\s+-R\s+0?777\b")),
    ("evasion", "Shell history cleared", "high",
     re.compile(r"\bhistory\s+-c\b|\bunset\s+HISTFILE\b|(?:rm|truncate|>\s*)[^\n]*\.bash_history\b|"
                r"\bexport\s+HISTSIZE=0\b")),
    ("evasion", "System log cleared", "high",
     re.compile(r"(?::>|>\s*|\btruncate\b[^\n]*)/var/log/\S+|\bjournalctl\b[^\n]*--vacuum|\bwevtutil\s+cl\b", re.I)),
]

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


@dataclass
class _Mention:
    key: str
    category: str
    text: str

    @property
    def sensitive(self) -> bool:
        return bool(SENSITIVE_PATHS.search(self.text) or SENSITIVE_PATHS.search(self.key))


# Bulky text arguments of file-writing, editing and messaging tools (Write, Edit,
# apply_patch, message …): the text is data being written, not a command.
_BULK_ARGS = {"content", "contents", "new_string", "old_string", "old_str", "new_str", "file_text", "text", "body",
              "patch", "edits", "new_source", "diff", "description", "prompt", "message", "instructions"}


def command_text(arguments: dict[str, Any] | None, tool_name: str | None) -> str:
    """Best-effort single command string from a tool call's arguments."""
    if not arguments:
        return tool_name or ""
    preferred = ("command", "cmd", "commands", "script", "code", "query", "sql", "input", "args", "arg")
    for key in preferred:
        if key in arguments and isinstance(arguments[key], (str, list)):
            v = arguments[key]
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


def _http_segments(cmd: str) -> str:
    """The parts of a command line run by an HTTP client (upload flags only mean
    something there: ``grep -F`` or ``cut -d`` are not uploads)."""
    return "\n".join(seg for seg in _SEGMENTS.split(cmd) if base_command(seg.strip()).lower() in _HTTP_CLIENTS)


def base_command(cmd: str) -> str:
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


def _write_targets(cmd: str) -> set[str]:
    out: set[str] = set()
    for rx in (_WRITE_FLAG, _REDIRECT):
        out.update(m.group(1).strip("'\"") for m in rx.finditer(cmd))
    return {t for t in out if t and not t.startswith("-")}


def _matches(name: str, targets: set[str]) -> bool:
    base = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return any(t == name or t.endswith(name) or name.endswith(t) or t.rsplit("/", 1)[-1] == base for t in targets)


@dataclass
class _Watch:
    label: str
    severity: str
    pattern: re.Pattern[str]


def parse_watchlist(spec: str, default_sev: str = "medium") -> list[_Watch]:
    out: list[_Watch] = []
    for raw in (spec or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        sev = default_sev
        m = re.match(r"^\[(info|low|medium|high|critical)\]\s*(.*)$", line, re.I)
        if m:
            sev, line = m.group(1).lower(), m.group(2)
        label, _, expr = line.partition(":")
        expr = expr.strip() or label.strip()
        try:
            out.append(_Watch(label.strip() or expr, sev, re.compile(expr, re.I)))
        except re.error:
            out.append(_Watch(label.strip() or expr, sev, re.compile(re.escape(expr), re.I)))
    return out


class SecurityScanner:
    def __init__(self, cfg: dict[str, Any]):
        self.detect_flows = cfg.get("sec_dataflow", True)
        self.detect_secrets = cfg.get("sec_secrets", True)
        self.detect_risky = cfg.get("sec_risky_ops", True)
        self.sensitive_paths = cfg.get("sec_sensitive_paths", True)
        self.min_rank = SEV_RANK.get(cfg.get("sec_min_severity", "low"), 1)
        self.scan_results = cfg.get("sec_scan_results", True)
        self.watchlist = parse_watchlist(cfg.get("security_watchlist", ""))

    def _keep(self, severity: str) -> bool:
        return SEV_RANK.get(severity, 0) >= self.min_rank

    # -- per event -------------------------------------------------------
    def _mentions(self, event, analysis) -> list[_Mention]:
        out: list[_Mention] = []
        for pid in event.paragraphs:
            res = analysis.get(pid)
            if not res:
                continue
            for m in res.mentions:
                out.append(_Mention(m.key, m.category, m.text))
        return out

    def _event_text(self, event, paragraphs) -> str:
        return "\n".join(paragraphs[pid].text for pid in event.paragraphs if pid in paragraphs)

    def scan_event(self, event, paragraphs, analysis) -> list[Finding]:
        findings: list[Finding] = []
        mentions = self._mentions(event, analysis)
        text = self._event_text(event, paragraphs)

        if event.type == "tool_call":
            findings += self._tool_call(event, mentions)
        # secrets / sensitive resources apply to any event (user paste, agent
        # echo, or command output). Skip tool_result output when configured.
        if not (event.type == "tool_result" and not self.scan_results):
            findings += self._secrets(event, text, mentions)
        findings += self._watch(event, text)
        return [f for f in findings if self._keep(f.severity)]

    def _tool_call(self, event, mentions: list[_Mention]) -> list[Finding]:
        findings: list[Finding] = []
        cmd = strip_heredocs(command_text(event.arguments, event.tool_name))
        base = base_command(cmd) or (event.tool_name or "")
        # only entities that are part of the command itself (not a heredoc body,
        # a description or file content being written)
        mentions = [m for m in mentions if m.text in cmd]

        remotes = [m for m in mentions if m.category in REMOTE_CATEGORIES
                   and not re.match(r"^(?:https?://)?(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[?::1\]?)\b", m.key)]
        # device files (/dev/null, /dev/stdout …) are not real data sources or sinks
        locals_ = [m for m in mentions if m.category in LOCAL_CATEGORIES and not m.key.startswith("/dev/")]
        secrets = [m for m in mentions if m.category in SECRET_CATEGORIES]

        if self.detect_risky:
            for cat, label, sev, rx in RISKY_OPS:
                if rx.search(cmd):
                    findings.append(Finding(event.conv, event.id, cat, "op." + label.lower().replace(" ", "_"),
                                            label, sev, f"`{base or 'command'}`: {label.lower()}",
                                            entities=[m.key for m in locals_]))

        if self.sensitive_paths:
            for m in locals_:
                if m.sensitive:
                    findings.append(Finding(event.conv, event.id, "credential_access", "sensitive_path",
                                            "Sensitive file accessed", "high",
                                            f"`{base or 'command'}` touched {m.text}", entities=[m.key]))

        if not self.detect_flows:
            return findings

        writes = _write_targets(cmd)
        local_writes = [m for m in locals_ if _matches(m.text, writes) or _matches(m.key, writes)]
        local_reads = [m for m in locals_ if m not in local_writes]
        sources = local_reads + secrets  # data that could leave the host
        lower = cmd.lower()
        s3_upload = bool(re.search(r"\b(?:aws\s+s3|gsutil|az\s+storage)\b[^\n]*\b(?:cp|sync|mv|put)\b", lower)) and \
            bool(re.search(r"\b(?:s3|gs)://", lower))
        scp_upload = base in ("scp", "rsync", "sftp") and bool(re.search(r"\S+@\S+:|:\S", cmd)) and bool(local_reads)
        http = _http_segments(cmd)
        has_upload = (bool(_UPLOAD_DATA.search(http)) or bool(_UPLOAD_FILE.search(http)) or bool(_METHOD_WRITE.search(http))
                      or s3_upload or scp_upload)
        has_download = bool(_DOWNLOADER.search(cmd)) and (bool(writes) or "clone" in lower)
        fetch_run = bool(_FETCH_TO_INTERP.search(cmd))
        remote_exec = bool(_REMOTE_EXEC.match(cmd.strip())) or base in ("ssh", "sshpass")
        # a bulk data read (DB query / dump / archive) is a data source even when
        # no file entity is present, e.g. `psql -c 'SELECT …' | curl -T - host`
        data_read = bool(re.search(
            r"\bSELECT\b[^\n]+\bFROM\b|\b(?:mysqldump|pg_dump(?:all)?|mongoexport|mongodump|sqlite3)\b|"
            r"\btar\s+-?\w*c|\bzip\s+-r\b", cmd, re.I))
        net_send = has_upload or bool(_PIPE_TO_NET.search(cmd))

        emitted: set[tuple] = set()

        def chain(finds_cat, rule, label, sev, pairs, ents, detail):
            sig = (rule, tuple(sorted(ents)), tuple(pairs))
            if sig in emitted:
                return
            emitted.add(sig)
            findings.append(Finding(event.conv, event.id, finds_cat, rule, label, sev, detail,
                                    entities=ents, chain=pairs))

        # Fetch-and-run: remote content executed by an interpreter.
        if fetch_run and remotes:
            for r in remotes:
                chain("execution", "fetch_and_run", "Downloaded content executed", "high",
                      [(r.key, "run", event.id)], [r.key],
                      f"`{base}` piped remote content from {r.text} into an interpreter")

        # Exfiltration: local/secret data (or a bulk data read) + a genuine
        # network egress in one command.
        if net_send and remotes and (sources or data_read):
            sev = "critical" if secrets else "high"
            if sources:
                for s in sources:
                    for r in remotes:
                        chain("egress", "exfiltration", "Data leaving the host", sev,
                              [(s.key, "sends to", r.key)], [s.key, r.key],
                              f"`{base}` moves {s.text} to {r.text}")
            else:  # data read with no file entity: anchor the chain at the tool call
                for r in remotes:
                    chain("egress", "exfiltration", "Query results leaving the host", sev,
                          [(event.id, "sends to", r.key)], [r.key],
                          f"`{base}` sends bulk query/archive output to {r.text}")

        # Ingress: remote source written to local disk.
        elif has_download and remotes:
            targets = local_writes  # only files the command actually writes
            if targets:
                for r in remotes:
                    for t in targets:
                        chain("ingress", "download", "File downloaded to disk", "medium",
                              [(r.key, "downloads to", t.key)], [r.key, t.key],
                              f"`{base}` downloads {r.text} to {t.text}")
            else:
                for r in remotes:
                    chain("ingress", "download", "Remote resource fetched", "low",
                          [], [r.key], f"`{base}` fetched {r.text}")

        # Plain remote command execution.
        elif remote_exec and remotes:
            for r in remotes:
                chain("execution", "remote_exec", "Remote command execution", "low",
                      [], [r.key], f"`{base}` runs a command on {r.text}")

        return findings

    def _secrets(self, event, text: str, mentions: list[_Mention]) -> list[Finding]:
        findings: list[Finding] = []
        if self.detect_secrets and text:
            where = {"tool_result": "tool output", "user": "a user message",
                     "tool_call": "a tool argument"}.get(event.type, "the conversation")
            for name, rx, sev in SECRET_PATTERNS:  # first match of each pattern
                m = rx.search(text)
                if not m:
                    continue
                snippet = m.group(0)
                redacted = snippet[:6] + "…" if len(snippet) > 8 else snippet
                findings.append(Finding(event.conv, event.id, "data_exposure", "secret." + name,
                                        "Secret exposed", sev,
                                        f"{name.replace('_', ' ')} in {where}: `{redacted}`"))
        if self.sensitive_paths and event.type != "tool_call":
            for m in mentions:
                if m.category in LOCAL_CATEGORIES and m.sensitive:
                    findings.append(Finding(event.conv, event.id, "credential_access", "sensitive_path",
                                            "Sensitive file referenced", "medium",
                                            f"reference to {m.text}", entities=[m.key]))
        return findings

    def _watch(self, event, text: str) -> list[Finding]:
        out: list[Finding] = []
        for w in self.watchlist:
            m = w.pattern.search(text)
            if m:
                out.append(Finding(event.conv, event.id, "watchlist", "watch." + w.label.lower().replace(" ", "_"),
                                   f"Watchlist: {w.label}", w.severity, f"matched `{m.group(0)[:60]}`",
                                   value=m.group(0)[:60]))
        return out


def scan(conversations, events_by_conv, paragraphs, analysis, cfg) -> list[Finding]:
    """Scan every event and return findings in conversation/event order."""
    scanner = SecurityScanner(cfg)
    findings: list[Finding] = []
    for conv in conversations:
        for event in events_by_conv.get(conv.id, []):
            findings.extend(scanner.scan_event(event, paragraphs, analysis))
    return findings
