import io
import json
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def make_zip(files: dict[str, object]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, content in files.items():
            zf.writestr(path, content if isinstance(content, (str, bytes)) else json.dumps(content))
    return buf.getvalue()


@pytest.fixture
def zip_of():
    return make_zip


@pytest.fixture(scope="session")
def sample_zip() -> bytes:
    return (ROOT / "samples" / "synthsift-samples.zip").read_bytes()


@pytest.fixture(scope="session")
def sample_workspace(tmp_path_factory, sample_zip):
    from synthsift.store import Workspace

    ws = Workspace(tmp_path_factory.mktemp("ws"))
    ws.add_zip("samples.zip", sample_zip)
    ws.wait()
    assert ws.status.state == "idle", ws.status.message
    return ws
