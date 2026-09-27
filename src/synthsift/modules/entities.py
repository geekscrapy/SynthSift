"""Entity resolver: merges every extraction module's candidates into the
entities and relations the graph shows.

Candidates come from each enabled module's ``span_table``.  In ``claim`` mode
they compete for text: lower ``prio`` first, each character belongs to at most
one entity, and a candidate group (a noun phrase and its shorter tails) takes
its first alternative whose words are all still free.  ``label`` mode
candidates (IOC / keyword lists) instead tag whatever entity they overlap, or
become an entity of their own on free text.

Keys are normalised here (case folding, leading articles, the ignore list),
so every source shares one vocabulary of nodes.
"""

from __future__ import annotations

import re
from typing import Any

from ..categories import LITERAL_CATEGORIES
from ..db import table
from ..fields import Field
from ..nlp.pipeline import Mention, ParaResult, Relation, analysed_text
from .base import Deps, Module, register, registry

_LEADING_JUNK = re.compile(r"^(?:the|a|an|this|that|these|those|my|your|our|their|his|her|its)\s+", re.I)
MERGE_BEFORE = 30  # adjacent vocabulary terms merge once vocabularies have claimed ("Toyota" + "Corolla")

ENTITIES = table("x_entities", "para_hash", ("ord", "int"), ("start", "int"), ("end", "int"), "text", "key", "category",
                 "source", ("sent", "int"), "verb", ("labels", "text[]"), index=["para_hash"],
                 description="Resolved entities per paragraph (what the graph shows)")
RELATIONS = table("x_relations", "para_hash", ("ord", "int"), "subj", "verb", "obj", ("sent", "int"), index=["para_hash"],
                  description="Entity → verb → entity relations")


@register
class EntitiesModule(Module):
    name = "entities"
    label = "Entity resolver"
    description = ("Merges the extraction modules' candidates into the graph's entities: higher-priority modules claim "
                   "text first, list modules label what they overlap, and parsed verbs become relations.")
    kind = "extraction"
    core = True
    version = "1"
    order = 90
    chunk_size = 3000
    tables = (ENTITIES, RELATIONS)
    options = (
        Field("min_term_length", "Minimum term length", "int", 2, "parse", "", min=1, max=10),
        Field("merge_case", "Merge case variants", "bool", True, "parse", "",
              "Treat 'Docker' and 'docker' as one node (literals like paths stay case-sensitive)."),
        Field("ignore_terms", "Ignored terms", "textarea",
              "thing\nthings\nway\nlot\nbit\nkind\nsort\nsomething\nanything\neverything\nnothing\n"
              "one\nones\nexample\nstuff\nlet\nokay\nok\nsure\nplease\nthanks\nthank\nhello\nhi\n"
              "question\nanswer\npoint\ncase\nfact\npart\nthat\nthis\nit\nyes\nno\n",
              "parse", "", "One per line (case-insensitive). These never become nodes."),
    )

    def dependencies(self, enabled: set[str]) -> list[str]:
        reg = registry()
        deps = {n for n in enabled if n != self.name and reg[n].span_table}
        if "nlp" in enabled:
            deps.add("nlp")  # sentences and verb structure
        return sorted(deps)

    def setup(self) -> None:
        self.ignore = {t.strip().lower() for t in self.opt("ignore_terms", "").splitlines() if t.strip()}
        self.min_len = int(self.opt("min_term_length", 2))
        self.merge_case = bool(self.opt("merge_case", True))

    # ------------------------------------------------------------------
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

    def process(self, paras, deps: Deps):
        sources = [(m.span_table, m.span_mode) for m in registry().values() if m.span_table and deps.has(m.span_table)]
        ents: list[tuple] = []
        rels: list[tuple] = []
        for p in paras:
            res = self.resolve(p, deps, sources)
            for i, m in enumerate(res.mentions):
                ents.append((p.hash, i, m.start, m.end, m.text, m.key, m.category, m.source, m.sent, m.verb, m.labels or None))
            for i, r in enumerate(res.relations):
                rels.append((p.hash, i, r.subj, r.verb, r.obj, r.sent))
        return {"x_entities": ents, "x_relations": rels}

    def resolve(self, p, deps: Deps, sources: list[tuple[str, str]]) -> ParaResult:
        res = ParaResult()
        text = analysed_text(p.text, p.role)
        claims: list[tuple[tuple, list[dict[str, Any]]]] = []
        labels: list[dict[str, Any]] = []
        # rows may arrive in any order (they come back from the database): sort on content only
        for ti, (tbl, mode) in enumerate(sources):
            groups: dict[Any, list[dict[str, Any]]] = {}
            for r in deps.dicts(tbl, p.hash):
                if mode == "label":
                    labels.append(r)
                elif r["alt_group"] is None:
                    claims.append(((r["prio"], r["ord"], ti, r["start"], r["end"]), [r]))
                else:
                    groups.setdefault(r["alt_group"], []).append(r)
            for g in groups.values():
                g.sort(key=lambda r: r["alt_rank"])
                claims.append(((g[0]["prio"], g[0]["ord"], ti, g[0]["start"], g[0]["end"]), g))
        if not claims and not labels:
            return res
        claims.sort(key=lambda c: c[0])

        sent_of = None
        sents = deps.dicts("x_nlp_sents", p.hash)[:1]
        if sents:
            sent_of = [0] * (len(text) + 1)
            for si, (s, e) in enumerate(zip(sents[0]["starts"], sents[0]["ends"])):
                for i in range(s, min(e + 1, len(text) + 1)):
                    sent_of[i] = si
        taken = bytearray(len(text) + 1)

        def free(s: int, e: int) -> bool:
            return not any(taken[s:e])

        def claim(r: dict[str, Any], extra_labels=None) -> Mention | None:
            s, e = r["start"], r["end"]
            if e <= s or e > len(text) or not free(s, e):
                return None
            surface = text[s:e]
            key = self.normalize(surface, r["category"], bool(r["literal"]))
            if key is None:
                return None
            taken[s:e] = b"\x01" * (e - s)
            m = Mention(key, surface, r["category"], s, e, r["source"], sent_of[s] if sent_of else 0,
                        labels=list(extra_labels or r.get("labels") or []))
            res.mentions.append(m)
            return m

        merged = False
        for sort_key, unit in claims:
            if not merged and sort_key[0] >= MERGE_BEFORE:
                self._merge_adjacent(text, res)
                merged = True
            if len(unit) == 1 and unit[0]["alt_group"] is None:
                claim(unit[0])
                continue
            chosen = next((r for r in unit if free(r["start"], r["end"])), None)
            if chosen is None or not chosen["category"]:
                continue
            m = claim(chosen)
            if m is not None and chosen["key_hint"] and self.merge_case:
                lemma_key = self.normalize(chosen["key_hint"], chosen["category"])
                if lemma_key:
                    m.key = lemma_key
        if not merged:
            self._merge_adjacent(text, res)

        # list hits label what they overlap, or become entities on free text
        for r in sorted(labels, key=lambda r: (r["prio"], r["ord"], r["start"], r["end"])):
            tags = list(r.get("labels") or [])
            hit = [m for m in res.mentions if m.start < r["end"] and r["start"] < m.end]
            if hit:
                for m in hit:
                    m.labels.extend(t for t in tags if t not in m.labels)
            else:
                claim(r, tags)

        self._relations(text, res, deps.dicts("x_nlp_svo", p.hash))
        return res

    def _merge_adjacent(self, text: str, res: ParaResult) -> None:
        ms = sorted(res.mentions, key=lambda m: m.start)
        merged: list[Mention] = []
        for m in ms:
            prev = merged[-1] if merged else None
            if (prev is not None and prev.source in ("vocab", "custom") and m.source == prev.source
                    and m.category == prev.category and text[prev.end:m.start] == " "):
                surface = text[prev.start:m.end]
                key = self.normalize(surface, m.category)
                if key:
                    merged[-1] = Mention(key, surface, m.category, prev.start, m.end, m.source, prev.sent,
                                         labels=prev.labels + [t for t in m.labels if t not in prev.labels])
                    continue
            merged.append(m)
        res.mentions[:] = merged

    @staticmethod
    def _relations(text: str, res: ParaResult, svo: list[dict[str, Any]]) -> None:
        if not res.mentions or not svo:
            return
        owner = [-1] * (len(text) + 1)
        for idx, m in enumerate(res.mentions):
            for i in range(m.start, m.end):
                owner[i] = idx

        def at(pos: int) -> Mention | None:
            i = owner[pos] if 0 <= pos < len(owner) else -1
            return res.mentions[i] if i >= 0 else None

        for r in sorted(svo, key=lambda r: (r["seq"], r["oseq"])):
            obj = at(r["obj_start"])
            if obj is None:
                continue
            if r["speaker"] and obj.verb is None:
                obj.verb = r["verb"]
            for ss in r["subj_starts"] or []:
                subj = at(ss)
                if subj is not None and subj.key != obj.key:
                    rel = Relation(subj.key, r["verb"], obj.key, r["sent"])
                    if rel not in res.relations:
                        res.relations.append(rel)


def to_para_result(ent_rows: list[tuple], rel_rows: list[tuple]) -> ParaResult:
    """Rows of ``x_entities`` / ``x_relations`` (full rows, para_hash first) → ParaResult."""
    res = ParaResult()
    for r in sorted(ent_rows, key=lambda r: r[1]):
        res.mentions.append(Mention(r[5], r[4], r[6], r[2], r[3], r[7], r[8] or 0, r[9], list(r[10] or [])))
    for r in sorted(rel_rows, key=lambda r: r[1]):
        res.relations.append(Relation(r[2], r[3], r[4], r[5] or 0))
    return res

