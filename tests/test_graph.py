from collections import Counter

import networkx as nx

from synthsift.graph.builder import build_graph
from synthsift.graph.export import to_graphml, to_pyvis_html


def test_tool_calls_are_individual_nodes_with_arguments(sample_workspace):
    G = sample_workspace.graph
    calls = [n for n, d in G.nodes(data=True) if d["type"] == "tool_call"]
    total_calls = sum(e.type == "tool_call" for evs in sample_workspace.events.values() for e in evs)
    assert len(calls) == total_calls > 100
    bash = next(n for n in calls if G.nodes[n]["label"].startswith("bash #"))
    args = [v for _, v, d in G.out_edges(bash, data=True) if d["type"] == "arg"]
    assert args and all(G.nodes[a]["label"].startswith("command:") for a in args)


def test_thoughts_live_on_their_own_layer(sample_workspace):
    G = sample_workspace.graph
    thoughts = [n for n, d in G.nodes(data=True) if d["type"] == "thought"]
    assert thoughts and all(G.nodes[n]["layer"] == "thought" for n in thoughts)
    edge_types = Counter(d["type"] for u, v, d in G.edges(data=True) if u in thoughts or v in thoughts)
    assert edge_types["thinks"] and edge_types["leads_to"]
    # something is only ever mentioned while thinking
    assert any(d["type"] == "entity" and d["layer"] == "thought" for _, d in G.nodes(data=True))


def test_entity_occurrences_point_at_the_text(sample_workspace):
    G = sample_workspace.graph
    paras = sample_workspace.paragraphs
    checked = 0
    for _, d in G.nodes(data=True):
        if d["type"] != "entity":
            continue
        for pid, s, e in d["occ"]:
            assert 0 <= s < e <= len(paras[pid].text)
            checked += 1
    assert checked > 500


def test_expected_domain_entities(sample_workspace):
    labels = {d["label"].lower(): d.get("category") for _, d in sample_workspace.graph.nodes(data=True) if d["type"] == "entity"}
    assert labels["10.0.4.35"] == "ip"
    assert labels["/opt/orders/requirements.txt"] == "file_path"
    assert labels["parmesan"] == "food"
    assert labels["honda civic"] == "vehicle"
    assert labels["cve-2017-11882"] == "cve"
    assert labels["ada lovelace"] == "person"


def test_shared_entities_connect_conversations(sample_workspace):
    G = sample_workspace.graph
    assert any(len(d.get("conv", [])) > 1 for _, d in G.nodes(data=True) if d["type"] == "entity")


def _rebuild(ws, **overrides):
    cfg = {**ws.settings.values, **overrides}
    return build_graph(ws._conv_meta(), ws.events, ws.paragraphs, ws.analysis, cfg)


def test_graph_settings(sample_workspace):
    ws = sample_workspace
    per_conv = _rebuild(ws, merge_across_conversations=False)
    assert all(len(d["conv"]) == 1 for _, d in per_conv.nodes(data=True) if d["type"] == "entity")
    frequent = _rebuild(ws, min_mentions=3)
    assert all(d["count"] >= 3 for _, d in frequent.nodes(data=True) if d["type"] == "entity")
    bare = _rebuild(ws, include_thoughts=False, include_tool_args=False, include_entities=False)
    assert Counter(d["type"] for _, d in bare.nodes(data=True)).keys() <= {"conversation", "user", "assistant", "tool_call", "tool_result"}
    hubs = _rebuild(ws, tool_hubs=True)
    assert any(d["type"] == "tool_hub" for _, d in hubs.nodes(data=True))


def test_exports(sample_workspace):
    html = to_pyvis_html(sample_workspace.graph, sample_workspace.paragraphs)
    assert "vis.Network" in html or "new vis" in html
    graphml = to_graphml(sample_workspace.graph)
    import io

    H = nx.read_graphml(io.BytesIO(graphml))
    assert H.number_of_nodes() == sample_workspace.graph.number_of_nodes()
