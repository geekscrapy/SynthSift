"""Security findings: dataflow chains (exfiltration, downloads, fetch-and-run),
exposed secrets, sensitive-file access, destructive operations, the analyst
watchlist – and hits from the IOC / keyword lists.

A corpus-scope module: it reads the resolved entities of every turn and writes
one row per finding.  The scanning logic lives in :mod:`synthsift.nlp.security`.
"""

from __future__ import annotations

import json
from typing import Any

from ..db import Batch, table
from ..fields import Field
from ..nlp import security
from .base import Module, register

FINDINGS = table("a_findings", ("seq", "int"), "conv", "event", "category", "rule", "label", "severity", "detail", ("entities", "json"),
                 ("chain", "json"), index=["conv"], description="One row per security finding")


@register
class SecurityModule(Module):
    name = "security"
    label = "Security analysis"
    description = ("Flags exfiltration, downloads, fetch-and-run, exposed secrets, sensitive-file access, destructive "
                   "commands, watchlist matches and IOC list hits, with source → action → sink chains.")
    kind = "analysis"
    scope = "corpus"
    version = "2"
    needs_corpus = True
    enable_key = "sec_enabled"
    tables = (FINDINGS,)
    uses_settings = ("ioc_findings",)
    options = (
        Field("sec_dataflow", "Dataflow chains", "bool", True, "parse", "",
              "Link source → action → sink within a tool call (e.g. a file leaving to a domain)."),
        Field("sec_secrets", "Secret scanning", "bool", True, "parse", "",
              "Detect private keys, cloud keys, tokens and password assignments in text, arguments and output."),
        Field("sec_scan_results", "Scan tool output for secrets", "bool", True, "parse", "",
              "Also scan command/tool output, not just prompts and arguments."),
        Field("sec_sensitive_paths", "Sensitive-file access", "bool", True, "parse", "",
              "Flag reads of credential locations such as /etc/shadow, ~/.ssh/id_rsa, .aws/credentials, .env."),
        Field("sec_risky_ops", "Risky / destructive operations", "bool", True, "parse", "",
              "Flag recursive deletes, disk overwrites, DROP/TRUNCATE, force pushes and history/log clearing."),
        Field("sec_min_severity", "Minimum severity", "select", "low", "parse", "",
              "Hide findings below this severity.", options=security.SEVERITIES),
        Field("security_watchlist", "Analyst watchlist", "textarea",
              "# one per line:  Label: <regex>    (optional [severity] prefix)\n"
              "# [high] Data staging: base64\\s+-d\n"
              "# [medium] Package install: \\bpip\\s+install\\b\n",
              "parse", "",
              "Your own terms or patterns to flag. Matched (case-insensitive) against every message, argument and result."),
    )

    def dependencies(self, enabled: set[str]) -> list[str]:
        return ["entities"] + (["ioc"] if "ioc" in enabled else [])

    def run_corpus(self, ctx: Any) -> int:
        conversations, events, paragraphs, analysis = ctx.corpus()
        cfg = {**self.cfg, "sec_enabled": True}
        findings = security.scan(conversations, events, paragraphs, analysis, cfg)
        if self.opt("ioc_findings", True) and "ioc" in ctx.enabled:
            findings += self._ioc_findings(ctx, paragraphs, analysis)
        st = ctx.storage
        with st.transaction():
            st.delete("a_findings")
            st.insert(Batch.from_rows(FINDINGS, [(i, f.conv, f.event, f.category, f.rule, f.label, f.severity, f.detail,
                                                  json.dumps(f.entities), json.dumps([list(c) for c in f.chain]))
                                                 for i, f in enumerate(findings)]))
        return len(findings)

    def _ioc_findings(self, ctx, paragraphs, analysis) -> list[security.Finding]:
        min_rank = security.SEVERITIES.index(self.opt("sec_min_severity", "low")) if self.opt("sec_min_severity") else 0
        by_hash: dict[str, list] = {}
        for p in paragraphs.values():
            by_hash.setdefault(ctx.hash_of(p.id), []).append(p)
        out: list[security.Finding] = []
        seen: set[tuple] = set()
        hits = ctx.storage.query('SELECT para_hash, start, "end", text, value, kind, list, label, severity FROM x_ioc_hits '
                                 'ORDER BY para_hash, start')
        for h, s, e, text, value, kind, lst, label, sev in hits:
            if security.SEVERITIES.index(sev) < min_rank:
                continue
            for p in by_hash.get(h, []):
                key = (p.event, lst, value)
                if key in seen:
                    continue
                seen.add(key)
                ents = [m.key for m in analysis.get(p.id).mentions if m.start < e and s < m.end] if p.id in analysis else []
                shown = p.text[s:e]
                what = f"{kind} " if kind != "keyword" else ""
                out.append(security.Finding(p.conv, p.event, "ioc", f"ioc.{lst}", f"List hit: {label}", sev,
                                            f"{what}`{shown}` is on {lst}" + (f" ({value})" if value.lower() != shown.lower() else ""),
                                            entities=ents))
        return out
