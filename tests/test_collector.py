"""The standalone collector in collector/: glob lists, zip layout, manifest and ingest round trip."""

import csv
import importlib.util
import io
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import zstandard

from synthsift.harnesses import get_parser
from synthsift.ingest import read_zip
from test_agent_harnesses import CC_ROWS, cc_jsonl, jsonl, make_db, oc_events

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "collector" / "synthsift_collect.py"
GLOBS = ROOT / "collector" / "globs"

spec = importlib.util.spec_from_file_location("synthsift_collect", SCRIPT)
sc = importlib.util.module_from_spec(spec)
sys.modules["synthsift_collect"] = sc  # dataclasses look the module up while the class is created
spec.loader.exec_module(sc)


def fake_home(tmp_path: Path) -> tuple[Path, object]:
    """A home directory with data from every agent; returns it and an open
    OpenClaw database connection (a live gateway: rows still only in the WAL)."""
    home = tmp_path / "home" / "alice"
    proj = home / ".claude" / "projects" / "-home-alice-proj"
    (proj / "s-1" / "subagents").mkdir(parents=True)
    (proj / "s-1.jsonl").write_bytes(cc_jsonl(CC_ROWS))
    (proj / "s-1" / "subagents" / "agent-a7.jsonl").write_bytes(cc_jsonl(CC_ROWS[1:3]))
    (proj / "s-1" / "tool-results").mkdir()
    (proj / "s-1" / "tool-results" / "big.txt").write_text("not collected")
    (home / ".claude" / "settings.json").write_text("{}")

    agent = home / ".openclaw" / "agents" / "work"
    (agent / "agent").mkdir(parents=True)
    (agent / "sessions" / "cold").mkdir(parents=True)
    con = make_db(agent / "agent" / "openclaw-agent.sqlite", wal=True)
    (agent / "sessions" / "old.jsonl").write_bytes(jsonl(oc_events()))
    (agent / "sessions" / "old.jsonl.lock").write_text('{"pid": 1}')
    (agent / "sessions" / "gone.jsonl.deleted.2026-06-25T08-00-00Z.zst").write_bytes(
        zstandard.ZstdCompressor().compress(jsonl(oc_events())))
    (agent / "sessions" / "sessions.json").write_text("{}")
    (agent / "sessions" / "cold" / "x.jsonl.zst").write_bytes(zstandard.ZstdCompressor().compress(jsonl(oc_events())))
    legacy = home / ".clawdbot" / "agents" / "work" / "sessions"  # same relative path as the current install
    legacy.mkdir(parents=True)
    (legacy / "old.jsonl").write_bytes(jsonl(oc_events()))

    (home / ".gemini" / "tmp" / "abc123" / "chats").mkdir(parents=True)
    (home / ".gemini" / "tmp" / "abc123" / "chats" / "session-2026-06-01T10-00-a1.jsonl").write_text("{}\n")
    (home / ".hermes").mkdir()
    import sqlite3
    sqlite3.connect(home / ".hermes" / "state.db").execute("CREATE TABLE sessions (id)").connection.close()
    return home, con


def test_shipped_glob_lists_match_synthsift_parsers():
    rules = sc.load_rules(GLOBS)
    assert {r.agent for r in rules} == {"claude_code", "openclaw", "gemini", "antigravity", "hermes"}
    for r in rules:
        assert get_parser(r.agent) is not None and get_parser(r.agent).name == r.agent  # zips route to the parser
        for p in r.patterns:
            assert p.startswith(("~/", "${")) and "\\" not in p, f"{r.agent}: {p}"


def test_collects_every_agent_into_the_upload_layout(tmp_path):
    home, con = fake_home(tmp_path)
    try:
        matches = sc.find(sc.load_rules(GLOBS), [("alice", home, True)], env={})
        rels = sorted((m.agent, m.rel) for m in matches)
        assert rels == [
            ("claude_code", "projects/-home-alice-proj/s-1.jsonl"),
            ("claude_code", "projects/-home-alice-proj/s-1/subagents/agent-a7.jsonl"),
            ("gemini", "tmp/abc123/chats/session-2026-06-01T10-00-a1.jsonl"),
            ("hermes", "state.db"),
            ("openclaw", "agents/work/agent/openclaw-agent.sqlite"),
            ("openclaw", "agents/work/sessions/cold/x.jsonl.zst"),
            ("openclaw", "agents/work/sessions/gone.jsonl.deleted.2026-06-25T08-00-00Z.zst"),
            ("openclaw", "agents/work/sessions/old.jsonl"),
            ("openclaw", "agents/work/sessions/old.jsonl"),  # the ~/.clawdbot copy
        ]
        out = tmp_path / "c.zip"
        rep = sc.write_zip(matches, out, "laptop")
    finally:
        con.close()
    assert len(rep.written) == 9 and not rep.skipped

    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
        assert "laptop/alice/openclaw/from-clawdbot/agents/work/sessions/old.jsonl" in names
        assert "laptop/alice/openclaw/agents/work/sessions/old.jsonl" in names
        assert zf.comment.startswith(b"synthsift-collect")
        manifest = list(csv.DictReader(io.StringIO(zf.read(sc.MANIFEST).decode())))
    assert len(manifest) == 9 and all(len(r["sha256"]) == 64 for r in manifest)
    methods = {r["zip_path"].rsplit("/", 1)[-1]: r["method"] for r in manifest}
    assert methods["openclaw-agent.sqlite"] == "sqlite-backup" and methods["state.db"] == "sqlite-backup"

    rep = read_zip(out.read_bytes(), "c")
    assert {(c.host, c.user, c.harness) for c in rep.conversations} == {("laptop", "alice", "claude_code"),
                                                                        ("laptop", "alice", "openclaw")}
    # the database snapshot includes rows that were only in the write-ahead log
    assert sum(1 for c in rep.conversations if c.meta.get("channel") == "telegram") == 1
    assert sum(1 for c in rep.conversations if c.meta.get("archive") == "deleted") >= 2
    # placeholder parsers: kept in the zip, skipped with a warning
    assert all("placeholder" in w for w in rep.warnings) and len(rep.warnings) == 2


def test_environment_overrides_and_deduplication(tmp_path):
    home, con = fake_home(tmp_path)
    con.close()
    elsewhere = tmp_path / "custom-claude"
    (elsewhere / "projects" / "p").mkdir(parents=True)
    (elsewhere / "projects" / "p" / "s-9.jsonl").write_bytes(cc_jsonl(CC_ROWS))
    rules = sc.load_rules(GLOBS, ["claude_code"])
    got = sorted(m.rel for m in sc.find(rules, [("alice", home, True)], env={"CLAUDE_CONFIG_DIR": str(elsewhere)}))
    assert "projects/p/s-9.jsonl" in got and "projects/-home-alice-proj/s-1.jsonl" in got
    # pointing the variable at the default folder must not collect everything twice
    got = [m.rel for m in sc.find(rules, [("alice", home, True)], env={"CLAUDE_CONFIG_DIR": str(home / ".claude")})]
    assert len(got) == len(set(got)) == 2
    # another user's environment is unknown: ${VAR} lines are skipped for them
    assert len(sc.find(rules, [("alice", home, False)], env={"CLAUDE_CONFIG_DIR": str(elsewhere)})) == 2


def test_since_filter_and_expand_rules(tmp_path):
    home, con = fake_home(tmp_path)
    con.close()
    old = time.time() - 30 * 86400
    for p in (home / ".claude").rglob("*.jsonl"):
        os.utime(p, (old, old))
    rules = sc.load_rules(GLOBS, ["claude_code", "hermes"])
    got = {m.agent for m in sc.find(rules, [("alice", home, True)], env={}, since=time.time() - 7 * 86400)}
    assert got == {"hermes"}
    h = Path("/home/bob")
    assert sc.expand("~/.x/./a/*.db", h, {}, False) == ("/home/bob/.x", "a/*.db")
    assert sc.expand("~/runs/*.jsonl", h, {}, False) == ("/home/bob/runs", "*.jsonl")
    assert sc.expand("${LOCALAPPDATA}/hermes/./state.db", h, {}, False) == ("/home/bob/AppData/Local/hermes", "state.db")
    assert sc.expand("${UNSET_VAR}/x/*.db", h, {}, True) is None


def test_script_runs_standalone(tmp_path):
    home, con = fake_home(tmp_path)
    con.close()
    assert "synthsift." not in SCRIPT.read_text().replace("synthsift.harnesses", "")  # stdlib only
    env = {**os.environ, "HOME": str(home), "USER": "alice", "LOGNAME": "alice"}
    for var in ("CLAUDE_CONFIG_DIR", "OPENCLAW_STATE_DIR", "GEMINI_CLI_HOME", "HERMES_HOME", "LOCALAPPDATA"):
        env.pop(var, None)
    dry = subprocess.run([sys.executable, "-I", str(SCRIPT), "--dry-run", "--host", "laptop", "--agents", "claude_code"],
                         env=env, capture_output=True, text=True, check=True)
    assert "laptop/alice/claude_code/projects/-home-alice-proj/s-1.jsonl" in dry.stdout
    out = tmp_path / "run.zip"
    run = subprocess.run([sys.executable, "-I", str(SCRIPT), "-o", str(out), "--host", "laptop"],
                         env=env, capture_output=True, text=True, check=True)
    assert "wrote" in run.stderr and out.is_file()
    listing = subprocess.run([sys.executable, "-I", str(SCRIPT), "--list-agents"], env=env, capture_output=True,
                             text=True, check=True).stdout
    assert "openclaw:" in listing and "!*.lock" in listing
