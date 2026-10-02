"""Downloading spaCy models into the data directory (no network: a fake release served from disk)."""

import json
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from synthsift.modules.nlp import NLPModule
from synthsift.nlp import models
from synthsift.server import create_app
from synthsift.store import Workspace

NAME, VERSION = "en_core_web_md", "3.8.0"


@pytest.fixture
def release(tmp_path, monkeypatch):
    """A blank English pipeline packaged like a spaCy model wheel, and the download URLs pointed at it."""
    import spacy

    src = tmp_path / "pipeline"
    spacy.blank("en").to_disk(src)
    meta = json.loads((src / "meta.json").read_text())
    meta.update(name="core_web_md", version=VERSION, requirements=["no-such-package-xyz>=1"])
    (src / "meta.json").write_text(json.dumps(meta))
    wheel = tmp_path / "rel" / f"{NAME}-{VERSION}-py3-none-any.whl"
    wheel.parent.mkdir()
    with zipfile.ZipFile(wheel, "w") as zf:
        zf.writestr(f"{NAME}/__init__.py", "")
        zf.writestr(f"{NAME}/meta.json", json.dumps(meta))
        for f in src.rglob("*"):
            if f.is_file():
                zf.write(f, f"{NAME}/{NAME}-{VERSION}/{f.relative_to(src).as_posix()}")
        zf.writestr(f"{NAME}-{VERSION}.dist-info/METADATA", "Name: en-core-web-md\n")
    monkeypatch.setattr(models, "RELEASES", (tmp_path / "rel").as_uri() + "/{name}-{version}-py3-none-any.whl")
    monkeypatch.setattr(models, "COMPATIBILITY", (tmp_path / "compat.json").as_uri())
    (tmp_path / "compat.json").write_text(json.dumps({"spacy": {models.spacy_minor(): {NAME: [VERSION]}}}))
    return wheel


def wait_download(store, name, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        dl = store.downloads[name]
        if dl.state in ("done", "error"):
            return dl
        time.sleep(0.05)
    raise AssertionError("download did not finish")


def test_download_unpacks_and_resolves(tmp_path, release):
    store = models.ModelStore(tmp_path / "data" / "models")
    assert models.model_state(NAME, store.dir) == f"{NAME} (missing)"
    assert models.resolve(NAME, store.dir) == NAME
    done = []
    store.start(NAME, on_done=done.append)
    dl = wait_download(store, NAME)
    assert dl.state == "done", dl.error
    assert done == [NAME] and dl.done == dl.total == release.stat().st_size
    path = store.dir / NAME
    assert (path / "config.cfg").is_file() and not list(store.dir.glob(".*"))  # no leftovers
    assert models.resolve(NAME, store.dir) == str(path)
    assert models.model_state(NAME, store.dir) == f"{NAME}=={VERSION} (downloaded)"
    assert NAME in models.available(store.dir)
    [row] = [m for m in store.describe() if m["name"] == NAME]
    assert row["state"] == "downloaded" and row["version"] == VERSION
    assert row["missing_requirements"] == ["no-such-package-xyz>=1"]

    import spacy

    assert spacy.load(models.resolve(NAME, store.dir)).lang == "en"
    # the NLP module loads it, and its fingerprint changes once the model is there
    mod = NLPModule({"spacy_model": NAME}, tmp_path / "data")
    assert NAME in mod.fingerprint_extra(None)
    assert store.remove(NAME) and models.local_path(store.dir, NAME) is None


def test_failed_download_is_reported(tmp_path, release):
    release.unlink()
    store = models.ModelStore(tmp_path / "models")
    dl = wait_download(store, store.start(NAME).name)
    assert dl.state == "error" and dl.error
    assert models.local_path(store.dir, NAME) is None


def test_unpack_rejects_other_archives(tmp_path):
    bad = tmp_path / "bad.whl"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("something/else.txt", "x")
    with pytest.raises(ValueError):
        models.unpack(bad, NAME, tmp_path / "out")
    evil = tmp_path / "evil.whl"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr(f"{NAME}/{NAME}-{VERSION}/config.cfg", "")
        zf.writestr(f"{NAME}/{NAME}-{VERSION}/../../../escape.txt", "x")
    with pytest.raises(ValueError):
        models.unpack(evil, NAME, tmp_path / "out2")
    assert not (tmp_path / "escape.txt").exists()


def test_models_api(tmp_path, release):
    ws = Workspace(tmp_path / "ws")
    try:
        client = TestClient(create_app(ws))
        r = client.get("/api/models").json()
        assert [m["name"] for m in r["models"]] == models.MODEL_NAMES
        assert {m["name"]: m["state"] for m in r["models"]}["en_core_web_sm"] == "installed"
        assert client.post("/api/models/not_a_model/download").status_code == 404
        client.post(f"/api/models/{NAME}/download")
        wait_download(ws.models, NAME)
        r = client.get("/api/models").json()
        assert {m["name"]: m["state"] for m in r["models"]}[NAME] == "downloaded"
        assert NAME in client.get("/api/settings").json()["installed_models"]
        assert client.delete(f"/api/models/{NAME}").status_code == 200
        assert client.delete(f"/api/models/{NAME}").status_code == 404
    finally:
        ws.close()
