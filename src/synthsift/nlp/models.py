"""spaCy models: which are available, and downloading more on request.

A downloaded model is unpacked from its release wheel into ``<data dir>/models/<name>/`` and loaded from there, so
nothing is installed into the Python environment (no pip needed, works in read-only installs). Models installed as
packages (``uv pip install …``) are used too, and win when both exist.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelInfo:
    name: str
    label: str
    size_mb: int  # download size, roughly
    description: str


#: the English pipelines offered in Settings (``spacy_model``)
MODELS = [
    ModelInfo("en_core_web_sm", "Small", 13, "Fast, the default. Shipped with SynthSift."),
    ModelInfo("en_core_web_md", "Medium", 34, "Adds word vectors; noticeably better names, places and organisations."),
    ModelInfo("en_core_web_lg", "Large", 400, "The most accurate of the CPU models; larger vectors."),
    ModelInfo("en_core_web_trf", "Transformer", 460,
              "Most accurate, but slow without a GPU. Also needs the spacy-curated-transformers package."),
]
MODEL_NAMES = [m.name for m in MODELS]
_BY_NAME = {m.name: m for m in MODELS}

#: where model wheels come from; SYNTHSIFT_SPACY_MODELS_URL points at a mirror (same {name} / {version} placeholders)
RELEASES = os.environ.get("SYNTHSIFT_SPACY_MODELS_URL") or \
    "https://github.com/explosion/spacy-models/releases/download/{name}-{version}/{name}-{version}-py3-none-any.whl"
COMPATIBILITY = "https://raw.githubusercontent.com/explosion/spacy-models/master/compatibility.json"


def spacy_minor() -> str:
    import spacy

    return ".".join(spacy.__version__.split(".")[:2])


def compatible_version(name: str) -> str:
    """The newest release of a model that works with the installed spaCy."""
    minor = spacy_minor()
    try:
        with urllib.request.urlopen(COMPATIBILITY, timeout=15) as resp:
            versions = json.load(resp)["spacy"][minor][name]
            if versions:
                return versions[0]
    except Exception as exc:  # noqa: BLE001 - offline or the file moved: fall back to the x.y.0 release
        log.info("spaCy compatibility table unavailable (%s); assuming %s.0", exc, minor)
    return f"{minor}.0"


def package_version(name: str) -> str | None:
    """Version of a model installed as a Python package, or None."""
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        try:
            return metadata.version(name.replace("_", "-"))
        except metadata.PackageNotFoundError:
            return None


def missing_requirements(meta: dict[str, Any]) -> list[str]:
    """Python packages a model needs that aren't installed (e.g. spacy-curated-transformers for trf)."""
    from packaging.requirements import Requirement

    out = []
    for spec in meta.get("requirements") or []:
        try:
            req = Requirement(spec)
            have = metadata.version(req.name)
            if req.specifier and have not in req.specifier:
                out.append(spec)
        except metadata.PackageNotFoundError:
            out.append(spec)
        except Exception:  # noqa: BLE001 - an unparsable requirement is not ours to judge
            continue
    return out


def read_meta(name: str, path: Path | None) -> dict[str, Any]:
    """A model's meta.json: from the downloaded folder ``path``, else from the installed package (``{}`` if none)."""
    try:
        if path is None:
            import spacy.util

            path = spacy.util.get_package_path(name)
        return json.loads((Path(path) / "meta.json").read_text())
    except Exception:  # noqa: BLE001 - not installed, or an odd layout
        return {}


def local_path(models_dir: Path | None, name: str) -> Path | None:
    if models_dir is None:
        return None
    path = Path(models_dir) / name
    return path if (path / "config.cfg").is_file() and (path / "meta.json").is_file() else None


def resolve(name: str, models_dir: Path | None) -> str:
    """What to hand ``spacy.load``: the package name when installed, else the downloaded folder, else the name
    (the loader then falls back to a model that is there)."""
    if package_version(name) is None:
        path = local_path(models_dir, name)
        if path is not None:
            return str(path)
    return name


def model_state(name: str, models_dir: Path | None) -> str:
    """A short identity of the model that will actually load (part of the NLP module's fingerprint)."""
    v = package_version(name)
    if v:
        return f"{name}=={v}"
    path = local_path(models_dir, name)
    if path is not None:
        return f"{name}=={read_meta(name, path).get('version', '?')} (downloaded)"
    return f"{name} (missing)"


def available(models_dir: Path | None) -> list[str]:
    """Models that can be loaded: installed packages plus downloads."""
    import spacy.util

    names = set(spacy.util.get_installed_models())
    names |= {n for n in MODEL_NAMES if local_path(models_dir, n)}
    if models_dir is not None and Path(models_dir).is_dir():
        names |= {p.name for p in Path(models_dir).iterdir() if local_path(models_dir, p.name)}
    return sorted(names)


@dataclass
class Download:
    name: str
    version: str = ""
    state: str = "starting"  # starting | downloading | unpacking | done | error
    done: int = 0
    total: int = 0
    error: str = ""
    started: float = 0.0

    def to_json(self) -> dict[str, Any]:
        return {"name": self.name, "version": self.version, "state": self.state, "done": self.done,
                "total": self.total, "error": self.error}


class ModelStore:
    """The models folder of a workspace and the downloads running into it."""

    def __init__(self, models_dir: Path):
        self.dir = Path(models_dir)
        self.downloads: dict[str, Download] = {}
        self._lock = threading.Lock()

    def describe(self) -> list[dict[str, Any]]:
        out = []
        for m in MODELS:
            pkg = package_version(m.name)
            path = local_path(self.dir, m.name)
            meta = read_meta(m.name, path if not pkg else None)
            dl = self.downloads.get(m.name)
            out.append({
                "name": m.name, "label": m.label, "size_mb": m.size_mb, "description": m.description,
                "state": "installed" if pkg else "downloaded" if path else "missing",
                "version": pkg or meta.get("version", ""),
                "missing_requirements": missing_requirements(meta) if meta else [],
                "download": dl.to_json() if dl else None,
            })
        return out

    def busy(self) -> bool:
        return any(d.state in ("starting", "downloading", "unpacking") for d in self.downloads.values())

    def start(self, name: str, on_done=None) -> Download:
        """Download a model in the background (a second request for the same model joins the running one)."""
        if name not in _BY_NAME:
            raise KeyError(name)
        with self._lock:
            cur = self.downloads.get(name)
            if cur and cur.state in ("starting", "downloading", "unpacking"):
                return cur
            dl = self.downloads[name] = Download(name, started=time.time())
        threading.Thread(target=self._run, args=(dl, on_done), name=f"synthsift-model-{name}", daemon=True).start()
        return dl

    def remove(self, name: str) -> bool:
        path = local_path(self.dir, name)
        if path is None:
            return False
        shutil.rmtree(path)
        self.downloads.pop(name, None)
        return True

    # ------------------------------------------------------------ download
    def _run(self, dl: Download, on_done) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        part = self.dir / f".{dl.name}.whl.part"
        tmp = self.dir / f".{dl.name}.unpacking"
        try:
            dl.version = compatible_version(dl.name)
            url = RELEASES.format(name=dl.name, version=dl.version)
            dl.state = "downloading"
            with urllib.request.urlopen(url, timeout=60) as resp, part.open("wb") as out:
                dl.total = int(resp.headers.get("Content-Length") or 0)
                while chunk := resp.read(1 << 18):
                    out.write(chunk)
                    dl.done += len(chunk)
            dl.state = "unpacking"
            shutil.rmtree(tmp, ignore_errors=True)
            unpack(part, dl.name, tmp)
            final = self.dir / dl.name
            shutil.rmtree(final, ignore_errors=True)
            tmp.rename(final)
            dl.state = "done"
            log.info("downloaded spaCy model %s %s to %s", dl.name, dl.version, final)
        except Exception as exc:  # noqa: BLE001 - reported to the Settings page
            dl.state, dl.error = "error", f"{type(exc).__name__}: {exc}"
            log.warning("downloading %s failed: %s", dl.name, dl.error)
        finally:
            part.unlink(missing_ok=True)
            shutil.rmtree(tmp, ignore_errors=True)
        if dl.state == "done" and on_done is not None:
            on_done(dl.name)


def unpack(wheel: Path, name: str, target: Path) -> None:
    """Extract the model data folder of a model wheel (``<name>/<name>-<version>/``) into ``target``."""
    with zipfile.ZipFile(wheel) as zf:
        configs = [n for n in zf.namelist() if n.startswith(f"{name}/{name}-") and n.endswith("/config.cfg")
                   and n.count("/") == 2]
        if not configs:
            raise ValueError(f"{wheel.name} does not look like a spaCy model package for {name}")
        prefix = configs[0][: -len("config.cfg")]
        root = target.resolve()
        for info in zf.infolist():
            if not info.filename.startswith(prefix) or info.is_dir():
                continue
            dest = (target / info.filename[len(prefix):]).resolve()
            if root not in dest.parents:
                raise ValueError(f"unsafe path in {wheel.name}: {info.filename}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, dest.open("wb") as out:
                shutil.copyfileobj(src, out)
