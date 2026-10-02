"""spaCy NLP: vocabularies (built-in and custom), named entities, noun
phrases classified through WordNet, and subject–verb–object structure.

Outputs entity candidates (``x_nlp``), sentence boundaries (``x_nlp_sents``)
and syntactic triples (``x_nlp_svo``); the ``entities`` resolver turns the
triples into relations between whatever entities end up owning those words.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..categories import NER_TO_CATEGORY
from ..db import table
from ..fields import Field
from ..nlp import gazetteer
from ..nlp.models import MODEL_NAMES, model_state
from ..nlp.models import resolve as resolve_model
from ..nlp.pipeline import TEXT_SOURCES, analysed_text, analysis_mode, load_spacy, resolve_wordnet, wordnet_lexicon
from .base import Module, register, span_table

_ALLOWED_GAZ_POS = {"NOUN", "PROPN", "X", "NUM", "SYM"}
_SUBJ = {"nsubj", "nsubjpass", "csubj"}
_OBJ = {"dobj", "attr", "oprd", "dative", "obj"}
_SPEAKER_PRONOUNS = {"i", "we", "you", "me", "us"}

NER_LABELS = [
    "PERSON", "NORP", "FAC", "ORG", "GPE", "LOC", "PRODUCT", "EVENT", "WORK_OF_ART",
    "LAW", "LANGUAGE", "DATE", "TIME", "PERCENT", "MONEY", "QUANTITY", "ORDINAL", "CARDINAL",
]
WORDNET_CATEGORIES = [
    "food", "vehicle", "software", "device", "tool", "clothing", "weapon", "medical", "body",
    "animal", "plant", "substance", "place", "organization", "role", "document", "finance",
    "time", "measure", "activity", "emotion", "color", "event",
]
# claim order: custom vocabulary, built-in vocabulary, NER, noun phrases (regex = 10)
P_CUSTOM, P_VOCAB, P_CASED, P_NER, P_NOUN = 20, 21, 22, 30, 40


@register
class NLPModule(Module):
    name = "nlp"
    label = "spaCy NLP"
    description = ("Vocabularies, named entities (people, organisations, places, dates, money …), noun phrases "
                   "classified through WordNet (garlic → food, sedan → vehicle) and subject–verb–object structure.")
    kind = "extraction"
    version = "1"
    order = 20
    chunk_size = 400
    tables = (
        span_table("x_nlp", "Vocabulary, NER and noun-phrase candidates"),
        table("x_nlp_sents", "para_hash", ("starts", "int[]"), ("ends", "int[]"), index=["para_hash"],
              description="Sentence boundaries of parsed paragraphs"),
        table("x_nlp_svo", "para_hash", ("seq", "int"), ("oseq", "int"), ("sent", "int"), "verb", ("obj_start", "int"),
              ("subj_starts", "int[]"), ("speaker", "bool"), index=["para_hash"],
              description="Verb → object (and subjects) from the dependency parse"),
    )
    span_table = "x_nlp"
    uses_settings = TEXT_SOURCES
    options = (
        Field("spacy_model", "spaCy model", "select", "en_core_web_sm", "parse", "",
              "Larger models are slower but recognise entities better. Download them below; until the chosen "
              "model is there, the small one is used.", options=MODEL_NAMES),
        Field("use_ner", "Named entity recognition", "bool", True, "parse", "",
              "People, organisations, places, products, dates, money …"),
        Field("ner_labels", "NER labels", "multiselect",
              [x for x in NER_LABELS if x not in ("CARDINAL", "ORDINAL", "PERCENT")], "parse", "",
              "Which spaCy entity labels become nodes.", options=NER_LABELS),
        Field("use_gazetteer", "Built-in vocabularies", "bool", True, "parse", "",
              "Curated term lists (software, AI models, vehicle makes, …)."),
        Field("custom_gazetteer", "Custom vocabulary", "textarea",
              "# category: term, term, …\n# food: gochujang, za'atar\n", "parse", "",
              "One line per category. Any category name works; unknown ones get a neutral colour."),
        Field("use_wordnet", "WordNet categories", "bool", True, "parse", "",
              "Classify common nouns through WordNet hypernyms: garlic→food, sedan→vehicle, surgeon→role."),
        Field("wordnet_categories", "WordNet categories kept", "multiselect", WORDNET_CATEGORIES, "parse", "",
              "Nouns falling into other categories become plain concepts.", options=WORDNET_CATEGORIES),
        Field("wordnet_senses", "Senses considered", "int", 3, "parse", "",
              "How many WordNet senses of a word may vote for its category.", min=1, max=3),
        Field("use_concepts", "Noun-phrase concepts", "bool", True, "parse", "",
              "Keep uncategorised noun phrases as generic concept nodes."),
        Field("concept_mode", "Concept granularity", "select", "compound", "parse", "",
              "compound: 'sports car'; head: 'car'; phrase: 'red sports car'.", options=["compound", "head", "phrase"]),
        Field("extract_relations", "Subject–verb–object relations", "bool", True, "parse", "",
              "Dependency parse each sentence; link entities via their verb."),
    )

    @property
    def models_dir(self) -> Path | None:
        return self.data_dir / "models" if self.data_dir is not None else None

    def fingerprint_extra(self, ctx: Any) -> Any:
        # downloading the chosen model (or a new version of it) changes what this module finds
        return model_state(self.opt("spacy_model", "en_core_web_sm"), self.models_dir)

    def setup(self) -> None:
        from spacy.matcher import PhraseMatcher

        self.nlp = load_spacy(resolve_model(self.opt("spacy_model", "en_core_web_sm"), self.models_dir))
        self.has_parser = "parser" in self.nlp.pipe_names
        self.has_ner = "ner" in self.nlp.pipe_names
        self.ner_labels = set(self.opt("ner_labels", []))
        self.wn_cats = set(self.opt("wordnet_categories", []))
        self.wn_senses = int(self.opt("wordnet_senses", 3))
        self.lexicon = wordnet_lexicon() if self.opt("use_wordnet", True) else {}
        self.custom = PhraseMatcher(self.nlp.vocab, attr="LOWER")
        self.gaz = PhraseMatcher(self.nlp.vocab, attr="LOWER")
        self.cased = PhraseMatcher(self.nlp.vocab, attr="ORTH")
        for cat, terms in gazetteer.parse_custom(self.opt("custom_gazetteer", "")).items():
            self.custom.add(cat, [self.nlp.make_doc(t) for t in terms])
        if self.opt("use_gazetteer", True):
            for cat, terms in gazetteer.TERMS.items():
                self.gaz.add(cat, [self.nlp.make_doc(t) for t in terms])
            for cat, terms in gazetteer.CASED.items():
                self.cased.add(cat, [self.nlp.make_doc(t) for t in terms])

    def process(self, paras, deps):
        full, light = [], []
        for p in paras:
            mode = analysis_mode(p.role, p.code, p.text, self.cfg)
            if mode == "full":
                full.append(p)
            elif mode == "patterns":
                light.append(p)
        out: dict[str, list[tuple]] = {"x_nlp": [], "x_nlp_sents": [], "x_nlp_svo": []}
        for p in light:
            text = analysed_text(p.text, p.role)
            self._doc(p.hash, text, self.nlp.make_doc(text), False, out)
        texts = [analysed_text(p.text, p.role) for p in full]
        for p, text, doc in zip(full, texts, self.nlp.pipe(texts, batch_size=32)):
            self._doc(p.hash, text, doc, True, out)
        return out

    # ----------------------------------------------------------------------
    def _doc(self, h: str, text: str, doc, parsed: bool, out: dict[str, list[tuple]]) -> None:
        spans = out["x_nlp"]

        def cand(s, e, category, source, prio, ord_, key_hint=None, group=None, rank=None):
            spans.append((h, s, e, text[s:e], category, source, key_hint, False, prio, ord_, group, rank, None))

        if parsed and (self.has_parser or "sentencizer" in self.nlp.pipe_names):
            sents = list(doc.sents)
            out["x_nlp_sents"].append((h, [s.start_char for s in sents], [s.end_char for s in sents]))

        # vocabularies
        for matcher, source, prio in ((self.custom, "custom", P_CUSTOM), (self.gaz, "vocab", P_VOCAB),
                                      (self.cased, "vocab", P_CASED)):
            if len(matcher) == 0:
                continue
            found = sorted(matcher(doc, as_spans=True), key=lambda sp: (sp.start, -(sp.end - sp.start)))
            for i, span in enumerate(found):
                if parsed and source == "vocab" and len(span) == 1:
                    tok = span[0]
                    if tok.pos_ and tok.pos_ not in _ALLOWED_GAZ_POS:
                        continue
                    if tok.is_sent_start and tok.text in gazetteer.SENTENCE_START_AMBIGUOUS:
                        continue
                cand(span.start_char, span.end_char, span.label_, source, prio, i)
        if not parsed:
            return
        # named entities
        if self.opt("use_ner", True) and self.has_ner:
            for i, ent in enumerate(doc.ents):
                if ent.label_ not in self.ner_labels:
                    continue
                s, e = ent.start_char, ent.end_char
                if ent[0].lower_ in ("the", "a", "an") and len(ent) > 1:
                    s = ent[1].idx
                if ent.label_ == "MONEY" and s > 0 and text[s - 1] in "$€£¥₹":
                    s -= 1
                cand(s, e, NER_TO_CATEGORY.get(ent.label_, "concept"), f"ner:{ent.label_}", P_NER, i)
        if self.has_parser:
            self._noun_phrases(doc, cand)
            if self.opt("extract_relations", True):
                self._svo(h, doc, out["x_nlp_svo"])

    def _noun_phrases(self, doc, cand) -> None:
        """One candidate group per noun chunk: the longest phrase WordNet knows,
        then modifier + head expansions, then the head alone – the resolver
        takes the first whose words are all still free."""
        mode = self.opt("concept_mode", "compound")
        keep_concepts = self.opt("use_concepts", True)
        allowed = {"compound", "flat", "nmod"} | ({"amod", "nummod"} if mode == "phrase" else set())
        for ci, chunk in enumerate(doc.noun_chunks):
            root = chunk.root
            if root.pos_ not in ("NOUN", "PROPN") or root.lower_ in _SPEAKER_PRONOUNS:
                continue
            if root.is_stop and root.pos_ != "PROPN":
                continue
            starts: list[int] = []
            if self.lexicon and mode != "head":
                first = chunk.start
                while first < root.i and (doc[first].pos_ in ("DET", "PRON", "NUM", "PUNCT") or doc[first].dep_ == "poss"):
                    first += 1
                for c in range(max(first, root.i - 3), root.i):
                    phrase = " ".join([t.lower_ for t in doc[c:root.i]] + [root.lemma_.lower()])
                    if phrase in self.lexicon:
                        starts.append(c)
            if mode != "head":
                s = root.i
                while s > chunk.start:
                    prev = doc[s - 1]
                    if prev.dep_ not in allowed or prev.is_punct:
                        break
                    s -= 1
                starts.extend(range(s, root.i))
            starts.append(root.i)
            seen: set[int] = set()
            head_lemma = root.lemma_.lower() if root.pos_ == "NOUN" else root.text
            for rank, st in enumerate(x for x in starts if not (x in seen or seen.add(x))):
                span = doc[st:root.i + 1]
                lookup = " ".join([t.lower_ for t in span[:-1]] + [head_lemma.lower()])
                category = ""
                if self.lexicon:
                    cats = self.lexicon.get(lookup) or self.lexicon.get(head_lemma.lower())
                    if cats:
                        category = resolve_wordnet(cats, self.wn_senses)
                        if category not in self.wn_cats:
                            category = ""
                if not category and keep_concepts:
                    category = "concept"
                source = "wordnet" if category not in ("concept", "") else "concept"
                # "" category: if this alternative wins, the chunk is dropped
                cand(span.start_char, span.end_char, category, source, P_NOUN, ci,
                     lookup if root.pos_ == "NOUN" else None, ci, rank)

    def _svo(self, h: str, doc, rows: list[tuple]) -> None:
        def expand(tok) -> list:
            return [tok, *tok.conjuncts]

        seq = 0
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
                subj_starts = [s.idx for s in subjects]
                for oi, (obj_tok, label) in enumerate(objects):
                    rows.append((h, seq, oi, si, label, obj_tok.idx, subj_starts, speaker))
                seq += 1
