"""All user-tunable knobs, declared once.

Each field has a *scope*:

* ``parse`` – changing it re-runs NLP extraction over every transcript
* ``graph`` – changing it rebuilds the graph from cached NLP results (fast)
* ``view``  – purely client side (layout, physics, colours, panel behaviour)

The settings page is rendered generically from :data:`SCHEMA`, so adding a knob
here is enough to expose it in the UI.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .categories import CATEGORIES, NODE_TYPES
from .nlp.regex_extractors import REGEX_DEFS
from .nlp.security import SEVERITIES

NER_LABELS = [
    "PERSON", "NORP", "FAC", "ORG", "GPE", "LOC", "PRODUCT", "EVENT", "WORK_OF_ART",
    "LAW", "LANGUAGE", "DATE", "TIME", "PERCENT", "MONEY", "QUANTITY", "ORDINAL", "CARDINAL",
]
WORDNET_CATEGORIES = [
    "food", "vehicle", "software", "device", "tool", "clothing", "weapon", "medical", "body",
    "animal", "plant", "substance", "place", "organization", "role", "document", "finance",
    "time", "measure", "activity", "emotion", "color", "event",
]


@dataclass
class Field:
    key: str
    label: str
    type: str  # bool | int | float | select | multiselect | text | textarea | color
    default: Any
    scope: str  # parse | graph | view
    section: str
    help: str = ""
    options: list[Any] = field(default_factory=list)
    min: float | None = None
    max: float | None = None
    step: float | None = None


S_EXTRACT = "Extraction"
S_SOURCES = "Text sources"
S_CUSTOM = "Custom vocabulary"
S_GRAPH = "Graph content"
S_EDGES = "Edges & relations"
S_LAYOUT = "Layout & physics"
S_LOOK = "Appearance"
S_PANEL = "Transcript panel"
S_SECURITY = "Security analysis"
S_COLORS = "Colours"

SCHEMA: list[Field] = [
    # ------------------------------------------------------------ extraction
    Field("spacy_model", "spaCy model", "select", "en_core_web_sm", "parse", S_EXTRACT,
          "Larger models are slower but recognise entities better. Only installed models work.",
          options=["en_core_web_sm", "en_core_web_md", "en_core_web_lg", "en_core_web_trf"]),
    Field("use_ner", "Named entity recognition", "bool", True, "parse", S_EXTRACT,
          "spaCy NER: people, organisations, places, products, dates, money…"),
    Field("ner_labels", "NER labels", "multiselect",
          [x for x in NER_LABELS if x not in ("CARDINAL", "ORDINAL", "PERCENT")],
          "parse", S_EXTRACT, "Which spaCy entity labels become nodes.", options=NER_LABELS),
    Field("use_regex", "Pattern extractors", "bool", True, "parse", S_EXTRACT,
          "Regular expressions for file paths, URLs, IPs, hashes, CVEs, env vars, …"),
    Field("regex_categories", "Enabled patterns", "multiselect",
          [d.name for d in REGEX_DEFS if d.default], "parse", S_EXTRACT,
          options=[d.name for d in REGEX_DEFS]),
    Field("use_gazetteer", "Built-in vocabularies", "bool", True, "parse", S_EXTRACT,
          "Curated term lists (software, AI models, vehicle makes, …)."),
    Field("use_wordnet", "WordNet categories", "bool", True, "parse", S_EXTRACT,
          "Classify common nouns through WordNet hypernyms: garlic→food, sedan→vehicle, surgeon→role."),
    Field("wordnet_categories", "WordNet categories kept", "multiselect", WORDNET_CATEGORIES, "parse",
          S_EXTRACT, "Nouns falling into other categories become plain concepts.", options=WORDNET_CATEGORIES),
    Field("wordnet_senses", "Senses considered", "int", 3, "parse", S_EXTRACT,
          "How many WordNet senses of a word may vote for its category.", min=1, max=3),
    Field("use_concepts", "Noun-phrase concepts", "bool", True, "parse", S_EXTRACT,
          "Keep uncategorised noun phrases as generic concept nodes."),
    Field("concept_mode", "Concept granularity", "select", "compound", "parse", S_EXTRACT,
          "compound: 'sports car'; head: 'car'; phrase: 'red sports car'.",
          options=["compound", "head", "phrase"]),
    Field("extract_relations", "Subject–verb–object relations", "bool", True, "parse", S_EXTRACT,
          "Dependency parse each sentence; link entities via their verb."),
    Field("min_term_length", "Minimum term length", "int", 2, "parse", S_EXTRACT, min=1, max=10),
    Field("merge_case", "Merge case variants", "bool", True, "parse", S_EXTRACT,
          "Treat 'Docker' and 'docker' as one node (literals like paths stay case-sensitive)."),
    # --------------------------------------------------------- text sources
    Field("analyze_thoughts", "Analyse thoughts", "bool", True, "parse", S_SOURCES),
    Field("analyze_tool_args", "Analyse tool arguments", "bool", True, "parse", S_SOURCES),
    Field("analyze_tool_results", "Analyse tool results", "bool", True, "parse", S_SOURCES),
    Field("tool_result_nlp", "Tool result analysis depth", "select", "patterns", "parse", S_SOURCES,
          "patterns: regex + vocabularies only (fast, low noise). full: also NER and noun phrases.",
          options=["patterns", "full"]),
    Field("code_nlp", "Code block analysis depth", "select", "patterns", "parse", S_SOURCES,
          options=["patterns", "full"]),
    Field("max_tool_result_chars", "Max tool result characters", "int", 20000, "parse", S_SOURCES,
          "Longer tool outputs are truncated before display and analysis.", min=500, max=500000, step=500),
    Field("max_paragraph_lines", "Max lines per paragraph", "int", 40, "parse", S_SOURCES,
          "Long blocks without blank lines are split into chunks of this many lines.", min=5, max=500),
    Field("paragraph_split", "Paragraph boundary", "select", "blank_line", "parse", S_SOURCES,
          options=["blank_line", "line"]),
    # ----------------------------------------------------- custom vocabulary
    Field("custom_gazetteer", "Custom vocabulary", "textarea",
          "# category: term, term, …\n# food: gochujang, za'atar\n", "parse", S_CUSTOM,
          "One line per category. Any category name works; unknown ones get a neutral colour."),
    Field("custom_regex", "Custom patterns", "textarea",
          "# category: regular expression\n# ticket: \\bJIRA-\\d+\\b\n", "parse", S_CUSTOM),
    Field("ignore_terms", "Ignored terms", "textarea",
          "thing\nthings\nway\nlot\nbit\nkind\nsort\nsomething\nanything\neverything\nnothing\n"
          "one\nones\nexample\nstuff\nlet\nokay\nok\nsure\nplease\nthanks\nthank\nhello\nhi\n"
          "question\nanswer\npoint\ncase\nfact\npart\nthat\nthis\nit\nyes\nno\n",
          "parse", S_CUSTOM, "One per line (case-insensitive). These never become nodes."),
    # --------------------------------------------------------- graph content
    Field("include_conversation_nodes", "Conversation nodes", "bool", True, "graph", S_GRAPH),
    Field("include_system", "System messages", "bool", False, "graph", S_GRAPH),
    Field("include_thoughts", "Thought nodes", "bool", True, "graph", S_GRAPH),
    Field("include_tool_args", "Tool argument nodes", "bool", True, "graph", S_GRAPH),
    Field("include_tool_results", "Tool result nodes", "bool", True, "graph", S_GRAPH),
    Field("tool_hubs", "Tool hub nodes", "bool", False, "graph", S_GRAPH,
          "Add one node per tool name linked to all its calls."),
    Field("include_entities", "Entity nodes", "bool", True, "graph", S_GRAPH),
    Field("min_mentions", "Minimum mentions", "int", 1, "graph", S_GRAPH,
          "Entities mentioned fewer times are dropped.", min=1, max=50),
    Field("max_entities", "Maximum entities", "int", 1500, "graph", S_GRAPH,
          "Keep only the most frequent entities.", min=50, max=20000, step=50),
    Field("merge_across_conversations", "Share entities across conversations", "bool", True, "graph",
          S_GRAPH, "One node per entity for the whole corpus (links conversations) vs one per conversation."),
    Field("arg_value_max", "Argument label length", "int", 48, "graph", S_GRAPH, min=10, max=400),
    Field("drop_isolated", "Drop isolated nodes", "bool", True, "graph", S_GRAPH),
    Field("tech_context", "Context-aware technical nouns", "bool", True, "graph", S_GRAPH,
          "In technical conversations, words like client, session or fixture are classed as software."),
    # ----------------------------------------------------- edges & relations
    Field("flow_edges", "Conversation flow edges", "bool", True, "graph", S_EDGES,
          "Chain user → LLM → tool call → result → … in order."),
    Field("mention_edges", "Mention edges", "bool", True, "graph", S_EDGES,
          "Link each message / tool call to the entities it mentions."),
    Field("relation_edges", "Relation edges", "bool", True, "graph", S_EDGES,
          "Entity → entity edges labelled with the connecting verb."),
    Field("path_aliases", "Link path variants", "bool", True, "graph", S_EDGES,
          "Connect '/srv/app/main.py' with 'app/main.py' and 'main.py'."),
    Field("cooccurrence_edges", "Co-occurrence edges", "bool", False, "graph", S_EDGES,
          "Link entities that appear in the same sentence/paragraph."),
    Field("cooccurrence_scope", "Co-occurrence scope", "select", "sentence", "graph", S_EDGES,
          options=["sentence", "paragraph"]),
    Field("cooccurrence_min", "Minimum co-occurrences", "int", 2, "graph", S_EDGES, min=1, max=50),
    # ------------------------------------------------------ layout & physics
    Field("layout", "Layout", "select", "force", "view", S_LAYOUT,
          "force: free physics. layers: bands for thoughts / dialogue / actions / entities along a timeline.",
          options=["force", "layers"]),
    Field("solver", "Physics solver", "select", "forceAtlas2Based", "view", S_LAYOUT,
          options=["forceAtlas2Based", "barnesHut", "repulsion"]),
    Field("gravity", "Gravitational constant", "int", -60, "view", S_LAYOUT, min=-30000, max=0, step=10),
    Field("central_gravity", "Central gravity", "float", 0.01, "view", S_LAYOUT, min=0, max=1, step=0.005),
    Field("spring_length", "Spring length", "int", 120, "view", S_LAYOUT, min=10, max=600),
    Field("spring_constant", "Spring constant", "float", 0.08, "view", S_LAYOUT, min=0, max=1, step=0.01),
    Field("damping", "Damping", "float", 0.4, "view", S_LAYOUT, min=0, max=1, step=0.05),
    Field("avoid_overlap", "Avoid overlap", "float", 0.2, "view", S_LAYOUT, min=0, max=1, step=0.05),
    Field("stabilization", "Stabilisation iterations", "int", 250, "view", S_LAYOUT, min=0, max=3000, step=50),
    Field("cluster_mode", "Clustering", "select", "auto", "view", S_LAYOUT,
          "Collapse the graph into one node per conversation, host, user or agent. Terms shared between groups "
          "stay outside so you can see what links them. auto picks host → user → agent → conversation and "
          "drills down when you click a cluster.",
          options=["auto", "off", "conversation", "host", "user", "agent"]),
    Field("cluster_auto_min", "Auto-cluster above", "int", 400, "view", S_LAYOUT,
          "In auto mode, only cluster when more than this many nodes are visible.", min=0, max=20000, step=50),
    Field("keep_physics", "Keep physics running", "bool", False, "view", S_LAYOUT,
          "Otherwise physics freezes after stabilising (smoother on big graphs)."),
    Field("layer_gap", "Layer spacing (layers layout)", "int", 200, "view", S_LAYOUT, min=80, max=1200, step=10),
    Field("step_gap", "Timeline step (layers layout)", "int", 170, "view", S_LAYOUT, min=40, max=800, step=10),
    # ------------------------------------------------------------ appearance
    Field("theme", "Theme", "select", "auto", "view", S_LOOK, options=["auto", "light", "dark"]),
    Field("size_by", "Entity size by", "select", "mentions", "view", S_LOOK,
          options=["mentions", "degree", "fixed"]),
    Field("node_min", "Minimum node size", "int", 8, "view", S_LOOK, min=2, max=60),
    Field("node_max", "Maximum node size", "int", 38, "view", S_LOOK, min=4, max=150),
    Field("font_size", "Label size", "int", 13, "view", S_LOOK, min=6, max=40),
    Field("label_max", "Label length", "int", 32, "view", S_LOOK, min=4, max=200),
    Field("edge_labels", "Edge labels", "select", "relations", "view", S_LOOK,
          options=["none", "relations", "all"]),
    Field("edge_smooth", "Edge style", "select", "continuous", "view", S_LOOK,
          options=["continuous", "dynamic", "straight", "curvedCW"]),
    Field("arrows", "Arrows", "bool", True, "view", S_LOOK),
    Field("color_flow_by_conversation", "Colour flow edges per conversation", "bool", True, "view", S_LOOK),
    Field("thought_opacity", "Thought layer opacity", "float", 0.75, "view", S_LOOK, min=0.1, max=1, step=0.05),
    Field("hover_tooltips", "Hover shows paragraph", "bool", True, "view", S_LOOK),
    # ------------------------------------------------------ transcript panel
    Field("context_before", "Paragraphs before match", "int", 1, "view", S_PANEL, min=0, max=20),
    Field("context_after", "Paragraphs after match", "int", 1, "view", S_PANEL, min=0, max=20),
    Field("max_matches", "Maximum matches listed", "int", 200, "view", S_PANEL, min=10, max=5000, step=10),
    Field("underline_entities", "Underline entities in transcript", "bool", True, "view", S_PANEL,
          "Click an underlined word to jump to its node."),
    Field("collapse_tool_results", "Collapse long tool results", "bool", True, "view", S_PANEL),
    Field("panel_width", "Panel width (px)", "int", 440, "view", S_PANEL, min=280, max=1200, step=10),
    # ------------------------------------------------------------ security
    Field("sec_enabled", "Security analysis", "bool", True, "graph", S_SECURITY,
          "Flag exfiltration, downloads, exposed secrets, sensitive-file access and destructive commands."),
    Field("sec_dataflow", "Dataflow chains", "bool", True, "graph", S_SECURITY,
          "Link source → action → sink within a tool call (e.g. a file leaving to a domain)."),
    Field("sec_secrets", "Secret scanning", "bool", True, "graph", S_SECURITY,
          "Detect private keys, cloud keys, tokens and password assignments in text, arguments and output."),
    Field("sec_scan_results", "Scan tool output for secrets", "bool", True, "graph", S_SECURITY,
          "Also scan command/tool output, not just prompts and arguments."),
    Field("sec_sensitive_paths", "Sensitive-file access", "bool", True, "graph", S_SECURITY,
          "Flag reads of credential locations such as /etc/shadow, ~/.ssh/id_rsa, .aws/credentials, .env."),
    Field("sec_risky_ops", "Risky / destructive operations", "bool", True, "graph", S_SECURITY,
          "Flag recursive deletes, disk overwrites, DROP/TRUNCATE, force pushes and history/log clearing."),
    Field("sec_min_severity", "Minimum severity", "select", "low", "graph", S_SECURITY,
          "Hide findings below this severity.", options=SEVERITIES),
    Field("security_watchlist", "Analyst watchlist", "textarea",
          "# one per line:  Label: <regex>    (optional [severity] prefix)\n"
          "# [high] Data staging: base64\s+-d\n"
          "# [medium] Package install: \bpip\s+install\b\n",
          "graph", S_SECURITY,
          "Your own terms or patterns to flag. Matched (case-insensitive) against every message, argument and result."),
]

# Colour overrides for every node type / category
for _k in NODE_TYPES + CATEGORIES:
    SCHEMA.append(Field(f"color.{_k.key}", _k.label, "color", _k.color, "view", S_COLORS))

SCHEMA_BY_KEY = {f.key: f for f in SCHEMA}


def defaults() -> dict[str, Any]:
    return {f.key: copy.deepcopy(f.default) for f in SCHEMA}


def coerce(key: str, value: Any) -> Any:
    f = SCHEMA_BY_KEY[key]
    if f.type == "bool":
        return bool(value) if not isinstance(value, str) else value.lower() in ("1", "true", "yes", "on")
    if f.type in ("int", "float"):
        num = int(float(value)) if f.type == "int" else float(value)
        if f.min is not None:
            num = max(num, type(num)(f.min))
        if f.max is not None:
            num = min(num, type(num)(f.max))
        return num
    if f.type == "multiselect":
        return [v for v in (value or []) if not f.options or v in f.options]
    if f.type == "select":
        return value if value in f.options else f.default
    if f.type == "color":
        return value if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{3,8}", value) else f.default
    return "" if value is None else str(value)


class SettingsStore:
    def __init__(self, path: Path | None):
        self.path = path
        self.values = defaults()
        if path and path.exists():
            try:
                self.update(json.loads(path.read_text()), save=False)
            except (OSError, ValueError):
                pass

    def update(self, changes: dict[str, Any], save: bool = True) -> set[str]:
        """Apply changes and return the set of affected scopes."""
        scopes: set[str] = set()
        for key, value in changes.items():
            if key not in SCHEMA_BY_KEY:
                continue
            new = coerce(key, value)
            if new != self.values.get(key):
                self.values[key] = new
                scopes.add(SCHEMA_BY_KEY[key].scope)
        if save:
            self.save()
        return scopes

    def reset(self) -> None:
        self.values = defaults()
        self.save()

    def save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.values, indent=2))

    def scoped(self, scope: str) -> dict[str, Any]:
        return {k: v for k, v in self.values.items() if SCHEMA_BY_KEY[k].scope == scope}

    def fingerprint(self, scope: str) -> str:
        return hashlib.sha1(json.dumps(self.scoped(scope), sort_keys=True).encode()).hexdigest()[:12]

    def schema_json(self) -> list[dict[str, Any]]:
        return [asdict(f) for f in SCHEMA]
