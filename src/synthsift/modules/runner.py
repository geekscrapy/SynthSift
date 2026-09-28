"""Runs the enabled enrichment modules against the database.

* **Order.**  Modules form a dependency graph.  A module starts as soon as
  everything it depends on has finished, so independent modules (the pattern
  extractors, spaCy, the IOC tokeniser, text features, content labels …) run
  side by side.
* **Incremental.**  A paragraph-scope module records every paragraph hash it
  has processed in ``module_done`` and later only sees new ones.  Its
  *fingerprint* (version, options, the fingerprints of its dependencies and
  any extra state such as list files) is stored too: when it changes, the
  module's tables are cleared and it – and everything downstream – runs
  again, while unrelated modules keep their results.  Corpus-scope modules
  rerun when their fingerprint, their inputs or the corpus change.
* **Parallel.**  Work is cut into chunks of ``chunk_size`` paragraphs.  Large
  batches go to worker processes (spawned once per run; each keeps its
  modules loaded between chunks); small ones run on threads, which cost
  nothing to start.  Only the coordinating thread writes paragraph results:
  each chunk's rows and its ``module_done`` markers commit together, so an
  interrupted run resumes where it stopped.
"""

from __future__ import annotations

import json
import logging
import multiprocessing as mp
import os
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, ThreadPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from ..db import Batch, Storage, open_storage
from ..db.schema import MODULE_DONE, PARA_TEXT
from ..nlp.pipeline import ParaResult
from .base import Deps, Module, ParaIn, enabled_modules, fingerprint, para_hash, registry
from .entities import read_results

log = logging.getLogger(__name__)

_PENDING_SQL = ("SELECT hash FROM para_text WHERE hash NOT IN "
                "(SELECT para_hash FROM module_done WHERE module = ?) ORDER BY hash")


class CorpusSource(Protocol):
    """What corpus-scope modules may read besides the database (the workspace provides it)."""

    #: changes whenever conversations, turns or paragraphs change
    signature: str

    def corpus(self) -> tuple[list, dict, dict, dict]:
        """(conversations, events per conversation, paragraphs by id, entities per paragraph id)."""

    def hash_of(self, pid: str) -> str: ...


class CorpusContext:
    """Handed to ``run_corpus``."""

    def __init__(self, runner: "Runner", module: str, source: CorpusSource | None):
        self.storage = runner.storage
        self.data_dir = runner.data_dir
        self.enabled = runner.enabled
        self._runner, self._module, self._source = runner, module, source

    def corpus(self):
        if self._source is None:
            raise RuntimeError(f"{self._module} needs the corpus, but none was given")
        return self._source.corpus()

    def hash_of(self, pid: str) -> str:
        return self._source.hash_of(pid) if self._source else ""

    def progress(self, message: str, fraction: float | None = None) -> None:
        self._runner._step_update(self._module, message=message, fraction=fraction)

    def note(self, key: str, value: Any) -> None:
        """Remember something for the Settings page (e.g. the lists loaded and their sizes)."""
        self._runner.notes.setdefault(self._module, {})[key] = value
        self.storage.set_state(f"note:{self._module}", json.dumps(self._runner.notes[self._module], default=str))


@dataclass
class Step:
    """Progress of one module in a run (shown on the loading screen)."""

    name: str
    label: str
    kind: str
    scope: str
    state: str = "waiting"  # waiting | running | done | cached | error
    total: int = 0  # paragraphs to process (paragraph scope)
    done: int = 0
    message: str = ""
    fraction: float | None = None
    started: float = 0.0
    seconds: float = 0.0
    workers: str = ""  # "4 processes" | "thread"
    load_seconds: float = 0.0  # reading inputs from the database (coordinator)
    write_seconds: float = 0.0  # storing results

    def to_json(self) -> dict[str, Any]:
        progress = self.fraction if self.fraction is not None else (self.done / self.total if self.total else None)
        if self.state in ("done", "cached"):
            progress = 1.0
        return {"name": self.name, "label": self.label, "kind": self.kind, "scope": self.scope, "state": self.state,
                "total": self.total, "done": self.done, "message": self.message, "progress": progress,
                "seconds": round(self.seconds or (time.time() - self.started if self.started else 0), 2),
                "workers": self.workers, "load_seconds": round(self.load_seconds, 2),
                "write_seconds": round(self.write_seconds, 2)}


# ------------------------------------------------------------------ workers
_WORKER_MODS: dict[str, tuple[str, Module]] = {}


def _process_chunk(name: str, fp: str, cfg: dict[str, Any], paras: list[ParaIn], data: dict, columns: dict) -> dict:
    """Runs in a worker process: keeps one loaded instance per module (reloaded when its fingerprint changes)."""
    cached = _WORKER_MODS.get(name)
    if cached is None or cached[0] != fp:
        mod = registry()[name](cfg)
        mod.setup()
        _WORKER_MODS[name] = cached = (fp, mod)
    return cached[1].process(paras, Deps(data, columns))


@dataclass
class _Job:
    name: str
    mod: Module
    fp: str
    chunks: list[list[str]] = field(default_factory=list)
    inflight: int = 0
    in_process: bool = False
    ready: bool = False  # setup() done (thread mode)


class Runner:
    def __init__(self, storage: Storage, cfg: dict[str, Any], data_dir: Path | None = None,
                 on_progress: Callable[[list[dict[str, Any]]], None] | None = None):
        self.storage = storage
        self.cfg = cfg
        self.data_dir = data_dir
        self.on_progress = on_progress
        self.reg = registry()
        self.enabled = enabled_modules(cfg)
        self.inst = {n: self.reg[n](cfg) for n in self.enabled}
        self.deps = {n: [d for d in self.inst[n].dependencies(self.enabled) if d in self.enabled] for n in self.enabled}
        self.order = self._toposort()
        self.steps = {n: Step(n, self.reg[n].label, self.reg[n].kind, self.reg[n].scope) for n in self.order}
        self.notes: dict[str, dict[str, Any]] = {}
        self.changed: set[str] = set()
        self._lock = threading.Lock()
        self._last_emit = 0.0

    # ------------------------------------------------------------ plan
    def _toposort(self) -> list[str]:
        order: list[str] = []
        state: dict[str, int] = {}

        def visit(n: str, trail: tuple[str, ...]) -> None:
            if state.get(n) == 2:
                return
            if state.get(n) == 1:
                raise ValueError("module dependency cycle: " + " → ".join(trail + (n,)))
            state[n] = 1
            for d in self.deps[n]:
                visit(d, trail + (n,))
            state[n] = 2
            order.append(n)

        for n in self.reg:  # registry order breaks ties
            if n in self.enabled:
                visit(n, ())
        return order

    def fingerprints(self, source: CorpusSource | None = None) -> dict[str, str]:
        fps: dict[str, str] = {}
        ctx = CorpusContext(self, "", source)
        for n in self.order:
            fps[n] = fingerprint(self.reg[n], self.cfg, [fps[d] for d in self.deps[n]], self.inst[n].fingerprint_extra(ctx))
        return fps

    def ensure_tables(self) -> None:
        for t in (PARA_TEXT, MODULE_DONE):
            self.storage.ensure(t)
        for cls in self.reg.values():
            if cls.name in self.enabled:
                for t in cls.tables:
                    self.storage.ensure(t)

    def gc(self) -> None:
        """Drop results for paragraphs no longer in the corpus (every module, enabled or not)."""
        existing = set(self.storage.tables())
        for cls in self.reg.values():
            if cls.scope != "paragraph":
                continue
            for t in cls.tables:
                if t.name in existing and "para_hash" in t.names:
                    self.storage.delete(t.name, "para_hash NOT IN (SELECT hash FROM para_text)")
        self.storage.delete("module_done", "para_hash NOT IN (SELECT hash FROM para_text)")

    # ------------------------------------------------------------- run
    def run(self, source: CorpusSource | None = None) -> dict[str, Any]:
        if source is None:  # no conversations to hand out: skip modules that need them, and what depends on those
            drop = {n for n in self.order if self.reg[n].needs_corpus}
            for n in self.order:
                if any(d in drop for d in self.deps[n]):
                    drop.add(n)
            self.order = [n for n in self.order if n not in drop]
            self.steps = {n: s for n, s in self.steps.items() if n not in drop}
        self.ensure_tables()
        fps = self.fingerprints(source)
        sig = source.signature if source else ""  # corpus modules also rerun when the corpus changes
        workers = int(self.cfg.get("workers", 0) or 0) or (os.cpu_count() or 1)
        min_parallel = int(self.cfg.get("parallel_min_paragraphs", 3000))
        finished: set[str] = set()
        started: set[str] = set()
        jobs: dict[str, _Job] = {}
        futures: dict[Future, tuple[str, list[str] | None]] = {}
        fut_in_process: dict[Future, bool] = {}
        threads = ThreadPoolExecutor(max_workers=max(2, len(self.order)), thread_name_prefix="synthsift-module")
        procs: ProcessPoolExecutor | None = None
        proc_inflight = 0
        procs_ok = True
        t0 = time.time()

        def no_processes(why: BaseException) -> None:
            """Worker processes can't start (or died): carry on in threads."""
            nonlocal procs_ok
            if procs_ok:
                log.warning("worker processes unavailable (%s: %s); running modules in threads",
                            type(why).__name__, str(why).strip().splitlines()[0] if str(why).strip() else "")
            procs_ok = False
            for job in jobs.values():
                if job.in_process:
                    job.in_process = False
                    self.steps[job.name].workers = "thread"

        def start(n: str) -> None:
            nonlocal procs
            started.add(n)
            cls, step = self.reg[n], self.steps[n]
            step.state, step.started = "running", time.time()
            if cls.scope == "corpus":
                stored = self.storage.get_state(f"fp:{n}") or ""
                if stored == f"{fps[n]}:{sig}" and not any(d in self.changed for d in self.deps[n]):
                    self._finish(n, "cached", finished)
                    return
                if stored.split(":", 1)[0] != fps[n]:  # new version or options: its tables are rebuilt
                    for t in cls.tables:
                        self.storage.drop(t.name)
                step.message = "Starting"
                futures[threads.submit(self._run_corpus, n, source)] = (n, None)
                return
            if self.storage.get_state(f"fp:{n}") != fps[n]:
                with self.storage.transaction():
                    for t in cls.tables:
                        self.storage.drop(t.name)
                        self.storage.ensure(t)
                    self.storage.delete("module_done", "module = ?", (n,))
                    self.storage.set_state(f"fp:{n}", fps[n])
                self.changed.add(n)
            pending = [r[0] for r in self.storage.query(_PENDING_SQL, (n,))]
            step.total = len(pending)
            if not pending:
                self._finish(n, "cached" if n not in self.changed else "done", finished)
                return
            self.changed.add(n)
            job = jobs[n] = _Job(n, self.inst[n], fps[n])
            job.in_process = procs_ok and cls.parallel and workers > 1 and len(pending) >= min_parallel
            size = cls.chunk_size
            if job.in_process:
                if procs is None:
                    procs = ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("spawn"))
                # smaller chunks keep every worker busy on mid-sized batches
                size = max(50, min(size, -(-len(pending) // (workers * 2))))
            job.chunks = [pending[i:i + size] for i in range(0, len(pending), size)]
            step.workers = f"{min(workers, len(job.chunks))} processes" if job.in_process else "thread"
            step.message = f"0 / {len(pending):,} paragraphs"

        # modules others wait for go first (the process pool runs chunks in submission order)
        has_dependents = {d for n in self.order for d in self.deps[n]}

        def feed() -> None:
            nonlocal proc_inflight
            for n, job in sorted(jobs.items(), key=lambda kv: (kv[0] not in has_dependents, self.order.index(kv[0]))):
                while job.chunks:
                    if job.in_process:
                        if proc_inflight >= workers * 2:
                            break
                    elif job.inflight:
                        break  # one chunk at a time per thread-mode module (its instance isn't shared)
                    hashes = job.chunks.pop(0)
                    t_load = time.time()
                    paras, data, cols = self._load_chunk(n, hashes)
                    self.steps[n].load_seconds += time.time() - t_load
                    fut = None
                    if job.in_process:
                        try:
                            fut = procs.submit(_process_chunk, n, job.fp, self.cfg, paras, data, cols)
                            proc_inflight += 1
                        except (BrokenProcessPool, RuntimeError, OSError) as exc:
                            no_processes(exc)
                        else:
                            fut_in_process[fut] = True
                    if fut is None:
                        fut = threads.submit(self._thread_chunk, job, paras, data, cols)
                    job.inflight += 1
                    futures[fut] = (n, hashes)

        try:
            while len(finished) < len(self.order):
                progressed = False
                for n in self.order:
                    if n not in started and all(d in finished for d in self.deps[n]):
                        start(n)
                        progressed = True
                feed()
                self._emit()
                if not futures:
                    if not progressed:
                        raise RuntimeError("module runner stalled: " + ", ".join(n for n in self.order if n not in finished))
                    continue
                done, _ = wait(list(futures), timeout=0.5, return_when=FIRST_COMPLETED)
                for fut in done:
                    n, hashes = futures.pop(fut)
                    if hashes is None:  # corpus module
                        result = fut.result()  # re-raises module errors
                        self.storage.set_state(f"fp:{n}", f"{fps[n]}:{sig}")
                        self.changed.add(n)
                        self.steps[n].message = f"{result:,} rows" if isinstance(result, int) else ""
                        self._finish(n, "done", finished)
                        continue
                    job = jobs[n]
                    job.inflight -= 1
                    if fut_in_process.pop(fut, False):
                        proc_inflight -= 1
                    if isinstance(fut.exception(), BrokenProcessPool):
                        job.chunks.insert(0, hashes)  # redo in a thread
                        no_processes(fut.exception())
                        continue
                    result = fut.result()  # re-raises worker errors
                    t_write = time.time()
                    self._write(n, hashes, result)
                    step = self.steps[n]
                    step.write_seconds += time.time() - t_write
                    step.done += len(hashes)
                    step.message = f"{step.done:,} / {step.total:,} paragraphs"
                    if not job.chunks and not job.inflight:
                        self._finish(n, "done", finished)
        except BaseException:
            for n in self.order:
                if n not in finished and self.steps[n].state == "running":
                    self.steps[n].state = "error"
            for fut in futures:
                fut.cancel()
            raise
        finally:
            threads.shutdown(wait=False, cancel_futures=True)
            if procs is not None:
                procs.shutdown(wait=False, cancel_futures=True)
            self._emit(force=True)
        return {"steps": [self.steps[n].to_json() for n in self.order], "changed": sorted(self.changed),
                "seconds": round(time.time() - t0, 2), "notes": self.notes}

    # --------------------------------------------------------- helpers
    def _finish(self, n: str, state: str, finished: set[str]) -> None:
        step = self.steps[n]
        step.state = state
        step.seconds = time.time() - step.started if step.started else 0.0
        if state == "cached" and not step.message:
            step.message = "up to date"
        finished.add(n)
        self._emit(force=True)

    def _load_chunk(self, n: str, hashes: list[str]) -> tuple[list[ParaIn], dict, dict]:
        rows = self.storage.select_in("para_text", "hash", hashes, ["hash", "text", "role", "code", "arg"])
        by_hash = {r[0]: ParaIn(r[0], r[1], r[2], bool(r[3]), r[4]) for r in rows}
        paras = [by_hash[h] for h in hashes if h in by_hash]
        data: dict[str, dict[str, list[tuple]]] = {}
        cols: dict[str, list[str]] = {}
        for d in self.deps[n]:
            for t in self.reg[d].tables:
                if "para_hash" not in t.names:
                    continue
                grouped: dict[str, list[tuple]] = {}
                for r in self.storage.select_in(t.name, "para_hash", hashes, t.names):
                    grouped.setdefault(r[0], []).append(r)
                data[t.name], cols[t.name] = grouped, t.names
        return paras, data, cols

    def _thread_chunk(self, job: _Job, paras, data, cols) -> dict:
        if not job.ready:
            job.mod.setup()
            job.ready = True
        return job.mod.process(paras, Deps(data, cols))

    def _run_corpus(self, n: str, source: CorpusSource | None) -> int:
        mod = self.inst[n]
        mod.setup()
        for t in self.reg[n].tables:
            self.storage.ensure(t)
        return mod.run_corpus(CorpusContext(self, n, source))

    def _write(self, n: str, hashes: list[str], out: dict[str, list[tuple]]) -> None:
        with self.storage.transaction():
            for t in self.reg[n].tables:
                rows = out.get(t.name)
                if rows:
                    self.storage.insert(Batch.from_rows(t, rows))
            self.storage.insert(Batch.from_rows(MODULE_DONE, [(n, h) for h in hashes]))

    def _step_update(self, n: str, message: str | None = None, fraction: float | None = None) -> None:
        step = self.steps.get(n)
        if step is None:
            return
        if message is not None:
            step.message = message
        if fraction is not None:
            step.fraction = fraction
        self._emit()

    def _emit(self, force: bool = False) -> None:
        if self.on_progress is None:
            return
        now = time.time()
        if not force and now - self._last_emit < 0.2:
            return
        self._last_emit = now
        try:
            self.on_progress([self.steps[n].to_json() for n in self.order])
        except Exception:  # noqa: BLE001 - progress must never break a run
            log.exception("progress callback failed")


def module_stats(storage: Storage, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Every module with its switch state, tables and row counts (for the Settings page)."""
    existing = set(storage.tables())
    enabled = enabled_modules(cfg)
    total = storage.count("para_text") if "para_text" in existing else 0
    done = dict(storage.query("SELECT module, count(*) FROM module_done GROUP BY module")) if "module_done" in existing else {}
    out = []
    for name, cls in registry().items():
        info = cls.info()
        info["enabled"] = name in enabled
        info["tables"] = [{**t, "rows": storage.count(t["name"]) if t["name"] in existing else 0} for t in info["tables"]]
        if cls.scope == "paragraph":
            info["processed"] = done.get(name, 0)
            info["paragraphs"] = total
        note = storage.get_state(f"note:{name}")
        info["notes"] = json.loads(note) if note else {}
        info["requires"] = cls(cfg).dependencies(enabled | {name})
        out.append(info)
    return out


def analyze(items, cfg: dict[str, Any] | None = None) -> dict[str, ParaResult]:
    """Run the enabled modules on a few paragraphs in a throwaway in-memory database and return
    the entities and relations per paragraph id – for tests and the REPL.

    items: (paragraph id, text, role, is_code).  cfg: settings overrides on top of the defaults.
    Corpus modules that need conversations (security) are skipped.
    """
    from ..settings import defaults

    st = open_storage(None)
    try:
        st.ensure(PARA_TEXT)
        ids, rows = {}, {}
        for pid, text, role, code in items:
            ids[pid] = h = para_hash(text, role, code)
            rows[h] = (h, text, role, bool(code), None)
        st.insert(Batch.from_rows(PARA_TEXT, list(rows.values())))
        Runner(st, {**defaults(), **(cfg or {})}).run()
        results = read_results(st)
    finally:
        st.close()
    return {pid: results.get(h) or ParaResult() for pid, h in ids.items()}
