"""Security findings: runs the security checks (:mod:`synthsift.checks`) over the corpus.

A corpus-scope module: it reads the resolved entities of every turn and writes
one row per finding. The checks themselves are plugins – the built-in ones in
``synthsift/checks/``, your own in ``<data dir>/checks/`` (see ``CHECKS.md``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import checks
from ..db import Batch, table
from ..fields import Field
from .base import Module, register

FINDINGS = table("a_findings", ("seq", "int"), "conv", "event", "category", "rule", "label", "severity", "detail", ("entities", "json"),
                 ("chain", "json"), "value", index=["conv"], description="One row per security finding")


def user_check_dirs(data_dir: Path | None) -> list[Path]:
    """Where analysts' own checks are loaded from."""
    return [data_dir / "checks"] if data_dir is not None else []


@register
class SecurityModule(Module):
    name = "security"
    label = "Security analysis"
    description = ("Runs the security checks: exfiltration, downloads, fetch-and-run, exposed secrets, sensitive-file "
                   "access, destructive commands, watchlist matches and IOC list hits, with source → action → sink "
                   "chains. Add your own checks as Python files in <data dir>/checks/ (see CHECKS.md).")
    kind = "analysis"
    scope = "corpus"
    version = "4"
    needs_corpus = True
    tables = (FINDINGS,)
    uses_settings = ("ioc_findings",)
    options = (
        Field("sec_checks_off", "Checks", "checks", [], "parse", "",
              "Switch checks off or change the severity they report. Your own checks (Python files in the checks "
              "folder) are listed with the built-in ones."),
        Field("sec_severity", "Severity per check", "hidden", "", "parse", "",
              "`check_name: severity` per line."),
        Field("sec_min_severity", "Minimum severity", "select", "low", "parse", "",
              "Hide findings below this severity.", options=checks.SEVERITIES),
        *checks.iter_options(),
    )

    def dependencies(self, enabled: set[str]) -> list[str]:
        return ["entities"] + (["ioc"] if "ioc" in enabled else [])

    def _load(self, ctx: Any) -> list[str]:
        problems = checks.load_user_checks(user_check_dirs(ctx.data_dir))
        ctx.note("checks", {"problems": problems, "user": [c["name"] for c in checks.describe(self.cfg)
                                                           if c["source"] != "builtin"]})
        return problems

    def fingerprint_extra(self, ctx: Any) -> Any:
        problems = self._load(ctx)
        return checks.sources_digest(checks.all_checks().values()), problems

    def run_corpus(self, ctx: Any) -> int:
        self._load(ctx)
        conversations, events, paragraphs, analysis = ctx.corpus()
        errors: dict[str, str] = {}
        findings = checks.scan(conversations, events, paragraphs, analysis, self.cfg, corpus=ctx, errors=errors)
        if errors:
            ctx.note("errors", errors)
        st = ctx.storage
        with st.transaction():
            st.delete("a_findings")
            st.insert(Batch.from_rows(FINDINGS, [(i, f.conv, f.event, f.category, f.rule, f.label, f.severity, f.detail,
                                                  json.dumps(f.entities), json.dumps([list(c) for c in f.chain]), f.value)
                                                 for i, f in enumerate(findings)]))
        return len(findings)
