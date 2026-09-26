"""Exports: standalone pyvis HTML, GraphML (Gephi, yEd, Cytoscape) and JSON."""

from __future__ import annotations

import io
import json
from typing import Any, Iterable

import networkx as nx

from ..categories import CATEGORIES, NODE_TYPES

_COLORS = {k.key: k.color for k in NODE_TYPES}
_CAT_COLORS = {k.key: k.color for k in CATEGORIES}
_SHAPES = {
    "conversation": "star", "user": "box", "assistant": "box", "system": "box", "thought": "box",
    "tool_call": "hexagon", "tool_arg": "dot", "tool_result": "database", "tool_hub": "diamond",
}


def subgraph(G: nx.MultiDiGraph, convs: Iterable[str] | None) -> nx.MultiDiGraph:
    if convs is None:
        return G
    wanted = set(convs)
    keep = [n for n, d in G.nodes(data=True) if not d.get("conv") or wanted & set(d["conv"])]
    return G.subgraph(keep).copy()


def to_pyvis_html(G: nx.MultiDiGraph, paragraphs: dict[str, Any] | None = None, dark: bool = False) -> str:
    from pyvis.network import Network

    net = Network(height="100vh", width="100%", directed=True, cdn_resources="in_line",
                  bgcolor="#202124" if dark else "#ffffff", font_color="#e8eaed" if dark else "#202124")
    for nid, d in G.nodes(data=True):
        ntype = d.get("type", "entity")
        color = _CAT_COLORS.get(d.get("category", ""), "#A8C7FA") if ntype == "entity" else _COLORS.get(ntype, "#80868B")
        tip = d.get("title") or d.get("label", "")
        if paragraphs and d.get("occ"):
            pid = d["occ"][0][0]
            if pid in paragraphs:
                tip = paragraphs[pid].text[:500]
        size = 10 + min(int(d.get("count", 1)), 40) if ntype == "entity" else 16
        kwargs = {"shape": _SHAPES.get(ntype, "dot"), "color": color, "title": tip, "size": size}
        if ntype == "thought":
            kwargs["shapeProperties"] = {"borderDashes": [6, 4]}
        if ntype in ("user", "assistant", "system", "thought"):
            kwargs["font"] = {"color": "#ffffff"}
        net.add_node(nid, label=str(d.get("label", nid))[:40], group=d.get("category") or ntype, **kwargs)
    for u, v, d in G.edges(data=True):
        dashes = bool(d.get("thought"))
        net.add_edge(u, v, title=d.get("label") or d.get("type"), label=d.get("label", "") if d.get("type") == "relation" else "",
                     dashes=dashes, value=d.get("w", 1), arrows="to")
    net.set_options(json.dumps({
        "physics": {"solver": "forceAtlas2Based", "stabilization": {"iterations": 250},
                    "forceAtlas2Based": {"gravitationalConstant": -60, "springLength": 120}},
        "interaction": {"hover": True, "navigationButtons": True},
        "edges": {"smooth": {"type": "continuous"}, "color": {"inherit": "from"}, "font": {"size": 10}},
    }))
    return net.generate_html(notebook=False)


def to_graphml(G: nx.MultiDiGraph) -> bytes:
    H = nx.MultiDiGraph()
    for nid, d in G.nodes(data=True):
        H.add_node(nid, **{k: (v if isinstance(v, (str, int, float, bool)) else json.dumps(v))
                           for k, v in d.items() if k != "occ"})
    for u, v, k, d in G.edges(keys=True, data=True):
        H.add_edge(u, v, key=k, **{a: (b if isinstance(b, (str, int, float, bool)) else json.dumps(b)) for a, b in d.items()})
    buf = io.BytesIO()
    nx.write_graphml(H, buf)
    return buf.getvalue()
