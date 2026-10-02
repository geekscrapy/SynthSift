"""Hits from the IOC / keyword lists (Settings → IOC & keyword lists): every value of a list found in a turn."""

from __future__ import annotations

from typing import Any, Iterable

from .base import SEVERITIES, CorpusCheck, Finding, register


@register
class ListHits(CorpusCheck):
    name = "list_hits"
    label = "List hit"
    description = ("Indicators and keywords from your IOC / keyword lists, found by the IOC module. Each hit has its list "
                   "entry's severity: the list's severity column, or the IOC module's default.")
    category = "ioc"
    severity = "medium"
    order = 950
    severity_from = "each list entry's own"
    needs_modules = ("ioc",)

    def run_corpus(self, cctx: Any) -> Iterable[Finding]:
        if not cctx.cfg.get("ioc_findings", True):
            return
        by_hash: dict[str, list] = {}
        for p in cctx.paragraphs.values():
            by_hash.setdefault(cctx.hash_of(p.id), []).append(p)
        seen: set[tuple] = set()
        hits = cctx.storage.query('SELECT para_hash, start, "end", text, value, kind, list, label, severity FROM x_ioc_hits '
                                  'ORDER BY para_hash, start')
        for h, s, e, _text, value, kind, lst, label, sev in hits:
            for p in by_hash.get(h, []):
                key = (p.event, lst, value)
                if key in seen:
                    continue
                seen.add(key)
                res = cctx.analysis.get(p.id)
                ents = [m.key for m in res.mentions if m.start < e and s < m.end] if res else []
                shown = p.text[s:e]
                what = f"{kind} " if kind != "keyword" else ""
                yield self.make(p.conv, p.event,
                                f"{what}`{shown}` is on {lst}" + (f" ({value})" if value.lower() != shown.lower() else ""),
                                category="keyword" if kind == "keyword" else "ioc", rule=f"ioc.{lst}",
                                label=f"List hit: {label}", severity=sev if sev in SEVERITIES else self.severity,
                                entities=ents, value=value)
