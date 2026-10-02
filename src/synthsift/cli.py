"""Command line entry point: ``uv run synthsift …``"""

from __future__ import annotations

import argparse
import logging
import sys
import tempfile
import webbrowser
from pathlib import Path


def _serve(args: argparse.Namespace) -> None:
    import uvicorn

    from .server import create_app
    from .store import Workspace

    ws = Workspace(Path(args.data_dir).expanduser())
    existing = {d.name for d in ws.datasets.values()}
    for z in args.load or []:
        p = Path(z)
        if p.name not in existing:
            ws.add_zip(p.name, p.read_bytes())
    if any(not d.ingested for d in ws.datasets.values()):
        ws.schedule("ingest")
    url = f"http://{args.host}:{args.port}"
    print(f"SynthSift running at {url}  (data dir: {ws.dir})", file=sys.stderr)
    if args.open:
        webbrowser.open(url)
    uvicorn.run(create_app(ws), host=args.host, port=args.port, log_level="warning")


def _build(args: argparse.Namespace) -> None:
    from .graph.export import to_graphml, to_pyvis_html
    from .store import Workspace

    with tempfile.TemporaryDirectory() as tmp:
        ws = Workspace(Path(tmp))
        if args.settings:
            import json

            ws.settings.update(json.loads(Path(args.settings).read_text()), save=False)
        for z in args.zips:
            ws.add_zip(Path(z).name, Path(z).read_bytes())
        ws.wait()
        if ws.status.state == "error":
            sys.exit(f"processing failed: {ws.status.error}\n{ws.status.message}")
        for w in ws.warnings:
            print(f"warning: {w}", file=sys.stderr)
        out = Path(args.output)
        out.write_text(to_pyvis_html(ws.graph, ws.paragraphs), encoding="utf-8")
        print(f"wrote {out} ({ws.graph.number_of_nodes()} nodes, {ws.graph.number_of_edges()} edges)", file=sys.stderr)
        if args.graphml:
            Path(args.graphml).write_bytes(to_graphml(ws.graph))
            print(f"wrote {args.graphml}", file=sys.stderr)
        ws.close()


def _harnesses(_: argparse.Namespace) -> None:
    from .harnesses import all_parsers

    for p in all_parsers():
        state = "ready" if p.implemented else "stub"
        print(f"{p.name:<14} {state:<6} aliases: {', '.join(p.aliases) or '-'}")


def _checks(args: argparse.Namespace) -> None:
    from . import checks
    from .settings import SettingsStore

    data_dir = Path(args.data_dir).expanduser()
    problems = checks.load_user_checks([data_dir / "checks"])
    cfg = SettingsStore(data_dir / "settings.json" if (data_dir / "settings.json").exists() else None).values
    for c in checks.describe(cfg):
        state = "on" if c["on"] else "parked" if c["parked"] else "off"
        where = "" if c["source"] == "builtin" else f"  [{Path(c['source']).name}{', replaces built-in' if c['overrides_builtin'] else ''}]"
        print(f"{c['order']:>4}  {c['name']:<26} {state:<6} {c['severity']:<8} {c['category']:<18}{where}")
    for p in problems:
        print(f"problem: {p}", file=sys.stderr)


COMMANDS = ("serve", "build", "harnesses", "checks")


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    # "synthsift --open" == "synthsift serve --open"
    if not any(a in COMMANDS for a in argv) and not {"-h", "--help"} & set(argv):
        argv = ["serve", *argv]

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-v", "--verbose", action="store_true")
    ap = argparse.ArgumentParser(prog="synthsift", description="Graph LLM agent transcripts without an LLM.",
                                 parents=[common])
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", parents=[common], help="run the web UI (default)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--data-dir", default=".synthsift", help="where uploads, settings and the database are kept")
    s.add_argument("--load", nargs="*", metavar="ZIP", help="zip(s) to load on start")
    s.add_argument("--open", action="store_true", help="open a browser tab")
    s.set_defaults(func=_serve)

    b = sub.add_parser("build", parents=[common], help="zip(s) -> standalone pyvis HTML (no server)")
    b.add_argument("zips", nargs="+")
    b.add_argument("-o", "--output", default="synthsift-graph.html")
    b.add_argument("--graphml", help="also write GraphML here")
    b.add_argument("--settings", help="JSON file with settings overrides")
    b.set_defaults(func=_build)

    h = sub.add_parser("harnesses", parents=[common], help="list registered transcript parsers")
    h.set_defaults(func=_harnesses)

    c = sub.add_parser("checks", parents=[common], help="list security checks (built-in and your own)")
    c.add_argument("--data-dir", default=".synthsift", help="its checks/ folder holds your own checks")
    c.set_defaults(func=_checks)

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    args.func(args)


if __name__ == "__main__":
    main()
