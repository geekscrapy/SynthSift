"""Security checks: small plugins that each flag one kind of behaviour.

Every module in this package is imported on load so its ``@register``
decorators run – drop a new file in here (or a ``.py`` file in
``<data dir>/checks/``) to add a check. :func:`scan` runs the switched-on
checks over a corpus and returns their findings. ``CHECKS.md`` (next to the
README) is the guide to writing one.
"""

from __future__ import annotations

import importlib
import pkgutil
import re
from dataclasses import dataclass
from typing import Any, Callable

from .base import (  # noqa: F401
    CATEGORIES,
    SEV_RANK,
    SEVERITIES,
    Check,
    CorpusCheck,
    EventContext,
    Finding,
    Mention,
    all_checks,
    builtin_checks,
    iter_options,
    load_user_checks,
    register,
    severity_overrides,
    sources_digest,
)

for _mod in pkgutil.iter_modules(__path__):
    if _mod.name not in ("base", "command"):
        importlib.import_module(f"{__name__}.{_mod.name}")


def active_checks(cfg: dict[str, Any]) -> list[Check]:
    """The checks that run with these settings, in run order: every registered check that is ``enabled`` and not
    switched off in Settings (``sec_checks_off``)."""
    off = set(cfg.get("sec_checks_off") or ())
    return [cls(cfg) for name, cls in all_checks().items() if cls.enabled and name not in off]


@dataclass
class CorpusView:
    """What a :class:`CorpusCheck` sees: the module's storage and switched-on modules plus the corpus."""

    storage: Any
    enabled: set[str]
    hash_of: Callable[[str], str]
    paragraphs: dict[str, Any]
    analysis: dict[str, Any]
    cfg: dict[str, Any]


def scan(conversations, events_by_conv, paragraphs, analysis, cfg: dict[str, Any], *, corpus: Any = None,
         errors: dict[str, str] | None = None) -> list[Finding]:
    """Run the active checks over every turn (conversation and turn order), then the corpus checks when ``corpus``
    (the module's corpus context) is given, and drop findings below ``sec_min_severity``. An exception in a user
    check is recorded in ``errors`` (check name → message) and that check skipped for the rest of the scan;
    built-in checks raise."""
    checks = active_checks(cfg)
    turn_checks = [c for c in checks if not isinstance(c, CorpusCheck)]
    broken: set[str] = set()

    def failed(chk: Check, exc: Exception) -> None:
        if chk.source == "builtin":
            raise exc
        broken.add(chk.name)
        if errors is not None:
            errors.setdefault(chk.name, f"{type(exc).__name__}: {exc}")

    findings: list[Finding] = []
    for conv in conversations:
        for event in events_by_conv.get(conv.id, []):
            ctx = EventContext(event, paragraphs, analysis, cfg)
            for chk in turn_checks:
                if chk.name in broken or not chk.applies(ctx):
                    continue
                try:
                    ctx.found.extend(chk.run(ctx) or ())
                except Exception as exc:  # noqa: BLE001 - see failed()
                    failed(chk, exc)
            findings.extend(ctx.found)

    if corpus is not None:
        view = CorpusView(corpus.storage, set(corpus.enabled), corpus.hash_of, paragraphs, analysis, cfg)
        for chk in checks:
            if isinstance(chk, CorpusCheck) and set(chk.needs_modules) <= view.enabled:
                try:
                    findings.extend(chk.run_corpus(view) or ())
                except Exception as exc:  # noqa: BLE001 - see failed()
                    failed(chk, exc)

    min_rank = SEV_RANK.get(cfg.get("sec_min_severity", "low"), 1)
    return [f for f in findings if SEV_RANK.get(f.severity, 0) >= min_rank]


def describe(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Every registered check for the Settings page and the CLI."""
    off = set(cfg.get("sec_checks_off") or ())
    sev_override = severity_overrides(cfg)
    builtins = builtin_checks()
    out = []
    for name, cls in all_checks().items():
        out.append({
            "name": name, "label": cls.label or name, "description": _clean(cls.description or cls.__doc__ or ""),
            "category": cls.category, "category_label": CATEGORIES.get(cls.category, cls.category),
            "severity": sev_override.get(name, cls.severity), "default_severity": cls.severity,
            "severity_from": cls.severity_from,
            "events": list(cls.events), "order": cls.order, "corpus": issubclass(cls, CorpusCheck),
            "source": cls.source, "overrides_builtin": cls.source != "builtin" and name in builtins,
            "parked": not cls.enabled, "on": cls.enabled and name not in off,
        })
    return out


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
