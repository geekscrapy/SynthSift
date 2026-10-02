"""Workspace: uploaded datasets, the corpus database and the current graph.

Everything lives in one DuckDB file, ``<data dir>/synthsift.duckdb``
(:mod:`synthsift.db`): uploads index, parsed conversations, turns,
paragraphs and every enrichment module's tables.  It survives restarts, so
reopening the app loads the stored corpus and results instead of recomputing.

Processing runs in a background thread in stages, so a change only redoes
what it affects:

    ingest   zip -> conversations                    new / removed uploads only
    segment  conversations -> turns + paragraphs     new conversations, or all on a 'segment' setting change
    enrich   the enrichment modules (runner)         new paragraphs, and modules whose options changed
    graph    networkx build + JSON payload           always (fast)
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import traceback
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import categories, checks
from .annotations import AnnotationStore
from .db import Batch, open_storage
from .db.schema import CONVERSATIONS, CORE, DATASETS, EVENTS, PARA_TEXT, PARAGRAPHS
from .graph.builder import build_graph, graph_to_json
from .ingest import read_zip
from .models import Conversation
from .modules import enabled_modules, para_hash, registry
from .modules.entities import read_results
from .modules.runner import Runner
from .nlp.pipeline import ParaResult
from .segment import VERSION as SEGMENT_VERSION
from .segment import Event, Paragraph, segment
from .settings import SettingsStore

log = logging.getLogger(__name__)

CONV_COLORS = [
    "#1A73E8", "#D93025", "#F9AB00", "#1E8E3E", "#9334E6", "#E8710A", "#12B5CB", "#E52592",
    "#185ABC", "#B31412", "#137333", "#681DA8", "#C26401", "#098591", "#B80672", "#5F6368",
]
STAGES = [("ingest", "Reading uploads"), ("segment", "Splitting into paragraphs"), ("enrich", "Running modules"),
          ("graph", "Building the graph")]


@dataclass
class Status:
    state: str = "idle"  # idle | running | error
    stage: str = ""
    progress: float = 0.0
    message: str = ""
    version: int = 0
    error: str = ""
    started: float = 0.0
    #: pipeline stages of the current / last run: name, label, state, message, seconds
    stages: list[dict[str, Any]] = field(default_factory=list)
    #: enrichment modules of the current / last run (see ``runner.Step``)
    steps: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        out = {k: getattr(self, k) for k in ("state", "stage", "progress", "message", "version", "error", "stages", "steps")}
        out["elapsed"] = round(time.time() - self.started, 1) if self.started and self.state == "running" else 0
        return out


@dataclass
class Dataset:
    id: str
    name: str
    uploaded_at: float
    size: int
    files: int = 0
    conversations: int = 0
    warnings: list[str] = field(default_factory=list)
    ingested: bool = False

    def to_json(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        d.pop("ingested")
        return d

    def row(self) -> tuple:
        return (self.id, self.name, self.uploaded_at, self.size, self.files, self.conversations,
                json.dumps(self.warnings), self.ingested)


class Workspace:
    def __init__(self, data_dir: Path):
        self.dir = data_dir
        self.uploads = data_dir / "uploads"
        self.uploads.mkdir(parents=True, exist_ok=True)
        self.lists_dir = data_dir / "lists"
        self.checks_dir = data_dir / "checks"  # analysts' own security checks
        self.settings = SettingsStore(data_dir / "settings.json")
        self.annotations = AnnotationStore(data_dir / "annotations.json")
        self.db = open_storage(data_dir / "synthsift.duckdb")
        for t in CORE:
            self.db.ensure(t)
        self.status = Status()
        self.datasets: dict[str, Dataset] = {}
        self.conversations: list[Conversation] = []
        self.events: dict[str, list[Event]] = {}
        self.paragraphs: dict[str, Paragraph] = {}
        self.hashes: dict[str, str] = {}  # paragraph id -> content hash
        self.analysis: dict[str, ParaResult] = {}
        self.payload: dict[str, Any] | None = None
        self.findings: list[checks.Finding] = []
        self.graph = None
        self.warnings: list[str] = []
        self.last_run: dict[str, Any] = {}
        self.signature = ""
        self._corpus_analysis: dict[str, ParaResult] | None = None  # loaded once per run, see corpus()
        self._lock = threading.Lock()
        self._pending: set[str] = set()
        self._thread: threading.Thread | None = None
        self._load_index()
        if self.datasets:
            self.schedule("restore")

    def close(self) -> None:
        self.wait()
        self.db.close()

    # ------------------------------------------------------------ datasets
    def _load_index(self) -> None:
        for r in self.db.query("SELECT id, name, uploaded_at, size, files, conversations, warnings, ingested FROM datasets"):
            if (self.uploads / f"{r[0]}.zip").exists():
                self.datasets[r[0]] = Dataset(*r[:6], json.loads(r[6]), r[7])

    def _save_dataset(self, ds: Dataset) -> None:
        with self.db.transaction():
            self.db.delete("datasets", "id = ?", (ds.id,))
            self.db.insert(Batch.from_rows(DATASETS, [ds.row()]))

    def add_zip(self, name: str, data: bytes) -> Dataset:
        digest = hashlib.sha1(data).hexdigest()[:8]
        ds_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{digest}"
        (self.uploads / f"{ds_id}.zip").write_bytes(data)
        ds = Dataset(id=ds_id, name=name, uploaded_at=time.time(), size=len(data))
        self.datasets[ds_id] = ds
        self._save_dataset(ds)
        self.schedule("ingest")
        return ds

    def remove_dataset(self, ds_id: str) -> bool:
        ds = self.datasets.pop(ds_id, None)
        if ds is None:
            return False
        (self.uploads / f"{ds_id}.zip").unlink(missing_ok=True)
        self.db.delete("datasets", "id = ?", (ds_id,))
        self.schedule("ingest")
        return True

    def clear(self) -> None:
        for ds_id in list(self.datasets):
            (self.uploads / f"{ds_id}.zip").unlink(missing_ok=True)
        self.datasets.clear()
        self.db.delete("datasets")
        self.schedule("ingest")

    # -------------------------------------------------------------- jobs
    def schedule(self, stage: str) -> None:
        """Queue work: restore | ingest | segment | enrich | graph (later stages are implied)."""
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
            st = self.status
            st.state, st.error, st.started, st.steps = "running", "", time.time(), []
            st.stages = [{"name": n, "label": lb, "state": "waiting", "message": "", "seconds": 0} for n, lb in STAGES]
            try:
                if "restore" in pending:
                    self._restore()
                self._stage("ingest", self._ingest if pending & {"restore", "ingest"} else None)
                self._stage("segment", self._segment if pending & {"restore", "ingest", "segment"} else None)
                self._stage("enrich", self._enrich if pending & {"restore", "ingest", "segment", "enrich"} else None)
                self._stage("graph", self._build)
                st.state, st.stage, st.progress = "idle", "done", 1.0
                st.message = f"{len(self.conversations)} conversations analysed"
                st.version += 1
            except Exception as exc:  # noqa: BLE001 - surface to the UI
                log.exception("processing failed")
                st.state, st.error = "error", f"{type(exc).__name__}: {exc}"
                st.message = traceback.format_exc(limit=3)
                for s in st.stages:
                    if s["state"] == "running":
                        s["state"] = "error"

    def _stage(self, name: str, fn) -> None:
        stage = next(s for s in self.status.stages if s["name"] == name)
        if fn is None:
            stage["state"] = "skipped"
            return
        stage["state"], t0 = "running", time.time()
        self.status.stage, self.status.progress = name, 0.0
        fn()
        stage["state"], stage["seconds"] = "done", round(time.time() - t0, 2)

    def _set(self, stage: str, progress: float, message: str) -> None:
        self.status.stage, self.status.progress, self.status.message = stage, progress, message
        for s in self.status.stages:
            if s["name"] == stage:
                s["message"] = message

    # ------------------------------------------------------------ stages
    def _restore(self) -> None:
        """Load the stored corpus (no zip parsing, no segmenting when settings are unchanged)."""
        self._set("ingest", 0, "Loading the stored corpus")
        self._load_conversations()
        self.events, self.paragraphs, self.hashes = {}, {}, {}
        if self.db.get_state("segment_fp") != self._segment_fp():
            return
        self._set("segment", 0, "Loading turns and paragraphs")
        for conv, doc in self.db.query("SELECT conv, doc FROM events ORDER BY conv, seq"):
            self.events.setdefault(conv, []).append(Event(**json.loads(doc)))
        for pid, conv, ev, seq, h, text, role, code, arg in self.db.query(
                "SELECT p.id, p.conv, p.event, p.seq, p.hash, t.text, t.role, t.code, t.arg FROM paragraphs p "
                "JOIN para_text t ON p.hash = t.hash ORDER BY p.conv, p.seq"):
            self.paragraphs[pid] = Paragraph(pid, conv, ev, seq, text, role, bool(code), arg)
            self.hashes[pid] = h
        self._sort_paragraphs()

    def _load_conversations(self) -> None:
        rows = self.db.query("SELECT c.doc FROM conversations c JOIN datasets d ON c.dataset = d.id "
                             "ORDER BY d.uploaded_at, c.ord")
        self.conversations = [Conversation.model_validate_json(r[0]) for r in rows]

    def _sort_paragraphs(self) -> None:
        """Conversation order, as the in-memory pipeline would produce it."""
        order = {c.id: i for i, c in enumerate(self.conversations)}
        self.paragraphs = dict(sorted(self.paragraphs.items(), key=lambda kv: (order.get(kv[1].conv, 0), kv[1].seq)))

    def _segment_fp(self) -> str:
        return f"{SEGMENT_VERSION}:{self.settings.fingerprint('segment')}"

    def _ordered_datasets(self) -> list[Dataset]:
        return sorted(self.datasets.values(), key=lambda d: d.uploaded_at)

    def _ingest(self) -> None:
        """Parse new uploads into the database; drop conversations of removed ones."""
        stored = {r[0] for r in self.db.query("SELECT DISTINCT dataset FROM conversations")}
        gone = stored - set(self.datasets)
        if gone:
            with self.db.transaction():
                for table in ("paragraphs", "events"):
                    self.db.execute(f"DELETE FROM {table} WHERE conv IN (SELECT id FROM conversations "
                                    "WHERE dataset IN (SELECT unnest(?)))", (sorted(gone),))
                self.db.delete_in("conversations", "dataset", sorted(gone))
        todo = [ds for ds in self._ordered_datasets() if not ds.ingested or ds.id not in stored]
        for i, ds in enumerate(todo):
            self._set("ingest", i / max(len(todo), 1), f"Reading {ds.name}")
            report = read_zip((self.uploads / f"{ds.id}.zip").read_bytes(), ds.id)
            ds.files, ds.conversations, ds.warnings, ds.ingested = (report.files, len(report.conversations),
                                                                     report.warnings, True)
            with self.db.transaction():
                self.db.delete("conversations", "dataset = ?", (ds.id,))
                self.db.insert(Batch.from_rows(CONVERSATIONS, [
                    (c.id, ds.id, j, c.host, c.user, c.harness, c.session, c.model_dump_json())
                    for j, c in enumerate(report.conversations)]))
                self.db.delete("datasets", "id = ?", (ds.id,))
                self.db.insert(Batch.from_rows(DATASETS, [ds.row()]))
        if gone or todo or not self.conversations:
            self._load_conversations()
        self.warnings = [f"{ds.name}: {w}" for ds in self._ordered_datasets() for w in ds.warnings]

    def _segment(self) -> None:
        """Turns and paragraphs for conversations that don't have them yet (all after a 'segment' change)."""
        cfg = self.settings.values
        fp = self._segment_fp()
        if self.db.get_state("segment_fp") != fp:
            with self.db.transaction():
                self.db.delete("events")
                self.db.delete("paragraphs")
                self.db.set_state("segment_fp", fp)
            self.events, self.paragraphs, self.hashes = {}, {}, {}
        live = {c.id for c in self.conversations}
        for cid in [c for c in self.events if c not in live]:
            for p in [pid for pid, p in self.paragraphs.items() if p.conv == cid]:
                del self.paragraphs[p]
                self.hashes.pop(p, None)
            del self.events[cid]
        new = [c for c in self.conversations if c.id not in self.events]
        ev_rows, p_rows, texts = [], [], {}
        for i, conv in enumerate(new):
            if i % 50 == 0:
                self._set("segment", i / max(len(new), 1), f"Splitting {i:,} / {len(new):,} conversations")
            evs, paras = segment(conv, cfg)
            self.events[conv.id] = evs
            for e in evs:
                ev_rows.append((e.id, e.conv, e.seq, e.type, e.label, e.tool_name, e.timestamp, json.dumps(asdict(e))))
            for p in paras:
                h = para_hash(p.text, p.role, p.code)
                self.paragraphs[p.id] = p
                self.hashes[p.id] = h
                p_rows.append((p.id, p.conv, p.event, p.seq, h))
                texts.setdefault(h, (h, p.text, p.role, p.code, p.arg))
        self._sort_paragraphs()
        self._set("segment", 0.9, "Storing paragraphs")
        with self.db.transaction():
            for table in ("events", "paragraphs"):
                # conversations removed since the last run
                self.db.execute(f"DELETE FROM {table} WHERE conv NOT IN (SELECT id FROM conversations)")
                # never duplicate turns of a conversation that was stored before
                self.db.delete_in(table, "conv", [c.id for c in new])
            self.db.insert(Batch.from_rows(EVENTS, ev_rows))
            self.db.insert(Batch.from_rows(PARAGRAPHS, p_rows))
            if texts:
                known = {r[0] for r in self.db.select_in("para_text", "hash", list(texts), ["hash"])}
                self.db.insert(Batch.from_rows(PARA_TEXT, [v for h, v in texts.items() if h not in known]))
            self.db.execute("DELETE FROM para_text WHERE hash NOT IN (SELECT hash FROM paragraphs)")

    def _enrich(self) -> None:
        self._segment_signature()

        def progress(steps: list[dict[str, Any]]) -> None:
            self.status.steps = steps
            running = [s for s in steps if s["state"] == "running"]
            done = sum(s["state"] in ("done", "cached") for s in steps)
            self.status.progress = done / max(len(steps), 1)
            if running:
                self.status.message = "; ".join(f"{s['label']}: {s['message']}" for s in running[:3])
                for s in self.status.stages:
                    if s["name"] == "enrich":
                        s["message"] = f"{done} / {len(steps)} modules"

        runner = Runner(self.db, self.settings.values, self.dir, on_progress=progress)
        self._set("enrich", 0, "Checking stored results")
        runner.gc()
        self._corpus_analysis = None
        self.last_run = runner.run(source=self)
        self.status.steps = self.last_run["steps"]
        ran = sum(s["state"] == "done" for s in self.last_run["steps"])
        self._set("enrich", 1.0, f"{ran} of {len(self.last_run['steps'])} modules ran" if ran else "all modules up to date")

    def _segment_signature(self) -> None:
        """Changes whenever a conversation, turn or paragraph does (corpus modules rerun then)."""
        sig = hashlib.sha1()
        for c in self.conversations:
            for e in self.events.get(c.id, []):
                sig.update(f"{e.id}\0{e.type}\0{e.tool_name}\0{e.label}\n".encode())
                for pid in e.paragraphs:
                    sig.update(f"{pid}\0{self.hashes.get(pid)}\n".encode())
        self.signature = sig.hexdigest()[:16]

    # CorpusSource for corpus-scope modules
    def corpus(self):
        if self._corpus_analysis is None:
            self._corpus_analysis = self._load_analysis()
        return self.conversations, self.events, self.paragraphs, self._corpus_analysis

    def hash_of(self, pid: str) -> str:
        return self.hashes.get(pid, "")

    def _load_analysis(self) -> dict[str, ParaResult]:
        results = read_results(self.db)
        return {pid: results.get(self.hashes.get(pid, "")) or ParaResult() for pid in self.paragraphs}

    def _load_findings(self) -> list[checks.Finding]:
        if "security" not in enabled_modules(self.settings.values) or "a_findings" not in self.db.tables():
            return []
        return [checks.Finding(conv, event, cat, rule, label, sev, detail, json.loads(ents or "[]"),
                                 [tuple(c) for c in json.loads(chain or "[]")], value or "")
                for conv, event, cat, rule, label, sev, detail, ents, chain, value in self.db.query(
                    "SELECT conv, event, category, rule, label, severity, detail, entities, chain, value FROM a_findings ORDER BY seq")]

    def _para_labels(self) -> dict[str, dict[str, Any]]:
        """Paragraph labels from label modules (content type …), by content hash."""
        if "content_type" not in enabled_modules(self.settings.values) or "l_content_type" not in self.db.tables():
            return {}
        return {h: {"type": label, "flags": list(flags or [])}
                for h, label, flags in self.db.query("SELECT para_hash, label, flags FROM l_content_type")}

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
        self._set("graph", 0.2, "Loading module results")
        self.analysis = self.corpus()[3]  # reuses what corpus modules loaded in this run
        self._corpus_analysis = None
        self.findings = self._load_findings()
        labels = self._para_labels()
        self._set("graph", 0.6, "Building graph")
        convs = self._conv_meta()
        G = build_graph(convs, self.events, self.paragraphs, self.analysis, self.settings.values, self.findings)
        self.graph = G
        data = graph_to_json(G)
        paragraphs = []
        for pid, p in self.paragraphs.items():
            d = p.to_json()
            lb = labels.get(self.hashes.get(pid, ""))
            if lb:
                d["lb"] = lb["type"]
                if lb["flags"]:
                    d["fl"] = lb["flags"]
            paragraphs.append(d)
        self.payload = {
            "conversations": convs,
            "events": [e.to_json() for c in self.conversations for e in self.events.get(c.id, [])],
            "paragraphs": paragraphs,
            "nodes": data["nodes"],
            "edges": data["edges"],
            "findings": [f.to_json() for f in self.findings],
            "security": {
                "categories": checks.CATEGORIES,
                "severities": checks.SEVERITIES,
                "counts": dict(Counter(f.severity for f in self.findings)),
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

    # ------------------------------------------------------ enrichment API
    def enrichment(self, pid: str) -> dict[str, Any] | None:
        """Every module's rows for one paragraph (the paragraph inspector)."""
        h = self.hashes.get(pid)
        if h is None:
            return None
        existing = set(self.db.tables())
        enabled = enabled_modules(self.settings.values)
        out = []
        for name, cls in registry().items():
            if name not in enabled:
                continue
            tables = []
            for t in cls.tables:
                if t.name not in existing or "para_hash" not in t.names:
                    continue
                cols = [c for c in t.names if c != "para_hash"]
                rows = self.db.query(f'SELECT {", ".join(chr(34) + c + chr(34) for c in cols)} FROM "{t.name}" '
                                     "WHERE para_hash = ?", (h,))
                tables.append({"name": t.name, "columns": cols, "rows": [list(r) for r in rows]})
            out.append({"name": name, "label": cls.label, "kind": cls.kind, "tables": tables})
        return {"id": pid, "hash": h, "modules": out}
