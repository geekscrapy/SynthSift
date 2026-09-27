"""All user-tunable knobs, declared once.

Each field has a *scope*, which decides what a change re-runs:

* ``segment`` – re-splits transcripts into paragraphs, then everything below
* ``parse``   – re-runs the enrichment modules the setting belongs to (and the
  modules that depend on them); other modules keep their stored results
* ``graph``   – rebuilds the graph from the stored module results (fast)
* ``view``    – purely client side (layout, physics, colours, panel behaviour)
* ``system``  – how processing runs (worker processes …); re-runs nothing

Enrichment modules (:mod:`synthsift.modules`) declare their own options; they
are appended here, with an on/off switch per module, under *Modules*.  The
settings page is rendered generically from :data:`SCHEMA`, so adding a knob
here (or to a module) is enough to expose it in the UI.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .categories import CATEGORIES, NODE_TYPES
from .fields import Field  # noqa: F401  (re-exported)

S_SOURCES = "Text sources"
S_PROCESSING = "Processing"
S_GRAPH = "Graph content"
S_EDGES = "Edges & relations"
S_LAYOUT = "Layout & physics"
S_LOOK = "Appearance"
S_PANEL = "Transcript panel"
S_COLORS = "Colours"

SCHEMA: list[Field] = [
    # --------------------------------------------------------- text sources
    Field("analyze_thoughts", "Analyse thoughts", "bool", True, "parse", S_SOURCES),
    Field("analyze_tool_args", "Analyse tool arguments", "bool", True, "parse", S_SOURCES),
    Field("analyze_tool_results", "Analyse tool results", "bool", True, "parse", S_SOURCES),
    Field("tool_result_nlp", "Tool result analysis depth", "select", "patterns", "parse", S_SOURCES,
          "patterns: regex + vocabularies only (fast, low noise). full: also NER and noun phrases.",
          options=["patterns", "full"]),
    Field("code_nlp", "Code block analysis depth", "select", "patterns", "parse", S_SOURCES,
          options=["patterns", "full"]),
    Field("max_tool_result_chars", "Max tool result characters", "int", 20000, "segment", S_SOURCES,
          "Longer tool outputs are truncated before display and analysis.", min=500, max=500000, step=500),
    Field("max_paragraph_lines", "Max lines per paragraph", "int", 40, "segment", S_SOURCES,
          "Long blocks without blank lines are split into chunks of this many lines.", min=5, max=500),
    Field("paragraph_split", "Paragraph boundary", "select", "blank_line", "segment", S_SOURCES,
          options=["blank_line", "line"]),
    # ------------------------------------------------------------ processing
    Field("workers", "Worker processes", "int", 0, "system", S_PROCESSING,
          "Paragraph modules run in this many processes on large corpora (0 = one per CPU core, 1 = no extra "
          "processes). Modules that don't depend on each other also run side by side.", min=0, max=64),
    Field("parallel_min_paragraphs", "Use processes above (paragraphs)", "int", 3000, "system", S_PROCESSING,
          "Smaller batches of new paragraphs run in threads (no process start-up cost).", min=0, max=1_000_000, step=500),
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
]

# Colour overrides for every node type / category
for _k in NODE_TYPES + CATEGORIES:
    SCHEMA.append(Field(f"color.{_k.key}", _k.label, "color", _k.color, "view", S_COLORS))


def _module_fields() -> list[Field]:
    from .modules import registry, switch_field

    out: list[Field] = []
    for cls in registry().values():
        sw = switch_field(cls)
        if sw is not None:
            out.append(sw)
        out.extend(cls.options)
    return out


SCHEMA.extend(_module_fields())
SCHEMA_BY_KEY = {f.key: f for f in SCHEMA}
assert len(SCHEMA_BY_KEY) == len(SCHEMA), "duplicate settings key"


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
    if isinstance(value, (list, tuple)) and f.type in ("hidden", "lists", "textarea"):
        value = "\n".join(str(v) for v in value)
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
