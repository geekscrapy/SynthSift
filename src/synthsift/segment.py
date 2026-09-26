"""Flatten normalized conversations into *events* and *paragraphs*.

An event is one node of the conversation flow chart (a user turn, an LLM reply,
a thought, a tool call, a tool result).  Each event owns one or more paragraphs:
the unit the transcript panel displays, the NLP pipeline analyses and search
highlights refer to (by character offsets inside the paragraph text).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .models import Conversation

_FENCE = re.compile(r"^```[^\n]*\n.*?^```[ \t]*$", re.S | re.M)


@dataclass
class Paragraph:
    id: str
    conv: str
    event: str
    seq: int
    text: str
    role: str  # user | assistant | system | thought | tool_call | tool_result
    code: bool = False
    arg: str | None = None  # tool_call paragraphs: the argument name

    def to_json(self) -> dict[str, Any]:
        d = {"id": self.id, "c": self.conv, "e": self.event, "t": self.text, "r": self.role}
        if self.code:
            d["code"] = 1
        if self.arg is not None:
            d["arg"] = self.arg
        return d


@dataclass
class Event:
    id: str
    conv: str
    seq: int
    type: str
    label: str
    paragraphs: list[str] = field(default_factory=list)
    turn: int = 0
    timestamp: str | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    arguments: dict[str, Any] | None = None
    is_error: bool = False
    # tool_call: argument name -> paragraph id
    arg_paragraphs: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id, "c": self.conv, "seq": self.seq, "type": self.type, "label": self.label,
            "p": self.paragraphs, "turn": self.turn,
        }
        if self.timestamp:
            d["ts"] = self.timestamp
        if self.tool_name:
            d["tool"] = self.tool_name
        if self.tool_call_id:
            d["call_id"] = self.tool_call_id
        if self.is_error:
            d["error"] = 1
        return d


def _chunk_lines(text: str, max_lines: int, max_chars: int = 4000) -> list[str]:
    lines = text.split("\n")
    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    for line in lines:
        if buf and (len(buf) >= max_lines or size + len(line) > max_chars):
            chunks.append("\n".join(buf))
            buf, size = [], 0
        while len(line) > max_chars:  # a single giant line (minified JSON, base64…)
            cut = line.rfind(" ", 0, max_chars)
            cut = cut if cut > max_chars // 2 else max_chars
            chunks.append(line[:cut])
            line = line[cut:].lstrip()
        buf.append(line)
        size += len(line) + 1
    if buf:
        chunks.append("\n".join(buf))
    return chunks


def split_paragraphs(text: str, mode: str = "blank_line", max_lines: int = 40) -> list[tuple[str, bool]]:
    """Split text into (paragraph, is_code) pairs, keeping fenced code intact."""
    text = text.replace("\r\n", "\n").strip("\n")
    out: list[tuple[str, bool]] = []
    pos = 0
    pieces: list[tuple[str, bool]] = []
    for m in _FENCE.finditer(text):
        pieces.append((text[pos:m.start()], False))
        pieces.append((m.group(0), True))
        pos = m.end()
    pieces.append((text[pos:], False))
    for piece, is_code in pieces:
        if not piece.strip():
            continue
        if is_code:
            out.extend((c, True) for c in _chunk_lines(piece, max_lines * 2))
            continue
        parts = re.split(r"\n\s*\n", piece) if mode == "blank_line" else piece.split("\n")
        for part in parts:
            part = part.strip("\n")
            if part.strip():
                out.extend((c, False) for c in _chunk_lines(part, max_lines) if c.strip())
    return out


def _arg_text(key: str, value: Any) -> str:
    if isinstance(value, str):
        return f"{key}: {value}"
    return f"{key}: {json.dumps(value, ensure_ascii=False, indent=2)}"


_ROLE_LABEL = {"user": "User", "assistant": "LLM", "system": "System"}


def segment(conv: Conversation, cfg: dict[str, Any]) -> tuple[list[Event], list[Paragraph]]:
    events: list[Event] = []
    paragraphs: list[Paragraph] = []
    counters: dict[str, int] = {}
    calls_by_id: dict[str, Event] = {}
    split_mode = cfg.get("paragraph_split", "blank_line")
    max_lines = int(cfg.get("max_paragraph_lines", 40))
    max_result = int(cfg.get("max_tool_result_chars", 20000))

    def new_event(etype: str, label: str, turn: int, ts: str | None) -> Event:
        ev = Event(id=f"{conv.id}:e{len(events)}", conv=conv.id, seq=len(events), type=etype,
                   label=label, turn=turn, timestamp=ts)
        events.append(ev)
        return ev

    def add_para(ev: Event, text: str, code: bool = False, arg: str | None = None) -> Paragraph:
        p = Paragraph(id=f"{conv.id}:p{len(paragraphs)}", conv=conv.id, event=ev.id, seq=len(paragraphs),
                      text=text, role=ev.type, code=code, arg=arg)
        paragraphs.append(p)
        ev.paragraphs.append(p.id)
        return p

    def count(kind: str) -> int:
        counters[kind] = counters.get(kind, 0) + 1
        return counters[kind]

    for turn, msg in enumerate(conv.messages):
        text_event: Event | None = None
        for block in msg.blocks:
            if block.kind == "text":
                role = msg.role if msg.role in _ROLE_LABEL else "assistant"
                if text_event is None:
                    text_event = new_event(role, f"{_ROLE_LABEL[role]} {count(role)}", turn, msg.timestamp)
                for para, code in split_paragraphs(block.text, split_mode, max_lines):
                    add_para(text_event, para, code)
                if not text_event.paragraphs:
                    events.remove(text_event)
                    counters[role] -= 1
                    text_event = None
            elif block.kind == "thinking":
                if not block.text.strip():
                    continue
                ev = new_event("thought", f"Thought {count('thought')}", turn, msg.timestamp)
                for para, code in split_paragraphs(block.text, split_mode, max_lines):
                    add_para(ev, para, code)
                text_event = None
            elif block.kind == "tool_call":
                n = count("tool_call")
                name = block.tool_name or "tool"
                ev = new_event("tool_call", f"{name} #{n}", turn, msg.timestamp)
                ev.tool_name, ev.tool_call_id, ev.arguments = name, block.tool_call_id, block.arguments or {}
                if ev.arguments:
                    for key, value in ev.arguments.items():
                        p = add_para(ev, _arg_text(str(key), value), code=not isinstance(value, str), arg=str(key))
                        ev.arg_paragraphs[str(key)] = p.id
                else:
                    add_para(ev, f"{name}()", code=True)
                if block.tool_call_id:
                    calls_by_id[block.tool_call_id] = ev
                text_event = None
            elif block.kind == "tool_result":
                call = calls_by_id.get(block.tool_call_id or "")
                name = block.tool_name or (call.tool_name if call else None) or "tool"
                n = int(call.label.rsplit("#", 1)[1]) if call and "#" in call.label else count("tool_result_orphan")
                ev = new_event("tool_result", f"{name} result #{n}", turn, msg.timestamp)
                ev.tool_name, ev.tool_call_id, ev.is_error = name, block.tool_call_id, block.is_error
                body = block.text or ""
                if len(body) > max_result:
                    body = body[:max_result] + f"\n… [truncated {len(block.text) - max_result:,} characters]"
                parts = split_paragraphs(body, split_mode, max_lines) or [("(empty result)", False)]
                for para, code in parts:
                    add_para(ev, para, code)
                text_event = None
    return events, paragraphs
