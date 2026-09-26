"""Workspace: uploaded datasets, cached analysis and the current graph.

Processing runs in a background thread in three stages so that settings
changes only redo what they affect:

    ingest   (zip -> conversations)                  on upload / delete
    analyze  (segment + NLP per paragraph, cached)   on 'parse' settings change
    graph    (networkx build + JSON payload)         on 'graph' settings change
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import categories
from .annotations import AnnotationStore
from .graph.builder import build_graph, graph_to_json
from .ingest import read_zip
from .models import Conversation
from .nlp import security
from .nlp.pipeline import Analyzer, ParaResult
from .segment import Event, Paragraph, segment
from .settings import SettingsStore

log = logging.getLogger(__name__)

CONV_COLORS = [
    "#1A73E8", "#D93025", "#F9AB00", "#1E8E3E", "#9334E6", "#E8710A", "#12B5CB", "#E52592",
    "#185ABC", "#B31412", "#137333", "#681DA8", "#C26401", "#098591", "#B80672", "#5F6368",
]


@dataclass
class Status:
    state: str = "idle"  # idle | running | error
    stage: str = ""
    progress: float = 0.0
    message: str = ""
    version: int = 0
    error: str = ""
    started: float = 0.0

    def to_json(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in ("state", "stage", "progress", "message", "version", "error")}


@dataclass
class Dataset:
    id: str
    name: str
    uploaded_at: float
    size: int
    files: int = 0
    conversations: int = 0
    warnings: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return self.__dict__.copy()


class Workspace:
    def __init__(self, data_dir: Path):
        self.dir = data_dir
        self.uploads = data_dir / "uploads"
        self.uploads.mkdir(parents=True, exist_ok=True)
        self.settings = SettingsStore(data_dir / "settings.json")
        self.annotations = AnnotationStore(data_dir / "annotations.json")
        self.status = Status()
        self.datasets: dict[str, Dataset] = {}
        self.conversations: list[Conversation] = []
        self.events: dict[str, list[Event]] = {}
        self.paragraphs: dict[str, Paragraph] = {}
        self.analysis: dict[str, ParaResult] = {}
        self._analysis_cache: dict[str, ParaResult] = {}  # (text hash) -> result for current parse cfg
        self._cache_fp = ""
        self.payload: dict[str, Any] | None = None
        self.findings: list[security.Finding] = []
        self.graph = None
        self.warnings: list[str] = []
        self._lock = threading.Lock()
        self._pending: set[str] = set()
        self._thread: threading.Thread | None = None
        self._load_index()

    # ------------------------------------------------------------ datasets
    def _index_path(self) -> Path:
        return self.dir / "datasets.json"

    def _load_index(self) -> None:
        try:
            for d in json.loads(self._index_path().read_text()):
                if (self.uploads / f"{d['id']}.zip").exists():
                    self.datasets[d["id"]] = Dataset(**d)
        except (OSError, ValueError, TypeError):
            pass

    def _save_index(self) -> None:
        self._index_path().write_text(json.dumps([d.to_json() for d in self.datasets.values()], indent=2))

    def add_zip(self, name: str, data: bytes) -> Dataset:
        digest = hashlib.sha1(data).hexdigest()[:8]
        ds_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{digest}"
        (self.uploads / f"{ds_id}.zip").write_bytes(data)
        ds = Dataset(id=ds_id, name=name, uploaded_at=time.time(), size=len(data))
        self.datasets[ds_id] = ds
        self._save_index()
        self.schedule("ingest")
        return ds

    def remove_dataset(self, ds_id: str) -> bool:
        ds = self.datasets.pop(ds_id, None)
        if ds is None:
            return False
        (self.uploads / f"{ds_id}.zip").unlink(missing_ok=True)
        self._save_index()
        self.schedule("ingest")
        return True

    def clear(self) -> None:
        for ds_id in list(self.datasets):
            (self.uploads / f"{ds_id}.zip").unlink(missing_ok=True)
        self.datasets.clear()
        self._save_index()
        self.schedule("ingest")

    # -------------------------------------------------------------- jobs
    def schedule(self, stage: str) -> None:
        """Queue work; later stages are implied by earlier ones."""
        with self._lock:
            self._pending.add(stage)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, daemon=True)
                self._thread.start()

    def wait(self, timeout: float = 600) -> None:
        end = time.time() + timeout
        while time.time() < end:
            with self._lock:
                busy = bool(self._pending) or (self._thread is not None and self._thread.is_alive())
            if not busy:
                return
            time.sleep(0.05)

    def _run(self) -> None:
        while True:
            with self._lock:
                if not self._pending:
                    self._thread = None
                    return
                pending, self._pending = self._pending, set()
            self.status.state, self.status.error, self.status.started = "running", "", time.time()
            try:
                if "ingest" in pending:
                    self._ingest()
                if pending & {"ingest", "analyze"}:
                    self._analyze()
                self._build()
                self.status.state, self.status.stage, self.status.progress = "idle", "done", 1.0
                self.status.message = f"{len(self.conversations)} conversations analysed"
                self.status.version += 1
            except Exception as exc:  # noqa: BLE001 - surface to the UI
                log.exception("processing failed")
                self.status.state, self.status.error = "error", f"{type(exc).__name__}: {exc}"
                self.status.message = traceback.format_exc(limit=3)

    def _set(self, stage: str, progress: float, message: str) -> None:
        self.status.stage, self.status.progress, self.status.message = stage, progress, message

    def _ingest(self) -> None:
        self.conversations, self.warnings = [], []
        for i, ds in enumerate(sorted(self.datasets.values(), key=lambda d: d.uploaded_at)):
            self._set("ingest", i / max(len(self.datasets), 1), f"Reading {ds.name}")
            report = read_zip((self.uploads / f"{ds.id}.zip").read_bytes(), ds.id)
            ds.files, ds.conversations, ds.warnings = report.files, len(report.conversations), report.warnings
            self.conversations.extend(report.conversations)
            self.warnings.extend(f"{ds.name}: {w}" for w in report.warnings)
        self._save_index()

    def _analyze(self) -> None:
        cfg = self.settings.values
        fp = self.settings.fingerprint("parse")
        if fp != self._cache_fp:
            self._analysis_cache, self._cache_fp = {}, fp
        self.events, self.paragraphs = {}, {}
        self._set("segment", 0, "Splitting transcripts into paragraphs")
        for conv in self.conversations:
            evs, paras = segment(conv, cfg)
            self.events[conv.id] = evs
            for p in paras:
                self.paragraphs[p.id] = p

        def h(p: Paragraph) -> str:
            return hashlib.sha1(f"{p.role}\0{int(p.code)}\0{p.text}".encode()).hexdigest()

        todo = [p for p in self.paragraphs.values() if h(p) not in self._analysis_cache]
        self._set("analyze", 0, f"Loading NLP model ({cfg['spacy_model']})")
        if todo:
            analyzer = Analyzer(cfg)

            def progress(done: int, total: int) -> None:
                self._set("analyze", done / max(total, 1), f"Extracting entities: {done:,} / {total:,} paragraphs")

            results = analyzer.analyze(((p.id, p.text, p.role, p.code) for p in todo), progress)
            for p in todo:
                self._analysis_cache[h(p)] = results.get(p.id, ParaResult())
        self.analysis = {pid: self._analysis_cache[h(p)] for pid, p in self.paragraphs.items()}

    def _conv_meta(self) -> list[dict[str, Any]]:
        out = []
        for i, c in enumerate(self.conversations):
            evs = self.events.get(c.id, [])
            out.append({
                "id": c.id, "title": c.display_title, "host": c.host, "user": c.user, "harness": c.harness,
                "session": c.session, "dataset": c.meta.get("dataset", ""), "source": c.source_path,
                "index": c.index_in_session, "model": c.model, "started_at": c.started_at,
                "color": CONV_COLORS[i % len(CONV_COLORS)],
                "n_events": len(evs), "n_paragraphs": sum(len(e.paragraphs) for e in evs),
                "n_tool_calls": sum(e.type == "tool_call" for e in evs),
                "n_thoughts": sum(e.type == "thought" for e in evs),
                # harness details: channel, working directory, git branch, archive state, …
                "meta": {k: v for k, v in c.meta.items()
                         if k != "dataset" and isinstance(v, (str, int, float, bool)) and v not in ("", None)},
            })
        return out

    def _build(self) -> None:
        self._set("graph", 0.4, "Scanning for security signals")
        self.findings = security.scan(self.conversations, self.events, self.paragraphs, self.analysis,
                                      self.settings.values)
        self._set("graph", 0.6, "Building graph")
        convs = self._conv_meta()
        G = build_graph(convs, self.events, self.paragraphs, self.analysis, self.settings.values, self.findings)
        self.graph = G
        data = graph_to_json(G)
        sev_counts: dict[str, int] = {}
        for f in self.findings:
            sev_counts[f.severity] = sev_counts.get(f.severity, 0) + 1
        self.payload = {
            "conversations": convs,
            "events": [e.to_json() for c in self.conversations for e in self.events.get(c.id, [])],
            "paragraphs": [p.to_json() for p in self.paragraphs.values()],
            "nodes": data["nodes"],
            "edges": data["edges"],
            "findings": [f.to_json() for f in self.findings],
            "security": {
                "categories": security.CATEGORIES,
                "severities": security.SEVERITIES,
                "counts": sev_counts,
            },
            "warnings": self.warnings,
            "datasets": [d.to_json() for d in self.datasets.values()],
            "kinds": categories.as_json(),
            "stats": {
                "conversations": len(convs), "paragraphs": len(self.paragraphs),
                "nodes": G.number_of_nodes(), "edges": G.number_of_edges(),
                "entities": sum(1 for _, d in G.nodes(data=True) if d.get("type") == "entity"),
                "findings": len(self.findings),
            },
        }
