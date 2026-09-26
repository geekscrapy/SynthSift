"""OpenClaw (personal AI agent, formerly Clawdbot / Moltbot) session transcripts.

Where to find them, per agent (``$OPENCLAW_STATE_DIR`` moves ``~/.openclaw``)::

    ~/.openclaw/agents/<agentId>/agent/openclaw-agent.sqlite   current releases
    ~/.openclaw/agents/<agentId>/sessions/<sessionId>.jsonl     older releases
    ~/.openclaw/agents/<agentId>/sessions/*.jsonl.deleted.<ts>[.zst]
    ~/.openclaw/agents/<agentId>/sessions/*.jsonl.reset.<ts>[.zst]
    ~/.openclaw/agents/<agentId>/sessions/cold/*.jsonl.zst      cold storage

``synthsift collect`` packs all of these into an upload zip.

Every source holds the same append-only event stream:

* a header ``{"type": "session", "id", "timestamp", "cwd", "parentSession"?}``
* ``message`` entries whose ``message.role`` is ``user`` (text / image
  content), ``assistant`` (``text``, ``thinking`` and ``toolCall`` blocks with
  ``id`` / ``name`` / ``arguments``, plus ``provider`` / ``model``) or
  ``toolResult`` (``toolCallId``, ``toolName``, ``content``, ``isError``)
* ``compaction`` / ``branch_summary`` summaries, ``reset`` boundaries,
  ``model_change``, ``custom_message`` (extension text that reached the model)
  and ``custom`` / ``label`` bookkeeping.

Entries carry ``id`` / ``parentId`` (a tree: rewinds and forks leave abandoned
branches); they are kept in file order, which is the order things happened.

In the SQLite database the events live in ``transcript_events`` (``event_json``
text, or zstd-compressed ``event_zstd``), session metadata (channel, chat type,
display name, model) in ``session_windows`` / ``session_nodes``, and deleted or
reset sessions in ``session_transcript_archives`` – those are read too and
tagged ``archive: deleted`` / ``reset`` in the conversation metadata.

Messages another agent session sent in (``provenance.kind == "inter_session"``)
stay ``user`` turns but are marked ``inter_session`` so they can be told apart
from a human.  Runtime-context carrier turns become ``system`` messages.
"""

from __future__ import annotations

import json
import re
from collections import Counter
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from ..models import Block, Conversation, Message
from .base import ZSTD_MAGIC, HarnessParser, register, zstd_decompress

SQLITE_MAGIC = b"SQLite format 3\x00"
_ARCHIVE = re.compile(r"\.jsonl\.(deleted|reset)\.", re.I)


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = []
    for part in content if isinstance(content, list) else [content]:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict):
            if part.get("type") == "image":
                parts.append("[image]")
            elif part.get("text") is not None:
                parts.append(str(part["text"]))
    return "\n\n".join(p for p in parts if p)


def _model_id(msg: dict[str, Any]) -> str | None:
    model = msg.get("model")
    if not model:
        return None
    provider = msg.get("provider")
    return f"{provider}/{model}" if provider and "/" not in str(model) else str(model)


def events_to_conversation(rows: list[dict[str, Any]], meta: dict[str, Any] | None = None) -> Conversation | None:
    """Turn one session's event stream into a conversation (``None`` if it has no turns)."""
    messages: list[Message] = []
    models: list[str] = []
    info: dict[str, Any] = dict(meta or {})
    started: str | None = None

    def add(role: str, blocks: list[Block], ts: Any, model: str | None = None, **m: Any) -> None:
        nonlocal started
        ts = HarnessParser.iso_time(ts)
        started = started or ts
        messages.append(Message(role=role, blocks=blocks, timestamp=ts, model=model, meta={k: v for k, v in m.items() if v}))

    def system(text: str, ts: Any, kind: str) -> None:
        if text and text.strip():
            add("system", [Block.text_block(text.strip())], ts, kind=kind)

    for row in rows:
        if not isinstance(row, dict):
            continue
        typ = row.get("type")
        ts = row.get("timestamp")
        if typ == "session":
            info.setdefault("session_id", row.get("id"))
            for src, dst in (("cwd", "cwd"), ("parentSession", "parent_session"), ("version", "format_version")):
                if row.get(src) is not None:
                    info.setdefault(dst, row[src])
            started = started or HarnessParser.iso_time(ts)
            continue
        if typ == "model_change":
            mid = row.get("modelId") or row.get("model")
            if mid:
                models.append(f"{row['provider']}/{mid}" if row.get("provider") else str(mid))
            continue
        if typ == "compaction":
            system(f"Compaction summary:\n{row.get('summary', '')}", ts, "compaction")
            continue
        if typ == "branch_summary":
            system(f"Branch summary:\n{row.get('summary', '')}", ts, "branch_summary")
            continue
        if typ == "reset":
            system("History reset – a fresh context window starts here.", ts, "reset")
            continue
        if typ == "custom_message":
            if row.get("display", True):
                system(_content_text(row.get("content")), ts, str(row.get("customType") or "custom_message"))
            continue
        if typ != "message":
            continue  # custom, label, thinking_level_change, … are bookkeeping

        msg = row.get("message") or {}
        role = msg.get("role")
        ts = ts or msg.get("timestamp")
        if role == "user":
            text = _content_text(msg.get("content"))
            provenance = (msg.get("provenance") or {}).get("kind")
            if msg.get("runtimeContextCarrier"):
                system(text, ts, "runtime_context")
            elif text.strip():
                add("user", [Block.text_block(text)], ts, provenance=provenance,
                    inter_session=provenance == "inter_session")
        elif role == "assistant":
            model = _model_id(msg)
            if model:
                models.append(model)
            blocks: list[Block] = []
            content = msg.get("content")
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            for part in content or []:
                if not isinstance(part, dict):
                    continue
                ptype = part.get("type")
                if ptype == "text" and str(part.get("text", "")).strip():
                    blocks.append(Block.text_block(str(part["text"])))
                elif ptype == "thinking" and str(part.get("thinking", "")).strip() and not part.get("redacted"):
                    blocks.append(Block.thinking(str(part["thinking"])))
                elif ptype in ("toolCall", "tool_call", "tool_use"):
                    args = part.get("arguments", part.get("input"))
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {"input": args}
                    blocks.append(Block.tool_call(str(part.get("name") or "tool"),
                                                  args if isinstance(args, dict) else {"input": args}, part.get("id")))
            if blocks:
                add("assistant", blocks, ts, model)
            if msg.get("stopReason") == "error" and msg.get("errorMessage"):
                system(f"Model error: {msg['errorMessage']}", ts, "model_error")
        elif role == "toolResult":
            add("tool", [Block.tool_result(_content_text(msg.get("content")), msg.get("toolCallId"),
                                           msg.get("toolName"), bool(msg.get("isError")))], ts)
        elif role == "bashExecution":  # a shell command the user ran directly
            call_id = f"user-shell-{row.get('id') or len(messages)}"
            add("user", [Block.tool_call("user_shell", {"command": str(msg.get("command", ""))}, call_id)], ts, kind="user_shell")
            out = str(msg.get("output") or "")
            exit_code = msg.get("exitCode")
            if exit_code not in (None, 0):
                out = f"{out}\n[exit code {exit_code}]".strip()
            add("tool", [Block.tool_result(out, call_id, "user_shell", exit_code not in (None, 0))], ts)
        elif role:
            system(_content_text(msg.get("content")), ts, str(role))

    if not any(m.role in ("user", "assistant", "tool") for m in messages):
        return None
    conv = Conversation(messages=messages, started_at=info.get("started_at") or started,
                        model=Counter(models).most_common(1)[0][0] if models else info.get("model"),
                        meta={k: v for k, v in info.items() if v not in (None, "")})
    key = str(conv.meta.get("session_key") or "")
    if key.startswith("agent:"):
        conv.meta.setdefault("agent", key.split(":")[1])
    title = conv.meta.pop("title", None)
    if title:
        conv.title = str(title)
    return conv


@register
class OpenClawParser(HarnessParser):
    name = "openclaw"
    label = "OpenClaw"
    aliases = ("open-claw", "open_claw", "clawdbot", "moltbot", ".openclaw", ".clawdbot", ".moltbot")
    extensions = (".jsonl", ".json", ".zst", ".sqlite", ".db")
    description = "OpenClaw agent sessions: per-agent openclaw-agent.sqlite, legacy sessions/*.jsonl and archives."

    def sniff(self, raw: bytes, filename: str) -> float:
        if raw[:16] == SQLITE_MAGIC:
            return 0.95 if b"transcript_events" in raw[:2_000_000] else 0.0
        try:
            raw, _ = self.decompress(raw[:1_000_000] if raw[:4] != ZSTD_MAGIC else raw, filename)
        except Exception:  # noqa: BLE001 - truncated compressed data
            return 0.0
        head = raw[:65536].decode("utf-8", errors="ignore")
        first = head.split("\n", 1)[0]
        if '"type":"session"' in first.replace(" ", "") and '"cwd"' in first:
            return 0.9
        if '"type":"message"' in head.replace(" ", "") and '"parentId"' in head and ('"toolCall"' in head or '"toolResult"' in head):
            return 0.85
        return 0.0

    def parse(self, raw: bytes, filename: str) -> list[Conversation]:
        if raw[:16] == SQLITE_MAGIC:
            return self.parse_sqlite(raw, filename)
        raw, inner = self.decompress(raw, filename)
        text = raw.decode("utf-8-sig", errors="replace").lstrip()
        if text.startswith("{") and "\n" in text.rstrip() and not text.split("\n", 1)[1].lstrip().startswith("{"):
            return []  # a pretty-printed JSON file such as the legacy sessions.json index – not a transcript
        rows, bad = self.read_jsonl(raw)
        if bad:
            self.warnings.append(f"skipped {bad} unreadable line(s)")
        meta: dict[str, Any] = {}
        m = _ARCHIVE.search(inner)
        if m:
            meta["archive"] = m.group(1).lower()
        elif "/cold/" in filename or filename.lower().endswith(".jsonl.zst"):
            meta["archive"] = "cold"
        conv = events_to_conversation(rows, meta)
        return [conv] if conv else []

    # --------------------------------------------------------------- SQLite
    def parse_sqlite(self, raw: bytes, filename: str) -> list[Conversation]:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agent.sqlite"
            path.write_bytes(raw)
            if self.companions.get("-wal"):
                # recent writes still in the write-ahead log of a live copy
                Path(f"{path}-wal").write_bytes(self.companions["-wal"])
            con = sqlite3.connect(path)
            try:
                return self._read_db(con, filename)
            finally:
                con.close()

    def _read_db(self, con: sqlite3.Connection, filename: str) -> list[Conversation]:
        con.row_factory = sqlite3.Row
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "transcript_events" not in tables:
            self.warnings.append("SQLite file has no transcript_events table – not an OpenClaw agent database?")
            return []

        def cols(table: str) -> set[str]:
            return {r[1] for r in con.execute(f"PRAGMA table_info({table})")} if table in tables else set()

        # session metadata ------------------------------------------------
        info: dict[str, dict[str, Any]] = {}
        wanted = ("session_key", "channel", "chat_type", "display_name", "model", "model_provider", "started_at",
                  "ended_at", "parent_session_key", "spawned_by", "account_id", "status", "reason")
        have = cols("session_windows")
        if "session_id" in have:
            sel = [c for c in wanted if c in have]
            for r in con.execute(f"SELECT session_id, {', '.join(sel)} FROM session_windows"):
                d = {k: r[k] for k in sel if r[k] not in (None, "")}
                if "display_name" in d:
                    d["title"] = d.pop("display_name")
                if "model" in d and "model_provider" in d:
                    d["model"] = f"{d.pop('model_provider')}/{d['model']}"
                for k in ("started_at", "ended_at"):
                    if k in d:
                        d[k] = self.iso_time(d[k])
                info[r["session_id"]] = d
        have = cols("session_nodes")
        if "current_session_id" in have:
            sel = [c for c in ("session_key", "label", "display_name", "category") if c in have]
            for r in con.execute(f"SELECT current_session_id, {', '.join(sel)} FROM session_nodes"):
                d = info.setdefault(r["current_session_id"], {})
                if "session_key" in sel and r["session_key"]:
                    d.setdefault("session_key", r["session_key"])
                title = (r["label"] if "label" in sel else None) or (r["display_name"] if "display_name" in sel else None)
                if title:
                    d["title"] = title  # an explicit label wins over the window's generated name
                if "category" in sel and r["category"]:
                    d["category"] = r["category"]

        # events -----------------------------------------------------------
        by_session: dict[str, list[dict[str, Any]]] = {}
        skipped = 0
        have = cols("transcript_events")
        zcol = "event_zstd" if "event_zstd" in have else "NULL AS event_zstd"
        for r in con.execute(f"SELECT session_id, seq, event_json, {zcol} FROM transcript_events ORDER BY session_id, seq"):
            try:
                ev = json.loads(r["event_json"] if r["event_json"] is not None else zstd_decompress(r["event_zstd"]))
            except Exception:  # noqa: BLE001 - one bad row must not lose the session
                skipped += 1
                continue
            by_session.setdefault(r["session_id"], []).append(ev)

        # older events moved to cold storage inside the database
        have = cols("session_transcript_cold_archives")
        if {"session_id", "archive_blob"} <= have:
            order = "last_seq" if "last_seq" in have else "rowid"
            cold: dict[str, list[dict[str, Any]]] = {}
            for r in con.execute(f"SELECT session_id, archive_blob FROM session_transcript_cold_archives "
                                 f"WHERE archive_blob IS NOT NULL ORDER BY session_id, {order}"):
                cold.setdefault(r["session_id"], []).extend(self._blob_rows(r["archive_blob"]))
            for sid, rows in cold.items():
                hot = by_session.get(sid, [])
                hot_ids = {e.get("id") for e in hot if isinstance(e, dict)}
                by_session[sid] = [e for e in rows if e.get("id") not in hot_ids] + hot

        convs: list[Conversation] = []
        for sid, rows in by_session.items():
            conv = events_to_conversation(rows, {"session_id": sid, **info.get(sid, {})})
            if conv:
                convs.append(conv)

        # deleted / reset sessions kept as archives --------------------------
        have = cols("session_transcript_archives")
        if {"session_id", "archive_blob"} <= have:
            sel = [c for c in ("session_key", "reason", "created_at", "archive_name") if c in have]
            for r in con.execute(f"SELECT session_id, archive_blob, {', '.join(sel) or 'NULL'} FROM session_transcript_archives"):
                name = (r["archive_name"] if "archive_name" in sel else "") or ""
                m = _ARCHIVE.search(name)
                meta = {"session_id": r["session_id"], **info.get(r["session_id"], {}),
                        "archive": m.group(1).lower() if m else "archived"}
                if "reason" in sel and r["reason"]:
                    meta["archive_reason"] = r["reason"]
                if "session_key" in sel and r["session_key"]:
                    meta["session_key"] = r["session_key"]
                if "created_at" in sel and r["created_at"]:
                    meta["archived_at"] = self.iso_time(r["created_at"])
                try:
                    conv = events_to_conversation(self._blob_rows(r["archive_blob"]), meta)
                except Exception:  # noqa: BLE001
                    skipped += 1
                    continue
                if conv:
                    convs.append(conv)
        if skipped:
            self.warnings.append(f"skipped {skipped} unreadable transcript row(s)")
        convs.sort(key=lambda c: c.started_at or "")
        return convs

    def _blob_rows(self, blob: bytes | None) -> list[dict[str, Any]]:
        if not blob:
            return []
        data = zstd_decompress(blob) if bytes(blob[:4]) == ZSTD_MAGIC else bytes(blob)
        rows, _ = self.read_jsonl(data)
        return [r for r in rows if isinstance(r, dict)]
