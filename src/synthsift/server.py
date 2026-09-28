"""FastAPI app: JSON API + the single page UI."""

from __future__ import annotations

import csv
import gzip
import io
import json
import re
from importlib import resources
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__
from .graph.export import subgraph, to_graphml, to_pyvis_html
from .harnesses import all_parsers
from .modules import registry
from .modules.ioc import LIST_SUFFIXES, disabled_lists
from .modules.runner import module_stats
from .nlp.pipeline import installed_models
from .store import Workspace

STATIC = Path(str(resources.files("synthsift").joinpath("static")))
SAMPLES = Path(__file__).resolve().parents[2] / "samples"


def _vis_dir() -> Path:
    import pyvis

    base = Path(pyvis.__file__).parent
    for cand in sorted(base.glob("**/vis-9*/vis-network.min.js"), reverse=True):
        return cand.parent
    raise RuntimeError("vis-network assets not found in the pyvis package")


def create_app(workspace: Workspace) -> FastAPI:
    app = FastAPI(title="SynthSift", version=__version__)
    app.state.ws = workspace
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    app.mount("/vendor/vis", StaticFiles(directory=_vis_dir()), name="vis")

    def ws() -> Workspace:
        return app.state.ws

    @app.get("/", response_class=HTMLResponse)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page() -> FileResponse:
        return FileResponse(STATIC / "settings.html")

    @app.get("/nodes", response_class=HTMLResponse)
    def nodes_page() -> FileResponse:
        return FileResponse(STATIC / "nodes.html")

    @app.get("/timeline", response_class=HTMLResponse)
    def timeline_page() -> FileResponse:
        return FileResponse(STATIC / "timeline.html")

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard_page() -> FileResponse:
        return FileResponse(STATIC / "dashboard.html")

    # -------------------------------------------------------------- data
    @app.get("/api/status")
    def status() -> dict:
        return {**ws().status.to_json(), "datasets": len(ws().datasets)}

    # ----------------------------------------------------------- modules
    @app.get("/api/modules")
    def modules() -> dict:
        w = ws()
        steps = {s["name"]: s for s in (w.status.steps or w.last_run.get("steps", []))}
        mods = module_stats(w.db, w.settings.values)
        for m in mods:
            m["last_run"] = steps.get(m["name"])
        return {"modules": mods, "paragraphs": len(w.paragraphs), "database": str(w.dir / "synthsift.duckdb")}

    @app.get("/api/enrichment/{pid:path}")
    def enrichment(pid: str) -> dict:
        out = ws().enrichment(pid)
        if out is None:
            raise HTTPException(404, "unknown paragraph")
        return out

    @app.get("/api/modules/{name}/export")
    def export_module(name: str, table: str | None = None) -> Response:
        cls = registry().get(name)
        if cls is None or not cls.tables:
            raise HTTPException(404, "unknown module")
        tbl = next((t for t in cls.tables if t.name == (table or cls.tables[0].name)), None)
        if tbl is None or tbl.name not in ws().db.tables():
            raise HTTPException(404, "no such table (has the module run?)")
        cols = ", ".join(f'x."{c}"' for c in tbl.names if c != "para_hash")
        join = "para_hash" in tbl.names
        sql = (f'SELECT p.id AS paragraph, p.conv, p.event, {cols} FROM "{tbl.name}" x JOIN paragraphs p ON p.hash = x.para_hash '
               "ORDER BY p.conv, p.seq" if join else f'SELECT {cols} FROM "{tbl.name}" x')
        buf = io.StringIO()
        cur = ws().db.con.execute(sql)
        w = csv.writer(buf)
        w.writerow([d[0] for d in cur.description])
        for row in cur.fetchall():
            w.writerow(["|".join(map(str, v)) if isinstance(v, list) else v for v in row])
        return Response(buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="synthsift-{tbl.name}.csv"'})

    # ------------------------------------------------------------- lists
    def _lists() -> list[dict]:
        w = ws()
        disabled = disabled_lists(w.settings.values)
        note = w.db.get_state("note:ioc")
        loaded = {d["file"]: d["entries"] for d in (json.loads(note).get("lists", []) if note else [])}
        out = []
        if w.lists_dir.is_dir():
            for f in sorted(w.lists_dir.iterdir()):
                if f.is_file():
                    out.append({"name": f.name, "bytes": f.stat().st_size, "enabled": f.name not in disabled,
                                "entries": loaded.get(f.name)})
        return out

    def _list_changed() -> None:
        if ws().settings.values.get("mod.ioc"):
            ws().schedule("enrich")

    @app.get("/api/lists")
    def get_lists() -> dict:
        return {"lists": _lists()}

    @app.post("/api/lists")
    async def upload_lists(files: list[UploadFile] = File(...)) -> dict:
        ws().lists_dir.mkdir(parents=True, exist_ok=True)
        for f in files:
            name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(f.filename or "list.txt").name).lstrip(".") or "list.txt"
            if not name.lower().removesuffix(".gz").endswith(LIST_SUFFIXES):
                raise HTTPException(400, f"{name}: lists must be {', '.join(LIST_SUFFIXES)} (optionally .gz)")
            dest = ws().lists_dir / name
            tmp = dest.with_name(dest.name + ".part")
            with tmp.open("wb") as out:  # stream: lists can be hundreds of megabytes
                while chunk := await f.read(4 << 20):
                    out.write(chunk)
            tmp.replace(dest)
        _list_changed()
        return {"lists": _lists()}

    @app.delete("/api/lists/{name}")
    def delete_list(name: str) -> dict:
        path = ws().lists_dir / Path(name).name
        if not path.is_file():
            raise HTTPException(404, "unknown list")
        path.unlink()
        _list_changed()
        return {"lists": _lists()}

    @app.put("/api/lists/{name}")
    async def toggle_list(name: str, request: Request) -> dict:
        body = await request.json()
        name = Path(name).name
        disabled = [x for x in str(ws().settings.values.get("ioc_disabled", "")).splitlines() if x.strip() and x != name]
        if not body.get("enabled", True):
            disabled.append(name)
        if ws().settings.update({"ioc_disabled": "\n".join(disabled)}):
            _list_changed()
        return {"lists": _lists()}

    @app.get("/api/graph")
    def graph(request: Request) -> Response:
        payload = ws().payload
        if payload is None:
            return JSONResponse({"empty": True, "status": ws().status.to_json()})
        body = json.dumps({**payload, "version": ws().status.version}, separators=(",", ":")).encode()
        if "gzip" in request.headers.get("accept-encoding", "") and len(body) > 4096:
            return Response(gzip.compress(body, 5), media_type="application/json",
                            headers={"Content-Encoding": "gzip"})
        return Response(body, media_type="application/json")

    @app.post("/api/upload")
    async def upload(files: list[UploadFile] = File(...)) -> dict:
        added = []
        for f in files:
            data = await f.read()
            if not data[:4].startswith(b"PK"):
                raise HTTPException(400, f"{f.filename} is not a zip file")
            added.append(ws().add_zip(f.filename or "upload.zip", data).to_json())
        return {"datasets": added}

    @app.post("/api/samples")
    def load_samples() -> dict:
        zips = sorted(SAMPLES.glob("*.zip"))
        if not zips:
            raise HTTPException(404, "no sample zips found; run scripts/generate_samples.py")
        existing = {d.name for d in ws().datasets.values()}
        added = [ws().add_zip(z.name, z.read_bytes()).to_json() for z in zips if z.name not in existing]
        return {"datasets": added}

    @app.get("/api/datasets")
    def datasets() -> list[dict]:
        return [d.to_json() for d in ws().datasets.values()]

    @app.delete("/api/datasets/{ds_id}")
    def delete_dataset(ds_id: str) -> dict:
        if not ws().remove_dataset(ds_id):
            raise HTTPException(404, "unknown dataset")
        return {"ok": True}

    @app.delete("/api/datasets")
    def clear_datasets() -> dict:
        ws().clear()
        return {"ok": True}

    # ---------------------------------------------------------- settings
    @app.get("/api/settings")
    def get_settings() -> dict:
        s = ws().settings
        return {
            "schema": s.schema_json(),
            "values": s.values,
            "installed_models": installed_models(),
            "modules": [cls.info() for cls in registry().values()],
            "harnesses": [
                {"name": p.name, "label": p.label, "aliases": list(p.aliases), "implemented": p.implemented,
                 "description": p.description}
                for p in all_parsers()
            ],
        }

    @app.put("/api/settings")
    async def put_settings(request: Request) -> dict:
        changes = await request.json()
        scopes = ws().settings.update(changes)
        if "segment" in scopes:
            ws().schedule("segment")
        elif "parse" in scopes:
            ws().schedule("enrich")
        elif "graph" in scopes:
            ws().schedule("graph")
        return {"values": ws().settings.values, "scopes": sorted(scopes)}

    @app.post("/api/settings/reset")
    def reset_settings() -> dict:
        ws().settings.reset()
        ws().schedule("segment")
        return {"values": ws().settings.values}

    # ------------------------------------------------------- annotations
    @app.get("/api/annotations")
    def get_annotations() -> dict:
        return ws().annotations.to_json()

    @app.put("/api/annotations")
    async def put_annotation(request: Request) -> dict:
        body = await request.json()
        target = str(body.get("target", ""))
        try:
            ann = ws().annotations.upsert(
                target, list(body.get("tags") or []), str(body.get("comment") or ""),
                str(body.get("label") or ""), body.get("conv"), body.get("ts"))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"annotation": ann.model_dump() if ann else None, "tags": ws().annotations.tags()}

    @app.delete("/api/annotations")
    def delete_annotation(target: str) -> dict:
        return {"deleted": ws().annotations.delete(target)}

    @app.post("/api/tags")
    async def add_tag(request: Request) -> dict:
        body = await request.json()
        try:
            ws().annotations.add_tag(str(body.get("name", "")), str(body.get("color") or "#5F6368"))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"tags": ws().annotations.tags()}

    @app.delete("/api/tags")
    def remove_tag(name: str) -> dict:
        ws().annotations.remove_tag(name)
        return {"tags": ws().annotations.tags()}

    @app.get("/api/annotations/export")
    def export_annotations() -> Response:
        return Response(ws().annotations.export(), media_type="application/json",
                        headers={"Content-Disposition": 'attachment; filename="synthsift-annotations.json"'})

    # ------------------------------------------------------------ export
    def _selected(convs: str | None):
        if ws().graph is None:
            raise HTTPException(404, "no graph yet")
        return subgraph(ws().graph, convs.split(",") if convs else None)

    @app.get("/api/export/pyvis")
    def export_pyvis(convs: str | None = None, dark: bool = False) -> Response:
        html = to_pyvis_html(_selected(convs), ws().paragraphs, dark=dark)
        return Response(html, media_type="text/html",
                        headers={"Content-Disposition": 'attachment; filename="synthsift-graph.html"'})

    @app.get("/api/export/graphml")
    def export_graphml(convs: str | None = None) -> Response:
        return Response(to_graphml(_selected(convs)), media_type="application/xml",
                        headers={"Content-Disposition": 'attachment; filename="synthsift-graph.graphml"'})

    return app
