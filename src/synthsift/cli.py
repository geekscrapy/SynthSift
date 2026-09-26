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
    if ws.datasets:
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


def _harnesses(_: argparse.Namespace) -> None:
    from .harnesses import all_parsers

    for p in all_parsers():
        state = "ready" if p.implemented else "stub"
        print(f"{p.name:<14} {state:<6} aliases: {', '.join(p.aliases) or '-'}")


def _collect(args: argparse.Namespace) -> None:
    from .collect import default_host, discover, write_zip

    harnesses = tuple(h for h, off in (("claude_code", args.no_claude_code), ("openclaw", args.no_openclaw)) if not off)
    found = discover(all_users=args.all_users, since_days=args.since_days,
                     claude_dir=Path(args.claude_dir).expanduser() if args.claude_dir else None,
                     openclaw_dir=Path(args.openclaw_dir).expanduser() if args.openclaw_dir else None,
                     harnesses=harnesses)
    host = args.host or default_host()
    if args.dry_run or not found:
        for f in found:
            print(f"{host}/{f.user}/{f.harness}/{f.arcpath}  ({f.kind})")
        if not found:
            print("no Claude Code or OpenClaw transcripts found", file=sys.stderr)
            if not args.all_users:
                print("hint: --all-users searches every home directory (needs permission to read them)", file=sys.stderr)
        return
    out = Path(args.output)
    rep = write_zip(found, out, host)
    counts: dict[str, int] = {}
    for f in rep.files:
        counts[f"{f.harness}:{f.kind}"] = counts.get(f"{f.harness}:{f.kind}", 0) + 1
    names = {"claude_code:session": "Claude Code session", "claude_code:subagent": "Claude Code sub-agent",
             "openclaw:database": "OpenClaw database", "openclaw:legacy": "OpenClaw transcript",
             "openclaw:archive": "OpenClaw archive"}
    parts = [f"{n} {names.get(k, k)}{'s' if n != 1 else ''}" for k, n in sorted(counts.items())]
    users = sorted({f.user for f in rep.files})
    print(f"wrote {out} ({rep.bytes / 1e6:.1f} MB): {', '.join(parts)} for {', '.join(users)} on {host}", file=sys.stderr)
    for s in rep.skipped:
        print(f"skipped {s}", file=sys.stderr)
    print(f"next: synthsift serve --load {out}", file=sys.stderr)


COMMANDS = ("serve", "build", "harnesses", "collect")


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
    s.add_argument("--data-dir", default=".synthsift", help="where uploads and settings are kept")
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

    c = sub.add_parser("collect", parents=[common],
                       help="zip this machine's Claude Code / OpenClaw transcripts for upload")
    c.add_argument("-o", "--output", default="synthsift-collect.zip")
    c.add_argument("--host", help="host name to file the transcripts under (default: this machine's)")
    c.add_argument("--all-users", action="store_true", help="search every home directory, not just yours")
    c.add_argument("--since-days", type=float, help="only files changed in the last N days")
    c.add_argument("--claude-dir", help="Claude Code config dir (default: $CLAUDE_CONFIG_DIR or ~/.claude)")
    c.add_argument("--openclaw-dir", help="OpenClaw state dir (default: $OPENCLAW_STATE_DIR or ~/.openclaw)")
    c.add_argument("--no-claude-code", action="store_true", help="skip Claude Code")
    c.add_argument("--no-openclaw", action="store_true", help="skip OpenClaw")
    c.add_argument("--dry-run", action="store_true", help="list what would be collected")
    c.set_defaults(func=_collect)

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    args.func(args)


if __name__ == "__main__":
    main()
