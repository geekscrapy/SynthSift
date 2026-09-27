"""Per-paragraph text features – a small, fast feature module that is also the
simplest template for writing a new one."""

from __future__ import annotations

import math
import re
from collections import Counter

from ..db import table
from .base import Module, register

_WORD = re.compile(r"\w+")
_URL = re.compile(r"https?://\S+")


def entropy(text: str) -> float:
    """Shannon entropy in bits per character (high for keys, hashes, base64)."""
    if not text:
        return 0.0
    n = len(text)
    return -sum(c / n * math.log2(c / n) for c in Counter(text).values())


@register
class TextStatsModule(Module):
    name = "text_stats"
    label = "Text statistics"
    description = "Length, word and line counts, character mix, Shannon entropy and URL count for every paragraph."
    kind = "feature"
    chunk_size = 20000
    tables = (table("f_text_stats", "para_hash", ("chars", "int"), ("words", "int"), ("lines", "int"),
                    ("digit_ratio", "float"), ("upper_ratio", "float"), ("symbol_ratio", "float"),
                    ("space_ratio", "float"), ("entropy", "float"), ("max_line", "int"), ("avg_word", "float"),
                    ("urls", "int"), index=["para_hash"], description="One row of numeric features per paragraph"),)

    def process(self, paras, deps):
        rows = []
        for p in paras:
            t = p.text
            n = max(len(t), 1)
            words = _WORD.findall(t)
            lines = t.split("\n")
            rows.append((
                p.hash, len(t), len(words), len(lines),
                round(sum(ch.isdigit() for ch in t) / n, 4),
                round(sum(ch.isupper() for ch in t) / n, 4),
                round(sum(not ch.isalnum() and not ch.isspace() for ch in t) / n, 4),
                round(sum(ch.isspace() for ch in t) / n, 4),
                round(entropy(t), 4),
                max((len(x) for x in lines), default=0),
                round(sum(map(len, words)) / len(words), 3) if words else 0.0,
                len(_URL.findall(t)),
            ))
        return {"f_text_stats": rows}
