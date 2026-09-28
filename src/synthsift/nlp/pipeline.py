"""Shared NLP building blocks.

The extraction itself lives in the enrichment modules (:mod:`synthsift.modules`):
``regex`` (paths, URLs, IPs, hashes …), ``nlp`` (vocabularies, spaCy NER, noun
phrases through WordNet, subject–verb–object structure), ``ioc`` (indicator and
keyword lists) and the ``entities`` resolver that merges their candidates into
*mentions* (typed spans with character offsets) and *relations*.  This module
keeps what they share: result types, spaCy / WordNet loading and the
text-source rules.  To run the modules on a few strings, use
:func:`synthsift.modules.runner.analyze`.
"""

from __future__ import annotations

import gzip
import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class Mention:
    key: str
    text: str
    category: str
    start: int
    end: int
    source: str
    sent: int = 0
    verb: str | None = None  # governing verb when the speaker acts on it ("read" /etc/hosts)
    labels: list[str] = field(default_factory=list)  # e.g. IOC / keyword list hits


@dataclass
class Relation:
    subj: str
    verb: str
    obj: str
    sent: int


@dataclass
class ParaResult:
    mentions: list[Mention] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)


# --------------------------------------------------------------- resources
@lru_cache(maxsize=4)
def load_spacy(model: str):
    import spacy

    try:
        nlp = spacy.load(model)
    except OSError:
        log.warning("spaCy model %s not installed; falling back to en_core_web_sm", model)
        try:
            nlp = spacy.load("en_core_web_sm")
        except OSError:
            log.warning("no spaCy model installed; using a blank English pipeline (no NER / parsing)")
            nlp = spacy.blank("en")
            nlp.add_pipe("sentencizer")
    nlp.max_length = 5_000_000
    return nlp


def installed_models() -> list[str]:
    import spacy.util

    return sorted(spacy.util.get_installed_models())


@lru_cache(maxsize=1)
def wordnet_lexicon() -> dict[str, tuple[str, ...]]:
    data = resources.files("synthsift.nlp").joinpath("data/wordnet_lexicon.tsv.gz").read_bytes()
    lex: dict[str, tuple[str, ...]] = {}
    for line in gzip.decompress(data).decode().splitlines():
        lemma, _, cats = line.partition("\t")
        lex[lemma] = tuple(cats.split("|"))
    return lex


def resolve_wordnet(cats: tuple[str, ...], senses: int) -> str:
    """Pick a category from the categories of a word's first senses.

    Senses vote with weights 1, 1/2, 1/3 (WordNet orders senses by frequency);
    an uncategorised sense votes for "" (plain concept).  Plant/animal senses
    that also have a food sense resolve to food – in conversation "garlic" or
    "chicken" is nearly always the ingredient.
    """
    cats = cats[:senses]
    if not cats:
        return ""
    if "food" in cats and cats[0] in ("", "plant", "animal", "substance", "food"):
        return "food"
    scores: dict[str, float] = {}
    for i, c in enumerate(cats):
        scores[c] = scores.get(c, 0.0) + 1.0 / (i + 1)
    return max(scores, key=lambda c: (scores[c], -cats.index(c)))


_CODE_CHARS = set("{}()[];=<>$\\|&*/_")
_CODE_LINE = re.compile(
    r"^\s*(?:def |class |import |from \S+ import|return\b|if .*:$|for .*:$|elif |else:|try:|except|"
    r"const |let |var |function\b|export |async |await |#include|public |private |@\w+|</?\w+[^>]*>|\}|\{)"
)


def looks_like_code(text: str) -> bool:
    """Heuristic for unfenced code / markup / logs inside prose messages."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return False
    sym = sum(ch in _CODE_CHARS for ch in text) / max(len(text), 1)
    codey = sum(bool(_CODE_LINE.match(ln)) for ln in lines) / len(lines)
    indented = sum(ln.startswith(("    ", "\t")) for ln in lines) / len(lines)
    return sym > 0.06 or codey >= 0.35 or (len(lines) >= 3 and indented >= 0.5 and sym > 0.02)


def _looks_like_prose(text: str) -> bool:
    words = text.split()
    if len(words) < 4 or looks_like_code(text):
        return False
    letters = sum(ch.isalpha() or ch.isspace() for ch in text)
    return letters / max(len(text), 1) > 0.8


def analysis_mode(role: str, code: bool, text: str, cfg: dict[str, Any]) -> str | None:
    """How deeply a paragraph is analysed: "full" (NLP), "patterns" (regex +
    vocabularies) or None (skipped), per the Text sources settings."""
    if role == "thought" and not cfg.get("analyze_thoughts", True):
        return None
    if role == "tool_call":
        if not cfg.get("analyze_tool_args", True):
            return None
        body = text.split(":", 1)[-1]
        return "full" if not code and _looks_like_prose(body) else "patterns"
    if role == "tool_result":
        if not cfg.get("analyze_tool_results", True):
            return None
        return cfg.get("tool_result_nlp", "patterns")
    if code or looks_like_code(text):
        return cfg.get("code_nlp", "patterns")
    return "full"


def analysed_text(text: str, role: str) -> str:
    """Tool-call paragraphs are "arg: value": analyse the value only, keeping offsets."""
    if role == "tool_call" and ": " in text[:80]:
        cut = text.index(": ") + 2
        return " " * cut + text[cut:]
    return text


#: settings that decide which paragraphs are analysed and how deeply
TEXT_SOURCES = ("analyze_thoughts", "analyze_tool_args", "analyze_tool_results", "tool_result_nlp", "code_nlp")
