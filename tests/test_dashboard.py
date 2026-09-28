"""What the dashboard reads from the payload: sub-agent calls and sessions, list hits by kind with their
matched values, and the runner rebuilding a corpus module's tables when its version changes."""

from synthsift.db import open_storage
from synthsift.harnesses.openclaw import events_to_conversation
from synthsift.models import Block, Conversation, Message
from synthsift.modules import registry
from synthsift.modules.runner import Runner
from synthsift.nlp import security
from synthsift.segment import segment
from synthsift.settings import defaults
from test_modules import _ioc_run


def conv(harness, *tools):
    msgs = [Message(role="user", blocks=[Block.text_block("go")])]
    for i, t in enumerate(tools):
        msgs.append(Message(role="assistant", blocks=[Block.tool_call(t, {"prompt": "look around"}, f"c{i}")]))
    return Conversation(id="c1", harness=harness, messages=msgs)


def test_tool_calls_that_start_a_subagent_are_marked():
    evs, _ = segment(conv("claude_code", "Task", "Bash", "Agent"), defaults())
    calls = [e for e in evs if e.type == "tool_call"]
    assert [(e.tool_name, e.subagent) for e in calls] == [("Task", True), ("Bash", False), ("Agent", True)]
    assert calls[0].to_json()["sub"] == 1 and "sub" not in calls[1].to_json()
    evs, _ = segment(conv("openclaw", "sessions_spawn", "exec"), defaults())
    assert [e.subagent for e in evs if e.type == "tool_call"] == [True, False]
    evs, _ = segment(conv("unknown-agent", "Task"), defaults())  # only known harnesses name their sub-agent tools
    assert not any(e.subagent for e in evs)


def test_openclaw_sessions_spawned_by_another_are_subagents():
    rows = [{"type": "message", "timestamp": "2026-06-01T10:00:00Z", "message": {"role": "user", "content": "hi"}}]
    assert events_to_conversation(rows, {"spawned_by": "agent:main:main"}).meta["subagent"] is True
    assert events_to_conversation(rows, {"session_key": "agent:main:subagent:7f3"}).meta["subagent"] is True
    assert "subagent" not in events_to_conversation(rows, {"session_key": "agent:main:main"}).meta


def test_payload_carries_subagents_and_hit_values(sample_workspace):
    p = sample_workspace.payload
    subs = [e for e in p["events"] if e.get("sub")]
    assert subs and {e["tool"] for e in subs} == {"Task"}
    assert any(c["meta"].get("subagent") for c in p["conversations"])
    assert all("value" in f for f in p["findings"])


def test_list_hits_split_into_ioc_and_keyword(tmp_path):
    items, hits, _, _ = _ioc_run(tmp_path, ["Beacon to cdn.bad-site.example.test after the garlic bread order"],
                                 {"watch.txt": "bad-site.example.test\ngarlic bread\n"})
    assert {x[4] for x in hits[items[0].hash]} == {"domain", "keyword"}


def test_ioc_and_keyword_findings(tmp_path, sample_zip):
    """List hits become findings of two categories, keeping the list entry that matched."""
    from fastapi.testclient import TestClient

    from synthsift.server import create_app
    from synthsift.store import Workspace

    ws = Workspace(tmp_path)
    client = TestClient(create_app(ws))
    ws.add_zip("samples.zip", sample_zip)
    ws.wait()
    client.post("/api/lists", files=[("files", ("watch.txt", b"api.example.com\ngarlic bread\n", "text/plain"))])
    client.put("/api/settings", json={"mod.ioc": True, "security_watchlist": "[low] Privileged: \\bsudo\\b"})
    ws.wait()
    fs = client.get("/api/graph").json()["findings"]
    by = {}
    for f in fs:
        by.setdefault(f["category"], set()).add(f["value"])
    assert by["ioc"] == {"api.example.com"} and by["keyword"] == {"garlic bread"} and by["watchlist"] == {"sudo"}
    assert set(security.CATEGORIES) >= {"ioc", "keyword", "watchlist"}
    ws.close()


def test_corpus_module_tables_are_rebuilt_on_a_new_version(tmp_path):
    st = open_storage(None)
    (tmp_path / "lists").mkdir()
    (tmp_path / "lists" / "l.txt").write_text("example.test\n")
    cfg = {**defaults(), "mod.nlp": False, "mod.ioc": True}
    Runner(st, cfg, data_dir=tmp_path).run()
    st.execute('ALTER TABLE x_ioc_hits ADD COLUMN "stale" INTEGER')  # a column an older version wrote
    fp = st.get_state("fp:ioc")
    st.set_state("fp:ioc", "0" * 16 + fp[16:])  # as if the module's version changed
    Runner(st, cfg, data_dir=tmp_path).run()
    cols = [r[0] for r in st.query("SELECT column_name FROM information_schema.columns WHERE table_name = 'x_ioc_hits'")]
    assert cols == registry()["ioc"].tables[1].names
