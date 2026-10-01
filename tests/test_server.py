import time

from fastapi.testclient import TestClient

from synthsift.server import create_app
from synthsift.store import Workspace


def wait(client, version=0, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        st = client.get("/api/status").json()
        if st["state"] != "running" and st["version"] > version:
            return st
        time.sleep(0.1)
    raise AssertionError("timed out")


def test_end_to_end(tmp_path, sample_zip):
    client = TestClient(create_app(Workspace(tmp_path)))
    assert client.get("/api/graph").json()["empty"]
    for page in ("/", "/dashboard", "/settings", "/nodes", "/timeline"):
        r = client.get(page)
        assert r.status_code == 200 and "<title>SynthSift" in r.text
    for asset in ("common.js", "workspace.js", "dashboard.js", "nodes.js", "timeline.js", "app.js"):
        assert client.get(f"/static/js/{asset}").status_code == 200
    assert client.get("/vendor/vis/vis-network.min.js").status_code == 200

    bad = client.post("/api/upload", files={"files": ("x.zip", b"nope", "application/zip")})
    assert bad.status_code == 400

    r = client.post("/api/upload", files={"files": ("samples.zip", sample_zip, "application/zip")})
    assert r.status_code == 200
    st = wait(client)
    g = client.get("/api/graph").json()
    assert g["stats"]["conversations"] == 28
    assert {n["type"] for n in g["nodes"]} >= {"user", "assistant", "thought", "tool_call", "tool_arg", "tool_result", "entity"}
    assert len(g["paragraphs"]) == g["stats"]["paragraphs"]

    # graph-scope setting -> rebuild only
    r = client.put("/api/settings", json={"min_mentions": 2}).json()
    assert r["scopes"] == ["graph"]
    st = wait(client, st["version"])
    # parse-scope setting -> re-analysis
    r = client.put("/api/settings", json={"use_wordnet": False}).json()
    assert r["scopes"] == ["parse"]
    st = wait(client, st["version"])
    g2 = client.get("/api/graph").json()
    assert not any(n.get("category") == "food" and n["label"] == "bread" for n in g2["nodes"])
    # view-scope settings don't touch the server pipeline
    assert client.put("/api/settings", json={"layout": "layers"}).json()["scopes"] == ["view"]

    html = client.get("/api/export/pyvis", params={"convs": g["conversations"][0]["id"]})
    assert html.status_code == 200 and "<html" in html.text.lower()
    assert client.get("/api/export/graphml").status_code == 200

    settings = client.get("/api/settings").json()
    assert {h["name"] for h in settings["harnesses"]} >= {"example", "claude_code", "gemini", "antigravity", "hermes", "openclaw"}
    assert "en_core_web_sm" in settings["installed_models"]

    ds = client.get("/api/datasets").json()
    assert client.delete(f"/api/datasets/{ds[0]['id']}").status_code == 200
    wait(client, st["version"])
    assert client.get("/api/graph").json()["stats"]["conversations"] == 0
