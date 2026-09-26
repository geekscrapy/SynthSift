"""Build the conversation knowledge / flow graph with networkx.

Node kinds
  structural: conversation, user, assistant, system, thought, tool_call,
              tool_arg, tool_result, tool_hub
  entity:     anything the NLP pipeline extracted (type == "entity",
              ``category`` says what kind)

Layers (used by the UI to separate what was *thought* from what was *done*)
  dialogue – conversation, user, assistant, system
  thought  – thoughts, and entities that only ever appear in thoughts
  action   – tool calls, their arguments and results
  entity   – everything extracted from dialogue / actions
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

import networkx as nx

from ..categories import NODE_TYPES
from ..nlp.pipeline import ParaResult
from ..segment import Event, Paragraph

LAYER_OF = {k.key: k.layer for k in NODE_TYPES}
TECH_CATEGORIES = {
    "file_path", "url", "domain", "ip", "host", "hash", "uuid", "cve", "version", "env_var", "error", "code",
    "software", "cloud", "mac",
}
# Nouns whose everyday WordNet sense is wrong inside a technical conversation
# ("client" is software, not a customer; "fixture" is a test fixture).
TECH_NOUNS = {
    "client", "server", "session", "module", "fixture", "test", "tests", "caller", "host", "port", "container",
    "node", "cluster", "branch", "commit", "shell", "thread", "process", "kernel", "driver", "library",
    "package", "bug", "log", "logs", "token", "key", "pipe", "socket", "worker", "job", "queue", "stack",
    "heap", "cache", "pool", "handler", "hook", "patch", "release", "build", "deploy", "deployment",
    "namespace", "service", "instance", "image", "volume", "pod", "manifest", "schema", "table", "query",
    "index", "migration", "function", "method", "class", "object", "variable", "string", "array", "file",
    "directory", "folder", "repository", "script", "command", "terminal", "console", "window", "tab", "cursor",
    "bucket", "secret", "webhook", "endpoint", "request", "response", "payload", "header", "cookie", "proxy",
    "router", "gateway", "firewall", "agent", "model", "prompt", "context", "tool", "runner", "pipeline",
}
SOURCE_WEIGHT = {"custom": 4.0, "regex": 3.0, "vocab": 2.5, "ner": 2.0, "wordnet": 1.0, "concept": 0.5}


@dataclass
class _EntityAcc:
    count: int = 0
    occ: list[list[Any]] = field(default_factory=list)
    convs: set[str] = field(default_factory=set)
    cats: Counter = field(default_factory=Counter)
    surfaces: Counter = field(default_factory=Counter)
    non_thought: bool = False


def _truncate(text: str, n: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def build_graph(
    conversations: list[dict[str, Any]],
    events: dict[str, list[Event]],
    paragraphs: dict[str, Paragraph],
    analysis: dict[str, ParaResult],
    cfg: dict[str, Any],
) -> nx.MultiDiGraph:
    """conversations: [{id, title, …}], events: conv id -> events in order."""
    G = nx.MultiDiGraph()
    merge = cfg.get("merge_across_conversations", True)
    arg_max = int(cfg.get("arg_value_max", 48))

    def add_edge(u: str, v: str, etype: str, **attrs: Any) -> None:
        G.add_edge(u, v, key=etype + attrs.get("label", ""), type=etype, **attrs)

    ent_acc: dict[str, _EntityAcc] = defaultdict(_EntityAcc)
    mention_edges: dict[tuple[str, str], dict[str, Any]] = {}
    relations: Counter = Counter()
    relation_conv: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    cooc: Counter = Counter()

    def ent_id(conv: str, key: str) -> str:
        return f"ent:{key}" if merge else f"ent:{conv}:{key}"

    for conv in conversations:
        cid = conv["id"]
        evs = events.get(cid, [])
        spine_prev: str | None = None
        if cfg.get("include_conversation_nodes", True):
            G.add_node(f"conv:{cid}", label=_truncate(conv["title"], 40), type="conversation",
                       layer="dialogue", conv=[cid], occ=[[evs[0].paragraphs[0], 0, 0]] if evs and evs[0].paragraphs else [],
                       seq=-1, title=conv["title"])
            spine_prev = f"conv:{cid}"
        pending_thoughts: list[str] = []
        included: dict[str, str] = {}  # event id -> node id for mentions
        calls: dict[str, str] = {}

        for ev in evs:
            if ev.type == "system" and not cfg.get("include_system", False):
                continue
            if ev.type == "thought" and not cfg.get("include_thoughts", True):
                continue
            if ev.type == "tool_result" and not cfg.get("include_tool_results", True):
                continue
            occ = [[pid, 0, len(paragraphs[pid].text)] for pid in ev.paragraphs]
            first_text = paragraphs[ev.paragraphs[0]].text if ev.paragraphs else ""
            G.add_node(ev.id, label=ev.label, type=ev.type, layer=LAYER_OF.get(ev.type, "dialogue"), conv=[cid],
                       occ=occ, seq=ev.seq, event=ev.id, title=_truncate(first_text, 300),
                       **({"tool": ev.tool_name} if ev.tool_name else {}),
                       **({"error": True} if ev.is_error else {}))
            included[ev.id] = ev.id

            if ev.type == "thought":
                if spine_prev and cfg.get("flow_edges", True):
                    add_edge(spine_prev, ev.id, "thinks", conv=cid, thought=True)
                pending_thoughts.append(ev.id)
                continue

            if cfg.get("flow_edges", True):
                if spine_prev:
                    add_edge(spine_prev, ev.id, "flow", conv=cid)
                for t in pending_thoughts:
                    add_edge(t, ev.id, "leads_to", conv=cid, thought=True)
            pending_thoughts = []
            spine_prev = ev.id

            if ev.type == "tool_call":
                if ev.tool_call_id:
                    calls[ev.tool_call_id] = ev.id
                if cfg.get("include_tool_args", True):
                    for key, pid in ev.arg_paragraphs.items():
                        value = paragraphs[pid].text.split(":", 1)[-1].strip()
                        aid = f"arg:{ev.id}:{key}"
                        G.add_node(aid, label=f"{key}: {_truncate(value, arg_max)}", type="tool_arg", layer="action",
                                   conv=[cid], occ=[[pid, 0, len(paragraphs[pid].text)]], seq=ev.seq,
                                   event=ev.id, title=_truncate(paragraphs[pid].text, 600))
                        add_edge(ev.id, aid, "arg", conv=cid)
                        included[pid] = aid  # mentions inside this argument hang off the arg node
                if cfg.get("tool_hubs", False) and ev.tool_name:
                    hub = f"toolhub:{ev.tool_name}"
                    if hub not in G:
                        G.add_node(hub, label=ev.tool_name, type="tool_hub", layer="action", conv=[], occ=[], seq=-1)
                    G.nodes[hub]["conv"] = sorted(set(G.nodes[hub]["conv"]) | {cid})
                    add_edge(ev.id, hub, "uses", conv=cid)
            elif ev.type == "tool_result" and ev.tool_call_id in calls and cfg.get("flow_edges", True):
                if not G.has_edge(calls[ev.tool_call_id], ev.id):
                    add_edge(calls[ev.tool_call_id], ev.id, "returns", conv=cid)

        # -------------------------------------------------- entity mentions
        if not cfg.get("include_entities", True):
            continue
        for ev in evs:
            if ev.id not in included:
                continue
            is_thought = ev.type == "thought"
            for pid in ev.paragraphs:
                res = analysis.get(pid)
                if not res:
                    continue
                src_node = included.get(pid, ev.id)
                by_sent: dict[int, set[str]] = defaultdict(set)
                for m in res.mentions:
                    nid = ent_id(cid, m.key)
                    acc = ent_acc[nid]
                    acc.count += 1
                    acc.occ.append([pid, m.start, m.end])
                    acc.convs.add(cid)
                    acc.cats[m.category] += SOURCE_WEIGHT.get(m.source.split(":")[0], 1.0)
                    acc.surfaces[m.text] += 1
                    acc.non_thought |= not is_thought
                    me = mention_edges.setdefault((src_node, nid), {"w": 0, "verbs": Counter(), "conv": cid,
                                                                    "thought": is_thought})
                    me["w"] += 1
                    if m.verb:
                        me["verbs"][m.verb] += 1
                    by_sent[m.sent if cfg.get("cooccurrence_scope") == "sentence" else 0].add(nid)
                for rel in res.relations:
                    key = (ent_id(cid, rel.subj), rel.verb, ent_id(cid, rel.obj))
                    relations[key] += 1
                    relation_conv[key].add(cid)
                if cfg.get("cooccurrence_edges", False):
                    for ids in by_sent.values():
                        for a, b in combinations(sorted(ids), 2):
                            cooc[(a, b)] += 1

    # --------------------------------------------- technical conversations
    tech_conv: set[str] = set()
    if cfg.get("tech_context", True):
        for conv in conversations:
            total = tech = 0
            for ev in events.get(conv["id"], []):
                for pid in ev.paragraphs:
                    for m in analysis.get(pid, ParaResult()).mentions:
                        total += 1
                        tech += m.category in TECH_CATEGORIES
            if total and tech / total >= 0.2:
                tech_conv.add(conv["id"])

    # ------------------------------------------------------ entity nodes
    min_mentions = int(cfg.get("min_mentions", 1))
    max_entities = int(cfg.get("max_entities", 1500))
    ranked = sorted((n for n, a in ent_acc.items() if a.count >= min_mentions),
                    key=lambda n: (-ent_acc[n].count, n))[:max_entities]
    keep = set(ranked)
    for nid in ranked:
        acc = ent_acc[nid]
        specific = [(w, c) for c, w in acc.cats.items() if c != "concept"]
        category = max(specific)[1] if specific else "concept"
        label = acc.surfaces.most_common(1)[0][0]
        key = nid.split(":", 2)[-1] if not merge else nid[4:]
        if (
            tech_conv
            and category not in TECH_CATEGORIES
            and key.lower() in TECH_NOUNS
            and len(acc.convs & tech_conv) * 2 >= len(acc.convs)
        ):
            category = "software"
        G.add_node(nid, label=" ".join(label.split()), type="entity", category=category,
                   layer="entity" if acc.non_thought else "thought", conv=sorted(acc.convs),
                   occ=acc.occ, count=acc.count)

    if cfg.get("mention_edges", True):
        for (src, dst), me in mention_edges.items():
            if dst in keep and src in G:
                verb = me["verbs"].most_common(1)[0][0] if me["verbs"] else ""
                add_edge(src, dst, "mention", label=verb, w=me["w"], conv=me["conv"], thought=me["thought"])
    if cfg.get("relation_edges", True):
        for (s, verb, o), w in relations.items():
            if s in keep and o in keep:
                convs = sorted(relation_conv[(s, verb, o)])
                add_edge(s, o, "relation", label=verb, w=w, conv=convs[0] if len(convs) == 1 else "")
    if cfg.get("cooccurrence_edges", False):
        min_w = int(cfg.get("cooccurrence_min", 2))
        for (a, b), w in cooc.items():
            if w >= min_w and a in keep and b in keep:
                add_edge(a, b, "cooccurs", w=w, conv="")

    if cfg.get("path_aliases", True):
        # "/home/dev/app/main.py" and "app/main.py" are probably the same file
        paths = [n for n in keep if G.nodes[n].get("category") == "file_path"]
        by_base: dict[str, list[str]] = defaultdict(list)
        for n in paths:
            by_base[G.nodes[n]["label"].rstrip("/").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]].append(n)
        for group in by_base.values():
            if len(group) < 2 or len(group) > 50:
                continue
            for a, b in combinations(group, 2):
                la, lb = G.nodes[a]["label"], G.nodes[b]["label"]
                short, long_ = (a, b) if len(la) <= len(lb) else (b, a)
                ls, ll = G.nodes[short]["label"].lstrip("./"), G.nodes[long_]["label"]
                if ls and ll.endswith("/" + ls):
                    add_edge(long_, short, "alias", label="same file?")

    if cfg.get("drop_isolated", True):
        G.remove_nodes_from([n for n in list(G.nodes) if G.degree(n) == 0 and G.nodes[n].get("type") == "entity"])
    return G


def graph_to_json(G: nx.MultiDiGraph) -> dict[str, list[dict[str, Any]]]:
    nodes = []
    for nid, d in G.nodes(data=True):
        n = {"id": nid, **{k: v for k, v in d.items() if k != "title"}}
        n["deg"] = G.degree(nid)
        nodes.append(n)
    edges = []
    for i, (u, v, d) in enumerate(G.edges(data=True)):
        e = {"id": i, "from": u, "to": v, **d}
        edges.append(e)
    return {"nodes": nodes, "edges": edges}
