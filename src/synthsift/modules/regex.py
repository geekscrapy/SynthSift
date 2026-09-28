"""Pattern extractors: file paths, URLs, IPs, hashes, CVEs, env vars, … and
the analyst's own ``category: regex`` lines."""

from __future__ import annotations

from ..fields import Field
from ..nlp.pipeline import TEXT_SOURCES, analysed_text, analysis_mode
from ..nlp.regex_extractors import REGEX_BY_NAME, REGEX_DEFS, compile_custom, find_all
from .base import Module, register, span_table

PRIORITY = 10  # claims text before vocabularies, NER and noun phrases


@register
class RegexModule(Module):
    name = "regex"
    label = "Pattern extractors"
    description = "Regular expressions for file paths, URLs, IPs, hashes, CVEs, env vars, inline code … plus your own patterns."
    kind = "extraction"
    order = 10
    chunk_size = 5000
    tables = (span_table("x_regex", "Pattern matches (entity candidates)"),)
    span_table = "x_regex"
    uses_settings = TEXT_SOURCES
    options = (
        Field("regex_categories", "Enabled patterns", "multiselect", [d.name for d in REGEX_DEFS if d.default], "parse", "",
              options=[d.name for d in REGEX_DEFS]),
        Field("custom_regex", "Custom patterns", "textarea",
              "# category: regular expression\n# ticket: \\bJIRA-\\d+\\b\n", "parse", "",
              "One `category: regex` per line."),
    )

    def setup(self) -> None:
        wanted = set(self.opt("regex_categories", []))
        self.defs = compile_custom(self.opt("custom_regex", "")) + [REGEX_BY_NAME[n] for n in REGEX_BY_NAME if n in wanted]

    def process(self, paras, deps):
        rows = []
        for p in paras:
            if analysis_mode(p.role, p.code, p.text, self.cfg) is None:
                continue
            text = analysed_text(p.text, p.role)
            for i, m in enumerate(find_all(text, self.defs)):
                rows.append((p.hash, m.start, m.end, m.text, m.category, m.source, None, bool(m.literal),
                             PRIORITY, i, None, None, None))
        return {"x_regex": rows}
