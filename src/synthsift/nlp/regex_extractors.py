"""Pattern based extractors for technical objects spaCy does not know about.

Order in :data:`REGEX_DEFS` is priority: when two matches overlap the earlier
definition wins (a URL beats the domain inside it, a path beats a filename).
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Callable, Iterable

_TLDS = (
    "com|org|net|io|dev|ai|app|edu|gov|mil|co|uk|de|fr|jp|cn|ru|nl|se|no|fi|es|it|pl|ch|at|be|"
    "info|biz|xyz|cloud|tech|me|us|ca|au|in|br|local|internal|lan|corp|onion|sh|gg|ly|tv"
)
# Extensions that are unambiguous even for a bare "name.ext" …
_FILE_EXT = (
    "py|pyi|ipynb|js|mjs|cjs|jsx|ts|tsx|json|jsonl|yaml|yml|toml|ini|cfg|conf|md|rst|txt|csv|tsv|"
    "log|sh|bash|zsh|ps1|bat|html|htm|css|scss|sql|sqlite|go|rs|java|kt|scala|cpp|hpp|rb|php|"
    "swift|xml|svg|png|jpe?g|gif|webp|pdf|docx|xlsx?|pptx?|zip|tar|gz|tgz|bz2|xz|7z|whl|"
    "exe|dll|dylib|iso|dmg|deb|rpm|apk|mp3|mp4|wav|mov|pem|crt|tf|tfvars|proto|"
    "graphql|vue|svelte|dockerfile|gradle|plist|service|lockb"
)
# … and ones that collide with code ("p.c", "f.key", "c.doc") – only inside a path
_WEAK_EXT = "c|h|cc|cs|m|r|pl|lua|so|bin|img|key|pub|env|lock|doc|db|rules|socket|timer|makefile|pom"

_TRAILING = ".,;:!?)]}>\"'`"


@dataclass(frozen=True)
class RegexDef:
    name: str
    category: str
    pattern: re.Pattern[str]
    default: bool = True
    group: int = 0
    validate: Callable[[str], bool] | None = None
    literal: bool = True  # case-sensitive key
    # (text, start, end) -> keep?  For checks that need the surrounding text.
    validate_ctx: Callable[[str, int, int], bool] | None = None


def _valid_ipv6(s: str) -> bool:
    try:
        ipaddress.IPv6Address(s.split("%")[0])
        return s.count(":") >= 2
    except ValueError:
        return False


def _valid_ipv4(s: str) -> bool:
    host = s.split("/")[0].split(":")[0]
    try:
        ipaddress.IPv4Address(host)
    except ValueError:
        return False
    # "0.1.2.3" is more likely a version number than an address
    return host == "0.0.0.0" or not host.startswith("0.")


def _not_decimal_date(s: str) -> bool:
    return not re.fullmatch(r"\d+\.\d+", s)


_STOP_CODE = {"e.g", "i.e", "etc", "vs"}

REGEX_DEFS: list[RegexDef] = [
    RegexDef("url", "url", re.compile(r"\b(?:https?|ftp|wss?|file|s3|gs|git|ssh)://[^\s<>\"'`\]}|]+", re.I)),
    RegexDef("email", "email", re.compile(r"\b[\w.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"), literal=False),
    RegexDef("aws_arn", "cloud", re.compile(r"\barn:aws[\w-]*:[\w-]+:[\w-]*:\d*:[\w\-/:.*]+")),
    RegexDef("cloud_region", "cloud", re.compile(
        r"\b(?:us|eu|ap|sa|ca|me|af|il|mx)-(?:gov-)?(?:north|south|east|west|central|northeast|southeast|"
        r"northwest|southwest)\d?-\d\b|\b(?:europe|asia|us|australia|southamerica|northamerica)-"
        r"(?:north|south|east|west|central|northeast|southeast)\d\b"), literal=False),
    RegexDef("uuid", "uuid", re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")),
    RegexDef("hash", "hash", re.compile(r"\b(?:[a-fA-F0-9]{64}|[a-fA-F0-9]{40}|[a-fA-F0-9]{32})\b"),
             validate=lambda s: bool(re.search(r"[a-fA-F]", s)) and bool(re.search(r"\d", s))),
    RegexDef("private_key", "credential", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----")),
    RegexDef("aws_key", "credential", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    RegexDef("gcp_key", "credential", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    RegexDef("github_token", "credential", re.compile(r"\bgh[pousr]_[0-9A-Za-z]{36,}\b")),
    RegexDef("slack_token", "credential", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b")),
    RegexDef("jwt", "credential", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{6,}\b")),
    RegexDef("cve", "cve", re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.I), literal=False),
    RegexDef("mac", "mac", re.compile(r"\b[0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5}\b"), literal=False),
    RegexDef("ipv6", "ip", re.compile(r"(?<![\w:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?:%\w+)?(?![\w:])"),
             validate=_valid_ipv6, literal=False),
    RegexDef("ipv4", "ip", re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?(?::\d{1,5})?(?!\w|\.\d)"),
             validate=_valid_ipv4),
    RegexDef("host_port", "host", re.compile(
        rf"\b(?:localhost|(?:[a-z0-9-]+\.)+(?:{_TLDS})|[a-z][a-z0-9-]*-(?:\d+|db|api|srv|host|node))" r":\d{2,5}\b", re.I), literal=False),
    RegexDef("windows_path", "file_path", re.compile(r"\b[A-Za-z]:\\(?:[^\\\s<>:\"|?*]+\\)*[^\\\s<>:\"|?*]*")),
    RegexDef("unix_path", "file_path", re.compile(
        # "@" may precede a path in curl's -d @file / -F 'f=@file', but not user@host/…
        r"(?<![\w.:/<*-])(?<![\w.:]@)(?:~|\.{1,2})?/(?:[\w.@+%-]+/)*[\w.@+%-]*[\w@+%-](?:/)?"),
        validate=lambda s: len(s) > 2 and s.count("/") >= 1 and not re.fullmatch(r"/\d+", s)),
    RegexDef("relative_path", "file_path", re.compile(
        rf"(?<![\w/.<-])[\w.-]+(?:/[\w.@-]+)+\.(?:{_FILE_EXT}|{_WEAK_EXT})\b", re.I)),
    RegexDef("filename", "file_path", re.compile(
        rf"(?<![\w/.-])[\w-]+(?:\.[\w-]+)*\.(?:{_FILE_EXT})\b(?![\w/(-])", re.I),
        validate=lambda s: not re.fullmatch(r"\d+(\.\d+)*", s) and len(s.split(".")[0]) >= 2),
    RegexDef("dockerfile", "file_path", re.compile(r"\b(?:Dockerfile|Makefile|Jenkinsfile|Procfile|Gemfile|Vagrantfile)\b")),
    RegexDef("domain", "domain", re.compile(
        rf"(?<![\w@./-])(?:[a-z0-9](?:[a-z0-9-]{{0,61}}[a-z0-9])?\.)+(?:{_TLDS})\b(?![\w-]|\.\w)", re.I),
        literal=False),
    RegexDef("iso_datetime", "date", re.compile(
        r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?\b"), literal=False),
    RegexDef("version", "version", re.compile(r"(?<![\w.])v?\d+\.\d+\.\d+(?:[-+][\w.]+)?\b|\bv\d+(?:\.\d+)+\b"),
             validate=_not_decimal_date),
    RegexDef("error_type", "error", re.compile(r"\b[A-Z][A-Za-z]*(?:Error|Exception|Fault|Failure)\b")),
    RegexDef("env_var", "env_var", re.compile(
        r"\$\{[A-Za-z_][A-Za-z0-9_]*\}|\$[A-Za-z_][A-Za-z0-9_]*\b|\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")),
    RegexDef("phone", "phone", re.compile(r"(?<![\w+])(?:\+\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b"),
             default=False, literal=False),
    RegexDef("coordinates", "geo", re.compile(r"(?<![\w.])-?\d{1,2}\.\d{3,}\s*,\s*-?\d{1,3}\.\d{3,}\b")),
    RegexDef("mention", "mention", re.compile(r"(?<![\w.@])@[A-Za-z][\w-]{1,38}\b"), literal=False,
             validate_ctx=lambda text, s, e: text[text.rfind("\n", 0, s) + 1:s].strip() != ""),
    RegexDef("hex_color", "color", re.compile(r"(?<![\w&])#(?:[0-9A-Fa-f]{8}|[0-9A-Fa-f]{6}|[0-9A-Fa-f]{3})\b"), literal=False),
    RegexDef("hashtag", "tag", re.compile(r"(?<![\w&#])#[A-Za-z][\w-]{1,50}\b"), literal=False),
    RegexDef("inline_code", "code", re.compile(r"`([^`\n]{2,80})`"), group=1,
             validate=lambda s: s.strip().lower() not in _STOP_CODE),
    RegexDef("function_call", "code", re.compile(r"\b[A-Za-z_][\w]*(?:\.[A-Za-z_]\w*)*\(\)")),
    RegexDef("snake_case", "code", re.compile(r"(?<![\w.$-])[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b(?![-/])")),
    RegexDef("dotted_identifier", "code", re.compile(
        r"(?<![\w./-])[a-z_][a-z0-9_]*(?:\.[a-z_][A-Za-z0-9_]*)+\b(?![-/(]|\.\w)"),
        validate=lambda s: s.lower() not in _STOP_CODE and len(s) > 4),
    RegexDef("camel_case", "code", re.compile(r"\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+\b"), default=False),
]

REGEX_BY_NAME = {d.name: d for d in REGEX_DEFS}


@dataclass
class Match:
    start: int
    end: int
    text: str
    category: str
    source: str
    literal: bool


def clean_span(text: str, start: int, end: int) -> tuple[int, int]:
    """Trim trailing punctuation that regexes greedily swallow."""
    pairs = {")": "(", "]": "[", "}": "{"}
    while end > start and text[end - 1] in _TRAILING:
        # keep balanced closers: .../Foo_(bar), ${HOME}
        ch = text[end - 1]
        if ch in pairs and text[start:end].count(pairs[ch]) >= text[start:end].count(ch):
            break
        end -= 1
    while start < end and text[start] in "'\"`(<[":
        start += 1
    return start, end


def compile_custom(spec: str) -> list[RegexDef]:
    """Parse the 'category: regex' textarea from settings."""
    out = []
    for i, line in enumerate(spec.splitlines()):
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        cat, pat = line.split(":", 1)
        try:
            out.append(RegexDef(f"custom_{i}", cat.strip().lower().replace(" ", "_"), re.compile(pat.strip())))
        except re.error:
            continue
    return out


def find_all(text: str, defs: Iterable[RegexDef]) -> list[Match]:
    """Return non-overlapping matches, earlier definitions taking priority."""
    taken: list[tuple[int, int]] = []
    out: list[Match] = []
    for d in defs:
        for m in d.pattern.finditer(text):
            s, e = m.span(d.group)
            if s < 0:
                continue
            s, e = clean_span(text, s, e)
            if e - s < 2:
                continue
            frag = text[s:e]
            if d.validate and not d.validate(frag):
                continue
            if d.validate_ctx and not d.validate_ctx(text, s, e):
                continue
            if any(s < te and ts < e for ts, te in taken):
                continue
            taken.append((s, e))
            out.append(Match(s, e, frag, d.category, f"regex:{d.name}", d.literal))
    out.sort(key=lambda m: m.start)
    return out
