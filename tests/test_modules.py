"""Storage, the enrichment module framework and runner, IOC / keyword lists, and the persistent workspace."""

import gzip
import json
import time

import pytest
from fastapi.testclient import TestClient

from synthsift.db import Batch, open_storage, table
from synthsift.db.schema import PARA_TEXT
from synthsift.ingest import read_zip
from synthsift.modules import Deps, ParaIn, enabled_modules, para_hash, registry
from synthsift.modules.ioc import tokens
from synthsift.modules.runner import Runner, module_stats
from synthsift.nlp.pipeline import Analyzer
from synthsift.segment import segment
from synthsift.server import create_app
from synthsift.settings import SCHEMA_BY_KEY, defaults
from synthsift.store import Workspace


def cfg(**over):
    return {**defaults(), **over}


@pytest.fixture(scope="module")
def sample_paras(sample_zip):
    c = defaults()
    out = {}
    for conv in read_zip(sample_zip, "s").conversations:
        for p in segment(conv, c)[1]:
            h = para_hash(p.text, p.role, p.code)
            out.setdefault(h, ParaIn(h, p.text, p.role, p.code, p.arg))
    return list(out.values())


def fill(storage, paras):
    storage.ensure(PARA_TEXT)
    storage.insert(Batch.from_rows(PARA_TEXT, [(p.hash, p.text, p.role, p.code, p.arg) for p in paras]))


def states(report):
    return {s["name"]: s["state"] for s in report["steps"]}


# ------------------------------------------------------------------ storage
def test_duckdb_storage_roundtrip(tmp_path):
    st = open_storage(tmp_path / "x.duckdb")
    t = table("t_demo", "para_hash", ("n", "int"), ("tags", "text[]"), ("meta", "json"), ("ok", "bool"))
    st.ensure(t)
    st.insert(Batch.from_rows(t, [("a", 1, ["x", "y"], {"k": 1}, True), ("b", 2, None, None, False), ("c", 3, [], "[1]", None)]))
    assert st.count("t_demo") == 3
    assert sorted(st.select_in("t_demo", "para_hash", ["a", "c", "zz"], ["para_hash", "n", "tags"])) == [("a", 1, ["x", "y"]), ("c", 3, [])]
    st.delete_in("t_demo", "para_hash", ["a"])
    assert st.count("t_demo") == 2
    with pytest.raises(RuntimeError):
        with st.transaction():
            st.delete("t_demo")
            raise RuntimeError("roll back")
    assert st.count("t_demo") == 2
    # additive migration: a new column on an existing table
    st.ensure(table("t_demo", "para_hash", ("n", "int"), ("tags", "text[]"), ("meta", "json"), ("ok", "bool"), ("extra", "float")))
    assert st.query('SELECT count(*) FROM t_demo WHERE extra IS NULL')[0][0] == 2
    st.set_state("k", "v")
    st.set_state("k", "w")
    assert st.get_state("k") == "w" and st.get_state("missing", 5) == 5
    st.close()
    st = open_storage(tmp_path / "x.duckdb")  # persisted
    assert st.count("t_demo") == 2 and st.get_state("k") == "w"
    st.close()


# ----------------------------------------------------------------- registry
def test_registry_and_settings():
    reg = registry()
    assert {"regex", "nlp", "ioc_tokens", "ioc", "entities", "text_stats", "content_type", "security"} <= set(reg)
    names = [t.name for m in reg.values() for t in m.tables]
    assert len(names) == len(set(names)), "every table belongs to exactly one module"
    for m in reg.values():
        assert m.kind in ("extraction", "feature", "label", "analysis")
        for f in m.options:
            assert f.section == "Modules" and f.module in reg and f.key in SCHEMA_BY_KEY
        if not (m.core or m.helper_of):
            assert m.switch() in SCHEMA_BY_KEY and SCHEMA_BY_KEY[m.switch()].type == "bool"
    # a switched-on module pulls in what it needs; defaults keep the list modules off
    assert "ioc" not in enabled_modules(cfg())
    assert {"ioc", "ioc_tokens"} <= enabled_modules(cfg(**{"mod.ioc": True}))
    assert "nlp" not in enabled_modules(cfg(**{"mod.nlp": False}))


# ------------------------------------------------------------------- runner
def test_runner_matches_in_process_analyzer(sample_paras):
    st = open_storage(None)
    fill(st, sample_paras)
    c = cfg()
    report = Runner(st, c).run()
    assert set(states(report).values()) == {"done"} and "security" not in states(report)  # no corpus given
    got = {}
    for row in st.query('SELECT para_hash, key, text, category, start, "end", source FROM x_entities'):
        got.setdefault(row[0], []).append(tuple(row[1:]))
    ref = Analyzer(c).analyze([(p.hash, p.text, p.role, p.code) for p in sample_paras])
    for p in sample_paras:
        want = sorted((m.key, m.text, m.category, m.start, m.end, m.source) for m in ref[p.hash].mentions)
        assert sorted(got.get(p.hash, [])) == want, p.text[:80]


def test_runner_is_incremental(sample_paras):
    st = open_storage(None)
    fill(st, sample_paras[:-5])
    c = cfg(**{"mod.nlp": False})  # keep it quick; spaCy is covered above
    first = Runner(st, c).run()
    assert set(states(first).values()) == {"done"}
    # nothing new: everything is served from the database
    assert set(states(Runner(st, c).run()).values()) == {"cached"}
    # new paragraphs: only they are processed
    st.insert(Batch.from_rows(PARA_TEXT, [(p.hash, p.text, p.role, p.code, p.arg) for p in sample_paras[-5:]]))
    again = Runner(st, c).run()
    assert {s["name"]: s["total"] for s in again["steps"]} == {n: 5 for n in states(again)}
    # an option change re-runs that module and what depends on it, nothing else
    c2 = {**c, "regex_categories": ["url"]}
    r = Runner(st, c2).run()
    assert states(r) == {"regex": "done", "entities": "done", "text_stats": "cached", "content_type": "cached"}
    assert {x[0] for x in st.query("SELECT DISTINCT category FROM x_regex")} <= {"url"}
    # garbage collection when paragraphs leave the corpus
    st.delete("para_text", "hash = ?", (sample_paras[0].hash,))
    Runner(st, c2).gc()
    assert not st.query("SELECT 1 FROM f_text_stats WHERE para_hash = ?", (sample_paras[0].hash,))


def test_runner_worker_processes_give_the_same_rows(sample_paras):
    rows = {}
    for workers in (1, 2):
        st = open_storage(None)
        fill(st, sample_paras[:200])
        c = cfg(**{"mod.nlp": False, "workers": workers, "parallel_min_paragraphs": 0})
        report = Runner(st, c).run()
        assert {s["workers"] for s in report["steps"]} == ({"thread"} if workers == 1 else {"2 processes"})
        rows[workers] = sorted(st.query('SELECT * FROM x_entities')), sorted(st.query("SELECT * FROM l_content_type"))
    assert rows[1] == rows[2] and rows[1][0]


def test_dependency_cycles_are_rejected(monkeypatch):
    from synthsift.modules import base

    class A(base.Module):
        name, label, requires = "cyc_a", "A", ("cyc_b",)

    class B(base.Module):
        name, label, requires = "cyc_b", "B", ("cyc_a",)

    monkeypatch.setattr(base, "_REGISTRY", {**base._REGISTRY, "cyc_a": A, "cyc_b": B})
    with pytest.raises(ValueError, match="cycle"):
        Runner(open_storage(None), cfg(**{"mod.cyc_a": True, "mod.cyc_b": True}))


# ---------------------------------------------------------------------- IOC
def test_ioc_tokens_refang_and_split():
    toks = {(t[2], t[3]) for t in tokens("Beacon to hxxps://cdn[.]bad-site.example.test/a.js from 203.0.113.9. "
                                         "Mail ops@corp.example.test", 3)}
    assert ("https://cdn.bad-site.example.test/a.js", "url") in toks
    assert ("cdn.bad-site.example.test", "domain") in toks and ("bad-site.example.test", "domain_parent") in toks
    assert ("203.0.113.9", "ip") in toks and ("ops@corp.example.test", "email") in toks
    assert ("mail ops", "word") in toks  # n-grams for keywords / phrases
    assert not any(k == "ip" for _, k in {(t[2], t[3]) for t in tokens("version v1.2.3.4 and 1.2.3.4.5", 1)})


def _ioc_run(tmp_path, paras, lists, **opts):
    (tmp_path / "lists").mkdir(exist_ok=True)
    for name, content in lists.items():
        path = tmp_path / "lists" / name
        path.write_bytes(gzip.compress(content.encode()) if name.endswith(".gz") else content.encode())
    st = open_storage(None)
    items = [ParaIn(para_hash(t, "user", False), t, "user") for t in paras]
    fill(st, items)
    c = cfg(**{"mod.nlp": False, "mod.ioc": True, **opts})
    report = Runner(st, c, data_dir=tmp_path).run()
    hits = {}
    for h, text, lst, label, sev, kind in st.query("SELECT para_hash, text, list, label, severity, kind FROM x_ioc_hits"):
        hits.setdefault(h, set()).add((text, lst, label, sev, kind))
    ents = {}
    for h, key, labels in st.query("SELECT para_hash, key, labels FROM x_entities WHERE labels IS NOT NULL"):
        ents.setdefault(h, {})[key] = labels
    return items, hits, ents, report


def test_ioc_lists_match_every_kind(tmp_path):
    paras = [
        "The loader fetched hxxp://files.bad-site[.]example.test/stage2 then called 198.51.100.77.",  # defanged URL + CIDR
        "Resolved cdn.bad-site.example.test and wrote /opt/app/cache.bin",  # subdomain of a listed domain
        "Checksum d41d8cd98f00b204e9800998ecf8427e matched; contact abuse@bad-site.example.test",
        "We ordered garlic bread for the team.",  # multi-word keyword
        "Nothing to see here.",
    ]
    lists = {
        "watch.txt": "# comment\nbad-site.example.test\ngarlic bread\nD41D8CD98F00B204E9800998ECF8427E\n/opt/app/cache.bin\n",
        "feed.csv": "indicator,type,label,severity\n198.51.100.0/24,cidr,test net two,high\nabuse@bad-site.example.test,email,contact,low\n",
        "extra.txt.gz": "files.bad-site.example.test\n",
    }
    items, hits, ents, report = _ioc_run(tmp_path, paras, lists)
    notes = report["notes"]["ioc"]
    assert {d["file"]: d["entries"] for d in notes["lists"]} == {"extra.txt.gz": 1, "feed.csv": 2, "watch.txt": 4}
    h = [p.hash for p in items]
    assert ("198.51.100.77", "feed.csv", "test net two", "high", "cidr") in hits[h[0]]
    assert any(x[1] == "extra.txt.gz" for x in hits[h[0]]) and any(x[1] == "watch.txt" for x in hits[h[0]])
    assert {x[1] for x in hits[h[1]]} == {"watch.txt"}  # subdomain + path
    assert {x[4] for x in hits[h[1]]} == {"domain", "path"}
    assert {x[4] for x in hits[h[2]]} == {"hash", "email", "domain"}
    assert ("garlic bread", "watch.txt", "watch", "medium", "keyword") in hits[h[3]]
    assert h[4] not in hits
    # hits label the entities they overlap
    assert ents[h[3]]["garlic bread"] == ["watch"]  # the list name is the default label
    assert "test net two" in ents[h[0]]["198.51.100.77"]
    # subdomains off: the parent-domain token no longer matches
    _, hits2, _, _ = _ioc_run(tmp_path, paras[1:2], {"watch.txt": "bad-site.example.test\n"}, ioc_subdomains=False)
    assert not hits2


def test_ioc_large_list(tmp_path):
    n = 300_000
    lines = "\n".join(f"host{i}.zone{i % 97}.example.test" for i in range(n))
    t0 = time.time()
    items, hits, _, report = _ioc_run(tmp_path, ["connect to host123456.zone72.example.test now", "host1.example.test is not listed"],
                                      {"big.txt": lines + "\n"})
    assert report["notes"]["ioc"]["lists"][0]["entries"] == n
    assert items[0].hash in hits and items[1].hash not in hits
    assert time.time() - t0 < 60


# ---------------------------------------------------------------- workspace
def test_workspace_persists_and_restores(tmp_path, sample_zip):
    ws = Workspace(tmp_path)
    ws.add_zip("samples.zip", sample_zip)
    ws.wait()
    assert ws.status.state == "idle", ws.status.message
    stats, findings = ws.payload["stats"], [f.to_json() for f in ws.findings]
    assert {s["name"]: s["state"] for s in ws.status.stages} == {"ingest": "done", "segment": "done", "enrich": "done", "graph": "done"}
    ws.close()

    ws2 = Workspace(tmp_path)  # restart: nothing is parsed or computed again
    ws2.wait()
    assert ws2.status.state == "idle", ws2.status.message
    assert {s["state"] for s in ws2.last_run["steps"]} == {"cached"}
    assert ws2.payload["stats"] == stats and [f.to_json() for f in ws2.findings] == findings
    # labels from label modules reach the payload
    assert {p.get("lb") for p in ws2.payload["paragraphs"]} >= {"prose", "code"}
    # removing the upload removes its conversations and their module rows
    ws2.remove_dataset(next(iter(ws2.datasets)))
    ws2.wait()
    assert not ws2.conversations and ws2.db.count("paragraphs") == 0 and ws2.db.count("x_entities") == 0
    ws2.close()


def test_workspace_migrates_the_old_uploads_index(tmp_path, sample_zip):
    (tmp_path / "uploads").mkdir()
    (tmp_path / "uploads" / "old-1.zip").write_bytes(sample_zip)
    (tmp_path / "datasets.json").write_text(json.dumps([{"id": "old-1", "name": "samples.zip", "uploaded_at": 1.0,
                                                         "size": len(sample_zip), "files": 0, "conversations": 0,
                                                         "warnings": []}]))
    ws = Workspace(tmp_path)
    ws.wait()
    assert ws.status.state == "idle" and len(ws.conversations) == 21
    assert not (tmp_path / "datasets.json").exists()
    ws.close()


def test_modules_lists_and_enrichment_api(tmp_path, sample_zip):
    ws = Workspace(tmp_path)
    client = TestClient(create_app(ws))
    ws.add_zip("samples.zip", sample_zip)
    ws.wait()
    mods = {m["name"]: m for m in client.get("/api/modules").json()["modules"]}
    assert mods["text_stats"]["enabled"] and mods["text_stats"]["processed"] == mods["text_stats"]["paragraphs"] > 0
    assert not mods["ioc"]["enabled"] and mods["entities"]["core"]

    assert client.post("/api/lists", files={"files": ("x.exe", b"1", "application/octet-stream")}).status_code == 400
    r = client.post("/api/lists", files=[("files", ("../watch.txt", b"api.example.com\n", "text/plain"))])
    assert r.status_code == 200 and [x["name"] for x in r.json()["lists"]] == ["watch.txt"]
    assert client.put("/api/settings", json={"mod.ioc": True}).json()["scopes"] == ["parse"]
    ws.wait()
    labelled = [n for n in client.get("/api/graph").json()["nodes"] if n.get("labels")]
    assert any(n["label"] == "api.example.com" and n["labels"] == ["watch"] for n in labelled)
    assert client.get("/api/lists").json()["lists"][0]["entries"] == 1
    assert any(f["category"] == "ioc" for f in client.get("/api/graph").json()["findings"])

    r = client.put("/api/lists/watch.txt", json={"enabled": False}).json()
    assert r["lists"][0]["enabled"] is False
    ws.wait()
    assert not [n for n in client.get("/api/graph").json()["nodes"] if n.get("labels")]
    assert client.delete("/api/lists/watch.txt").json()["lists"] == []

    pid = client.get("/api/graph").json()["paragraphs"][1]["id"]
    enr = client.get(f"/api/enrichment/{pid}").json()
    assert {"regex", "nlp", "entities", "text_stats", "content_type"} <= {m["name"] for m in enr["modules"]}
    assert client.get("/api/enrichment/nope").status_code == 404
    csv = client.get("/api/modules/content_type/export")
    assert csv.status_code == 200 and csv.text.splitlines()[0] == "paragraph,conv,event,label,flags"
    # segment-scope settings re-split the transcripts
    assert client.put("/api/settings", json={"max_paragraph_lines": 20}).json()["scopes"] == ["segment"]
    ws.wait()
    assert ws.status.state == "idle" and {s["name"]: s["state"] for s in ws.status.stages}["segment"] == "done"
    assert module_stats(ws.db, ws.settings.values)
    ws.close()


def test_deps_helpers():
    d = Deps({"t": {"h": [("h", 1, "x")]}}, {"t": ["para_hash", "a", "b"]})
    assert d.dicts("t", "h") == [{"para_hash": "h", "a": 1, "b": "x"}] and d.rows("t", "zz") == [] and d.has("t")
