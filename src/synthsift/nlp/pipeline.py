"""Non-LLM extraction pipeline.

For every paragraph we produce *mentions* (typed entity spans with character
offsets) and *relations* (subject –verb→ object triples from the dependency
parse).  Sources, in priority order when spans overlap:

1. regex patterns    – paths, URLs, IPs, hashes, CVEs, env vars, inline code …
2. vocabularies      – custom terms from settings, then built-in gazetteers
3. spaCy NER         – people, organisations, places, dates, money …
4. noun phrases      – categorised through the WordNet lexicon (food, vehicle,
                       role, device, …) or kept as generic concepts
"""

from __future__ import annotations

import gzip
import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from typing import Any, Callable, Iterable

from ..categories import LITERAL_CATEGORIES, NER_TO_CATEGORY
from . import gazetteer
from .regex_extractors import REGEX_BY_NAME, Match, compile_custom, find_all

log = logging.getLogger(__name__)

_ALLOWED_GAZ_POS = {"NOUN", "PROPN", "X", "NUM", "SYM"}
_SUBJ = {"nsubj", "nsubjpass", "csubj"}
_OBJ = {"dobj", "attr", "oprd", "dative", "obj"}
_SPEAKER_PRONOUNS = {"i", "we", "you", "me", "us"}
_LEADING_JUNK = re.compile(r"^(?:the|a|an|this|that|these|those|my|your|our|their|his|her|its)\s+", re.I)


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

    def to_tuple(self) -> tuple:
        return (self.key, self.text, self.category, self.start, self.end, self.source, self.sent, self.verb)


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


class Analyzer:
    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.nlp = load_spacy(cfg.get("spacy_model", "en_core_web_sm"))
        self.has_parser = "parser" in self.nlp.pipe_names
        self.has_ner = "ner" in self.nlp.pipe_names
        self.ignore = {t.strip().lower() for t in cfg.get("ignore_terms", "").splitlines() if t.strip()}
        self.min_len = int(cfg.get("min_term_length", 2))
        self.merge_case = bool(cfg.get("merge_case", True))
        self.ner_labels = set(cfg.get("ner_labels", []))
        self.wn_cats = set(cfg.get("wordnet_categories", []))
        self.wn_senses = int(cfg.get("wordnet_senses", 3))
        self.lexicon = wordnet_lexicon() if cfg.get("use_wordnet", True) else {}

        self.regex_defs = []
        if cfg.get("use_regex", True):
            self.regex_defs = [REGEX_BY_NAME[n] for n in REGEX_BY_NAME if n in set(cfg.get("regex_categories", []))]
        self.regex_defs = compile_custom(cfg.get("custom_regex", "")) + self.regex_defs

        from spacy.matcher import PhraseMatcher

        self.custom_matcher = PhraseMatcher(self.nlp.vocab, attr="LOWER")
        self.gaz_matcher = PhraseMatcher(self.nlp.vocab, attr="LOWER")
        self.cased_matcher = PhraseMatcher(self.nlp.vocab, attr="ORTH")
        for cat, terms in gazetteer.parse_custom(cfg.get("custom_gazetteer", "")).items():
            self.custom_matcher.add(cat, [self.nlp.make_doc(t) for t in terms])
        if cfg.get("use_gazetteer", True):
            for cat, terms in gazetteer.TERMS.items():
                self.gaz_matcher.add(cat, [self.nlp.make_doc(t) for t in terms])
            for cat, terms in gazetteer.CASED.items():
                self.cased_matcher.add(cat, [self.nlp.make_doc(t) for t in terms])

    # ----------------------------------------------------------- helpers
    def normalize(self, text: str, category: str, literal: bool = False) -> str | None:
        text = " ".join(text.split())
        if literal or category in LITERAL_CATEGORIES:
            key = text
        else:
            key = _LEADING_JUNK.sub("", text)
            key = re.sub(r"(?:'s|’s)$", "", key).strip(".,;:!?\"'()[]{}")
            if self.merge_case:
                key = key.lower()
        if len(key) < self.min_len or key.lower() in self.ignore or not re.search(r"[^\W_]", key):
            return None
        return key

    def mode_for(self, role: str, code: bool, text: str) -> str | None:
        c = self.cfg
        if role == "thought" and not c.get("analyze_thoughts", True):
            return None
        if role == "tool_call":
            if not c.get("analyze_tool_args", True):
                return None
            body = text.split(":", 1)[-1]
            return "full" if not code and _looks_like_prose(body) else "patterns"
        if role == "tool_result":
            if not c.get("analyze_tool_results", True):
                return None
            return c.get("tool_result_nlp", "patterns")
        if code or looks_like_code(text):
            return c.get("code_nlp", "patterns")
        return "full"

    # ---------------------------------------------------------- analysis
    def analyze(
        self,
        items: Iterable[tuple[str, str, str, bool]],
        progress: Callable[[int, int], None] | None = None,
    ) -> dict[str, ParaResult]:
        """items: (paragraph id, text, role, is_code)."""
        full: list[tuple[str, str]] = []
        light: list[tuple[str, str]] = []
        for pid, text, role, code in items:
            mode = self.mode_for(role, code, text)
            if role == "tool_call" and ": " in text[:80]:
                # "command: ls -la" – analyse the value only, keep offsets intact
                cut = text.index(": ") + 2
                text = " " * cut + text[cut:]
            if mode == "full":
                full.append((pid, text))
            elif mode == "patterns":
                light.append((pid, text))
        total = len(full) + len(light)
        done = 0
        out: dict[str, ParaResult] = {}

        for pid, text in light:
            out[pid] = self._analyze_doc(text, self.nlp.make_doc(text), parsed=False)
            done += 1
            if progress and done % 200 == 0:
                progress(done, total)

        texts = [t for _, t in full]
        for (pid, text), doc in zip(full, self.nlp.pipe(texts, batch_size=32)):
            out[pid] = self._analyze_doc(text, doc, parsed=True)
            done += 1
            if progress and done % 25 == 0:
                progress(done, total)
        if progress:
            progress(total, total)
        return out

    def _analyze_doc(self, text: str, doc, parsed: bool) -> ParaResult:
        res = ParaResult()
        taken = [0] * (len(text) + 1)  # 1 where a char is already claimed
        sent_of_char: list[int] | None = None
        if parsed and (self.has_parser or "sentencizer" in self.nlp.pipe_names):
            sent_of_char = [0] * (len(text) + 1)
            for si, s in enumerate(doc.sents):
                for i in range(s.start_char, min(s.end_char + 1, len(text) + 1)):
                    sent_of_char[i] = si

        def free(s: int, e: int) -> bool:
            return not any(taken[s:e])

        def add(s: int, e: int, category: str, source: str, literal: bool = False) -> Mention | None:
            if e <= s or not free(s, e):
                return None
            surface = text[s:e]
            key = self.normalize(surface, category, literal)
            if key is None:
                return None
            for i in range(s, e):
                taken[i] = 1
            m = Mention(key, surface, category, s, e, source, sent_of_char[s] if sent_of_char else 0)
            res.mentions.append(m)
            return m

        # 1. regex
        for m in find_all(text, self.regex_defs):
            add(m.start, m.end, m.category, m.source, m.literal)

        # 2. vocabularies
        for matcher, source in ((self.custom_matcher, "custom"), (self.gaz_matcher, "vocab"), (self.cased_matcher, "vocab")):
            if len(matcher) == 0:
                continue
            spans = sorted(matcher(doc, as_spans=True), key=lambda sp: (sp.start, -(sp.end - sp.start)))
            for span in spans:
                if parsed and source == "vocab" and len(span) == 1:
                    tok = span[0]
                    if tok.pos_ and tok.pos_ not in _ALLOWED_GAZ_POS:
                        continue
                    if tok.is_sent_start and tok.text in gazetteer.SENTENCE_START_AMBIGUOUS:
                        continue
                add(span.start_char, span.end_char, span.label_, source)
        self._merge_adjacent(text, res)

        if parsed:
            # 3. named entities
            if self.cfg.get("use_ner", True) and self.has_ner:
                for ent in doc.ents:
                    if ent.label_ not in self.ner_labels:
                        continue
                    s, e = ent.start_char, ent.end_char
                    # drop leading determiners ("the Eiffel Tower" -> "Eiffel Tower")
                    if ent[0].lower_ in ("the", "a", "an") and len(ent) > 1:
                        s = ent[1].idx
                    if ent.label_ == "MONEY" and s > 0 and text[s - 1] in "$€£¥₹":
                        s -= 1
                    add(s, e, NER_TO_CATEGORY.get(ent.label_, "concept"), f"ner:{ent.label_}")
            # 4. noun phrases
            if self.has_parser:
                self._noun_phrases(doc, add, free)
            if self.cfg.get("extract_relations", True) and self.has_parser:
                self._relations(doc, text, res)
        return res

    def _merge_adjacent(self, text: str, res: ParaResult) -> None:
        """'Toyota' + 'Corolla' -> 'Toyota Corolla' (same vocabulary category, one space apart)."""
        ms = sorted(res.mentions, key=lambda m: m.start)
        merged: list[Mention] = []
        for m in ms:
            prev = merged[-1] if merged else None
            if (
                prev is not None
                and prev.source in ("vocab", "custom")
                and m.source == prev.source
                and m.category == prev.category
                and text[prev.end:m.start] == " "
            ):
                surface = text[prev.start:m.end]
                key = self.normalize(surface, m.category)
                if key:
                    merged[-1] = Mention(key, surface, m.category, prev.start, m.end, m.source, prev.sent)
                    continue
            merged.append(m)
        res.mentions[:] = merged

    def _noun_phrases(self, doc, add, free) -> None:
        mode = self.cfg.get("concept_mode", "compound")
        keep_concepts = self.cfg.get("use_concepts", True)
        for chunk in doc.noun_chunks:
            root = chunk.root
            if root.pos_ not in ("NOUN", "PROPN") or root.lower_ in _SPEAKER_PRONOUNS:
                continue
            if root.is_stop and root.pos_ != "PROPN":
                continue
            if not free(root.idx, root.idx + len(root.text)):
                continue
            start = root.i
            allowed = {"compound", "flat", "nmod"} | ({"amod", "nummod"} if mode == "phrase" else set())
            # prefer the longest modifier+head phrase WordNet knows ("garlic bread", "fire truck")
            if self.lexicon and mode != "head":
                first = chunk.start
                while first < root.i and (doc[first].pos_ in ("DET", "PRON", "NUM", "PUNCT") or doc[first].dep_ == "poss"):
                    first += 1
                for cand in range(max(first, root.i - 3), root.i):
                    toks = doc[cand:root.i]
                    if all(free(t.idx, t.idx + len(t.text)) for t in toks):
                        phrase = " ".join([t.lower_ for t in toks] + [root.lemma_.lower()])
                        if phrase in self.lexicon:
                            start = cand
                            break
            if mode != "head" and start == root.i:
                while start > chunk.start:
                    prev = doc[start - 1]
                    if prev.dep_ not in allowed or prev.is_punct:
                        break
                    if not free(prev.idx, prev.idx + len(prev.text)):
                        break
                    start -= 1
            span = doc[start:root.i + 1]
            # key uses the lemma of the head noun so "cars"/"car" merge
            head_lemma = root.lemma_.lower() if root.pos_ == "NOUN" else root.text
            surface = span.text
            lookup = " ".join([t.lower_ for t in span[:-1]] + [head_lemma.lower()])
            category = ""
            if self.lexicon:
                cats = self.lexicon.get(lookup) or self.lexicon.get(head_lemma.lower())
                if cats:
                    category = resolve_wordnet(cats, self.wn_senses)
                    if category not in self.wn_cats:
                        category = ""
            if not category:
                if not keep_concepts:
                    continue
                category = "concept"
            m = add(span.start_char, span.end_char, category, "wordnet" if category != "concept" else "concept")
            if m is not None and root.pos_ == "NOUN" and self.merge_case:
                # merge plural/singular forms under the lemma
                lemma_key = self.normalize(lookup, category)
                if lemma_key:
                    m.key = lemma_key

    def _relations(self, doc, text: str, res: ParaResult) -> None:
        if not res.mentions:
            return
        owner = [-1] * (len(text) + 1)
        for idx, m in enumerate(res.mentions):
            for i in range(m.start, m.end):
                owner[i] = idx

        def mention_of(tok) -> Mention | None:
            i = owner[tok.idx] if tok.idx < len(owner) else -1
            return res.mentions[i] if i >= 0 else None

        def expand(tok) -> list:
            return [tok, *tok.conjuncts]

        sent_index = {s.start: si for si, s in enumerate(doc.sents)}
        for sent in doc.sents:
            si = sent_index.get(sent.start, 0)
            for tok in sent:
                if tok.pos_ not in ("VERB", "AUX"):
                    continue
                verb = tok.lemma_.lower()
                particle = next((c.lower_ for c in tok.children if c.dep_ == "prt"), None)
                if particle:
                    verb = f"{verb} {particle}"
                subjects = [s for c in tok.children if c.dep_ in _SUBJ for s in expand(c)]
                if not subjects and tok.dep_ in ("xcomp", "conj", "advcl") and tok.head.pos_ in ("VERB", "AUX"):
                    subjects = [s for c in tok.head.children if c.dep_ in _SUBJ for s in expand(c)]
                objects: list[tuple[Any, str]] = [(o, verb) for c in tok.children if c.dep_ in _OBJ for o in expand(c)]
                for prep in (c for c in tok.children if c.dep_ in ("prep", "agent")):
                    for pobj in (c for c in prep.children if c.dep_ == "pobj"):
                        objects.extend((o, f"{verb} {prep.lower_}") for o in expand(pobj))
                if not objects:
                    continue
                speaker = not subjects or all(s.lower_ in _SPEAKER_PRONOUNS for s in subjects)
                for obj_tok, label in objects:
                    obj = mention_of(obj_tok)
                    if obj is None:
                        continue
                    if speaker and obj.verb is None:
                        obj.verb = label
                    for subj_tok in subjects:
                        subj = mention_of(subj_tok)
                        if subj is not None and subj.key != obj.key:
                            rel = Relation(subj.key, label, obj.key, si)
                            if rel not in res.relations:
                                res.relations.append(rel)


def extract_text(text: str, cfg: dict[str, Any] | None = None) -> list[Match | Mention]:
    """Convenience helper: analyse a single piece of text (used in tests / REPL)."""
    from ..settings import defaults

    merged = {**defaults(), **(cfg or {})}
    return Analyzer(merged).analyze([("p", text, "user", False)])["p"].mentions
