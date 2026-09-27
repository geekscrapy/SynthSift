"""Labels each paragraph with what kind of content it is."""

from __future__ import annotations

import json
import re

from ..db import table
from ..nlp.pipeline import looks_like_code
from .base import Module, register

_LOG = re.compile(r"^\s*(?:\[?\d{4}[-/]\d{2}[-/]\d{2}[ T]\d{2}:\d{2}|\w{3} +\d+ \d{2}:\d{2}:\d{2}|\d{2}:\d{2}:\d{2}[.,]\d+)"
                  r"|\b(?:DEBUG|INFO|WARN(?:ING)?|ERROR|FATAL|CRITICAL)\b", re.M)
_TRACE = re.compile(r"Traceback \(most recent call last\)|^\s+at [\w.$<>]+\(.*\)$|^\s*File \".+\", line \d+", re.M)
_TABLE = re.compile(r"^\s*\|.*\|\s*$|\t.*\t", re.M)
_SECRETISH = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\bAKIA[0-9A-Z]{16}\b|\b(?:ghp|gho|ghu|ghs)_[A-Za-z0-9]{30,}\b"
                        r"|\b(?:password|passwd|secret|api[_-]?key|token)\s*[:=]\s*\S+", re.I)
_DIFF = re.compile(r"^(?:diff --git|@@ .* @@|\+\+\+ |--- )", re.M)


def classify(text: str, role: str, arg: str | None = None) -> tuple[str, list[str]]:
    """(main label, extra flags) for one paragraph."""
    flags: list[str] = []
    if _SECRETISH.search(text):
        flags.append("secret-like")
    s = text.strip()
    if role == "tool_call" and arg in ("command", "cmd", "script"):
        return "command", flags
    if s[:1] in "{[" and len(s) > 1:
        try:
            json.loads(s)
            return "json", flags
        except ValueError:
            pass
    if _TRACE.search(text):
        return "stacktrace", flags
    if _DIFF.search(text):
        return "diff", flags
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if lines and sum(bool(_LOG.search(ln)) for ln in lines) / len(lines) >= 0.5:
        return "log", flags
    if len(lines) >= 2 and sum(bool(_TABLE.search(ln)) for ln in lines) / len(lines) >= 0.6:
        return "table", flags
    if looks_like_code(text):
        return "code", flags
    return "prose", flags


@register
class ContentTypeModule(Module):
    name = "content_type"
    label = "Content type"
    description = "Labels every paragraph as prose, code, command, JSON, log, stack trace, diff or table, and flags secret-like text."
    kind = "label"
    chunk_size = 20000
    tables = (table("l_content_type", "para_hash", "label", ("flags", "text[]"), index=["para_hash"],
                    description="One label (+ flags) per paragraph"),)

    def process(self, paras, deps):
        rows = []
        for p in paras:
            label, flags = classify(p.text, p.role, p.arg)
            rows.append((p.hash, label, flags or None))
        return {"l_content_type": rows}
