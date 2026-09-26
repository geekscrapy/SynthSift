from fastapi.testclient import TestClient

from synthsift.annotations import AnnotationStore
from synthsift.server import create_app
from synthsift.store import Workspace


def test_store_roundtrip(tmp_path):
    path = tmp_path / "a.json"
    st = AnnotationStore(path)
    st.upsert("event:c1:e3", ["Suspicious", "seen", "seen"], "look at this", label="bash #3", conv="c1", ts="2026-01-01T00:00:00Z")
    st.upsert("term:ent:/etc/shadow", ["exfil-review"], "")
    st.upsert("conv:c1", [], "whole session looks odd")
    again = AnnotationStore(path)
    anns = again.data.annotations
    assert anns["event:c1:e3"].tags == ["suspicious", "seen"]
    assert anns["event:c1:e3"].conv == "c1" and anns["event:c1:e3"].label == "bash #3"
    assert anns["conv:c1"].comment == "whole session looks odd"
    assert "exfil-review" in {t["name"] for t in again.tags()}  # new tags become custom tags
    # updating without a label keeps the earlier snapshot
    again.upsert("event:c1:e3", ["bad"], "")
    assert again.data.annotations["event:c1:e3"].label == "bash #3"
    # clearing tags and comment removes the annotation
    again.upsert("event:c1:e3", [], "")
    assert "event:c1:e3" not in again.data.annotations


def test_removing_a_custom_tag_strips_it(tmp_path):
    st = AnnotationStore(tmp_path / "a.json")
    st.add_tag("case-42", "#123456")
    st.upsert("term:ent:x", ["case-42"])
    st.remove_tag("case-42")
    assert "term:ent:x" not in st.data.annotations
    assert "case-42" not in {t["name"] for t in st.tags()}


def test_bad_target_rejected(tmp_path):
    client = TestClient(create_app(Workspace(tmp_path)))
    assert client.put("/api/annotations", json={"target": "nope:1", "tags": ["bad"]}).status_code == 400


def test_api(tmp_path):
    client = TestClient(create_app(Workspace(tmp_path)))
    r = client.put("/api/annotations", json={"target": "term:ent:https://x.example/a b", "tags": ["bad"], "comment": "c"})
    assert r.status_code == 200 and r.json()["annotation"]["tags"] == ["bad"]
    got = client.get("/api/annotations").json()
    assert "term:ent:https://x.example/a b" in got["annotations"]
    assert {"bad", "suspicious", "seen", "ignore"} <= {t["name"] for t in got["tags"]}
    assert client.post("/api/tags", json={"name": "Needs Follow-up"}).status_code == 200
    assert "needs follow-up" in {t["name"] for t in client.get("/api/annotations").json()["tags"]}
    assert client.delete("/api/annotations", params={"target": "term:ent:https://x.example/a b"}).json()["deleted"]
    assert client.get("/api/annotations/export").status_code == 200
