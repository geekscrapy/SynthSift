"""The analyst watchlist: your own terms or patterns, one per line in Settings, matched against every turn."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from ..fields import Field
from .base import Check, EventContext, Finding, register


@dataclass
class Watch:
    label: str
    severity: str
    pattern: re.Pattern[str]


def parse_watchlist(spec: str, default_sev: str = "medium") -> list[Watch]:
    """``Label: <regex>`` per line, optional ``[severity]`` prefix; a bad regex is matched as plain text."""
    out: list[Watch] = []
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
            out.append(Watch(label.strip() or expr, sev, re.compile(expr, re.I)))
        except re.error:
            out.append(Watch(label.strip() or expr, sev, re.compile(re.escape(expr), re.I)))
    return out


@register
class Watchlist(Check):
    name = "watchlist"
    label = "Watchlist match"
    description = ("Your own terms or patterns (Settings → Security analysis → Analyst watchlist). Its severity is the "
                   "default for lines without a [severity] prefix.")
    category = "watchlist"
    severity = "medium"
    order = 900
    options = (
        Field("security_watchlist", "Analyst watchlist", "textarea",
              "# one per line:  Label: <regex>    (optional [severity] prefix)\n"
              "# [high] Data staging: base64\\s+-d\n"
              "# [medium] Package install: \\bpip\\s+install\\b\n",
              "parse", "",
              "Your own terms or patterns to flag. Matched (case-insensitive) against every message, argument and result."),
    )

    def __init__(self, cfg):
        super().__init__(cfg)
        # the check's severity is the default for lines without a [severity] prefix
        self.watches = parse_watchlist(cfg.get("security_watchlist", ""), self.severity)

    def run(self, ctx: EventContext) -> Iterable[Finding]:
        for w in self.watches:
            m = w.pattern.search(ctx.text)
            if m:
                yield self.finding(ctx, f"matched `{m.group(0)[:60]}`", rule="watch." + w.label.lower().replace(" ", "_"),
                                   label=f"Watchlist: {w.label}", severity=w.severity, value=m.group(0)[:60])
