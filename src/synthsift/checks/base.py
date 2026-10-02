"""The check SDK: what a security check is, what it sees and what it returns.

A check is a small class that looks at one turn (``Check``) or at the whole
corpus (``CorpusCheck``) and yields :class:`Finding` objects. Checks are
registered with ``@register``; the built-in ones live in this package (one
module per theme) and analysts can add or override checks by dropping a
``.py`` file into ``<data dir>/checks/``. See ``CHECKS.md`` for the guide.
"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import re
import sys
import threading
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any, ClassVar, Iterable, Iterator

from ..fields import Field

SEVERITIES = ["info", "low", "medium", "high", "critical"]
SEV_RANK = {s: i for i, s in enumerate(SEVERITIES)}

#: finding categories, the legend / filter groups in the UI; a check may add its own (``Check.categories``)
CATEGORIES: dict[str, str] = {
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

# Entity roles: which extracted categories are remote destinations, local data or secrets – derived from the
# ordinary NLP categories, so checks need no security-specific entity extraction.
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


@dataclass
class Finding:
    """One flagged thing: where (conversation, turn), what (category, rule, label, severity, detail) and which
    entities it involves. ``chain`` holds (source, action, destination) links drawn as dataflow edges."""

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


@dataclass(frozen=True)
class Mention:
    """An entity the NLP found in the turn: its normalised key, category (``file_path``, ``url`` …) and surface text."""

    key: str
    category: str
    text: str

    @property
    def remote(self) -> bool:
        return self.category in REMOTE_CATEGORIES

    @property
    def local(self) -> bool:
        return self.category in LOCAL_CATEGORIES

    @property
    def secret(self) -> bool:
        return self.category in SECRET_CATEGORIES

    @property
    def sensitive(self) -> bool:
        return bool(SENSITIVE_PATHS.search(self.text) or SENSITIVE_PATHS.search(self.key))


def severity_overrides(cfg: dict[str, Any]) -> dict[str, str]:
    """Severities chosen in Settings: the ``sec_severity`` setting, ``check_name: severity`` per line."""
    out: dict[str, str] = {}
    for line in str(cfg.get("sec_severity") or "").splitlines():
        name, _, sev = line.partition(":")
        name, sev = name.strip(), sev.strip().lower()
        if name and not name.startswith("#") and sev in SEV_RANK:
            out[name] = sev
    return out


class EventContext:
    """What a check sees of one turn. Everything is computed once, when first asked for, and shared by every check."""

    def __init__(self, event: Any, paragraphs: dict[str, Any], analysis: dict[str, Any], cfg: dict[str, Any]):
        self.event = event
        self.conv: str = event.conv
        self.type: str = event.type  # user | assistant | system | thought | tool_call | tool_result
        self.cfg = cfg
        self._paragraphs = paragraphs
        self._analysis = analysis
        #: findings earlier checks made for this turn (in check order)
        self.found: list[Finding] = []

    @cached_property
    def paragraphs(self) -> list[Any]:
        """The turn's paragraphs (``text``, ``role``, ``code``; a tool call's also ``arg``, the argument name)."""
        return [self._paragraphs[pid] for pid in self.event.paragraphs if pid in self._paragraphs]

    @cached_property
    def text(self) -> str:
        """The turn's text (a tool call: its arguments, one per paragraph)."""
        return "\n".join(p.text for p in self.paragraphs)

    @cached_property
    def mentions(self) -> list[Mention]:
        out: list[Mention] = []
        for pid in self.event.paragraphs:
            res = self._analysis.get(pid)
            if res:
                out.extend(Mention(m.key, m.category, m.text) for m in res.mentions)
        return out

    @cached_property
    def command(self) -> Any:
        """A tool call's command line, parsed (see :class:`~synthsift.checks.command.CommandFacts`); None otherwise."""
        if self.type != "tool_call":
            return None
        from .command import CommandFacts  # noqa: PLC0415 - command imports this module
        return CommandFacts.of(self.event, self.mentions)

    def has(self, rule: str) -> bool:
        """An earlier check already reported ``rule`` (or a rule starting with ``rule.``) for this turn."""
        return any(f.rule == rule or f.rule.startswith(rule + ".") for f in self.found)

    def opt(self, key: str, default: Any = None) -> Any:
        return self.cfg.get(key, default)


class Check:
    """A per-turn check. Set the class attributes, implement :meth:`run`, decorate with ``@register``.

    ``run`` yields findings, usually made with :meth:`finding` (which fills in the turn, the category, rule, label
    and severity from the class attributes). It is called for the turn types in ``events`` (empty: every turn).
    """

    #: unique name, used in Settings (to switch it off or change its severity) and as the default rule name
    name: ClassVar[str] = ""
    #: short human title, used as the default finding label
    label: ClassVar[str] = ""
    description: ClassVar[str] = ""
    #: a key of CATEGORIES (or of this check's own ``categories``)
    category: ClassVar[str] = "watchlist"
    #: default severity; on an instance, ``self.severity`` is the one configured in Settings (else this default)
    severity: str = "medium"
    #: set when findings take their severity from elsewhere (Settings then shows this instead of a severity picker)
    severity_from: ClassVar[str] = ""
    #: rule recorded on findings (default: ``name``)
    rule: ClassVar[str] = ""
    #: turn types it looks at; empty: all
    events: ClassVar[tuple[str, ...]] = ()
    #: run order (lower first); later checks can see earlier findings through ``ctx.found`` / ``ctx.has``
    order: ClassVar[int] = 500
    #: False parks the check: it is listed but never runs, whatever Settings say
    enabled: ClassVar[bool] = True
    #: settings this check reads, shown in the Security analysis card
    options: ClassVar[tuple[Field, ...]] = ()
    #: new categories this check introduces: {key: label}
    categories: ClassVar[dict[str, str]] = {}
    #: where it was loaded from (set by the registry): "builtin" or a file path
    source: ClassVar[str] = "builtin"

    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.severity = severity_overrides(cfg).get(self.name, type(self).severity)

    def opt(self, key: str, default: Any = None) -> Any:
        return self.cfg.get(key, default)

    def applies(self, ctx: EventContext) -> bool:
        return not self.events or ctx.type in self.events

    def run(self, ctx: EventContext) -> Iterable[Finding]:
        raise NotImplementedError

    def finding(self, ctx: EventContext, detail: str, **kw: Any) -> Finding:
        """A finding on this turn; the category, rule, label and severity default to the class attributes."""
        return self.make(ctx.conv, ctx.event.id, detail, **kw)

    def make(self, conv: str, event: str, detail: str, *, label: str | None = None, severity: str | None = None,
             rule: str | None = None, category: str | None = None, entities: Iterable[str] = (),
             chain: Iterable[tuple[str, str, str]] = (), value: str = "") -> Finding:
        return Finding(conv, event, category or self.category, rule or self.rule or self.name, label or self.label,
                       severity or self.severity, detail, list(entities), list(chain), value)


class CorpusCheck(Check):
    """A check over the whole corpus (e.g. hits from the IOC / keyword lists): implement :meth:`run_corpus` and make
    findings with :meth:`make`."""

    #: modules that must be enabled for it to run (e.g. ``("ioc",)``)
    needs_modules: ClassVar[tuple[str, ...]] = ()

    def run(self, ctx: EventContext) -> Iterable[Finding]:  # pragma: no cover - corpus checks have no per-turn part
        return ()

    def run_corpus(self, cctx: Any) -> Iterable[Finding]:
        """``cctx`` is a :class:`~synthsift.checks.CorpusView`: ``storage``, ``enabled`` (module names),
        ``hash_of(paragraph_id)``, ``paragraphs``, ``analysis`` and ``cfg``."""
        raise NotImplementedError


# ---------------------------------------------------------------- registry
_BUILTIN: dict[str, type[Check]] = {}
_USER: dict[str, type[Check]] = {}
_loading_user: str | None = None  # the user file being imported, if any
_load_lock = threading.Lock()


def register(cls: type[Check]) -> type[Check]:
    """Class decorator that adds a check (a user file's check replaces a built-in of the same name)."""
    if not cls.name:
        raise ValueError(f"{cls.__name__} needs a name")
    if cls.severity not in SEV_RANK:
        raise ValueError(f"{cls.name}: severity must be one of {SEVERITIES}")
    for key, label in cls.categories.items():
        CATEGORIES.setdefault(key, label)
    if cls.category not in CATEGORIES:
        raise ValueError(f"{cls.name}: unknown category {cls.category!r} (add it to `categories`)")
    if _loading_user:
        cls.source = _loading_user
        _USER[cls.name] = cls
    else:
        _BUILTIN[cls.name] = cls
    return cls


def builtin_checks() -> dict[str, type[Check]]:
    return dict(sorted(_BUILTIN.items(), key=lambda kv: (kv[1].order, kv[0])))


def all_checks() -> dict[str, type[Check]]:
    """Every check, user checks replacing built-ins of the same name, in run order."""
    merged = {**_BUILTIN, **_USER}
    return dict(sorted(merged.items(), key=lambda kv: (kv[1].order, kv[0])))


def load_user_checks(folders: Iterable[Path]) -> list[str]:
    """(Re)load the ``*.py`` checks in these folders (files starting with ``_`` are skipped); returns problems – a
    file that fails to import is left out and the others still load."""
    global _loading_user
    with _load_lock:
        _USER.clear()
        problems: list[str] = []
        for folder in folders:
            if not folder or not Path(folder).is_dir():
                continue
            for path in sorted(Path(folder).glob("*.py")):
                if path.name.startswith("_"):
                    continue
                mod_name = f"synthsift_user_checks.{path.stem}_{hashlib.sha1(str(path).encode()).hexdigest()[:8]}"
                try:
                    spec = importlib.util.spec_from_file_location(mod_name, path)
                    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
                    sys.modules[mod_name] = module
                    _loading_user = str(path)
                    spec.loader.exec_module(module)  # type: ignore[union-attr]
                except Exception as exc:  # noqa: BLE001 - one broken check must not stop the others
                    problems.append(f"{path.name}: {type(exc).__name__}: {exc}")
                finally:
                    _loading_user = None
        return problems


def sources_digest(checks: Iterable[type[Check]]) -> str:
    """A hash of the files these checks are defined in (and of the SDK itself): editing a check makes its findings
    be computed again."""
    files = {Path(__file__), Path(__file__).with_name("command.py")}
    for cls in checks:
        try:
            files.add(Path(inspect.getfile(cls)))
        except (OSError, TypeError):
            pass
    h = hashlib.sha1()
    for path in sorted(files):
        h.update(str(path).encode())
        try:
            h.update(path.read_bytes())
        except OSError:
            pass
    return h.hexdigest()[:16]


def iter_options() -> Iterator[Field]:
    """The settings of the built-in checks, each once."""
    seen: set[str] = set()
    for cls in builtin_checks().values():
        for f in cls.options:
            if f.key not in seen:
                seen.add(f.key)
                yield f
