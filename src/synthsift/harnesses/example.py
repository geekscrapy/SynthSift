"""Reference parser: the SynthSift example transcript format.

This is the fully worked example harness; copy it when adding a new one.  It is
deliberately lenient so it also reads plain OpenAI chat-completions logs and
Anthropic Messages API logs.

Canonical layout (``"format": "synthsift.example/v1"``)::

    {
      "format": "synthsift.example/v1",
      "session_id": "abc123",
      "title": "Optional title",
      "model": "some-model",
      "started_at": "2026-01-01T10:00:00Z",
      "messages": [
        {"role": "system", "content": "You are a helpful agent."},
        {"role": "user", "content": "Read /etc/hosts please", "timestamp": "..."},
        {"role": "assistant", "content": [
            {"type": "thinking", "text": "I should use the read_file tool."},
            {"type": "text", "text": "Sure, reading it now."},
            {"type": "tool_call", "id": "call_1", "name": "read_file",
             "arguments": {"path": "/etc/hosts"}}
        ]},
        {"role": "tool", "tool_call_id": "call_1", "name": "read_file",
         "content": "127.0.0.1 localhost"},
        {"role": "assistant", "content": "The file maps localhost to 127.0.0.1."}
      ]
    }

Also accepted:

* ``{"conversations": [<conversation>, ...]}`` – several conversations per file
* a bare JSON list of messages, or JSONL with one message per line
* OpenAI style ``tool_calls`` (``function.arguments`` as a JSON string) and
  ``reasoning_content``; Anthropic style ``tool_use`` / ``tool_result`` blocks
"""

from __future__ import annotations

import json
from typing import Any

from ..models import Block, Conversation, Message
from .base import HarnessParser, register

FORMAT_ID = "synthsift.example/v1"

_ROLE_MAP = {
    "user": "user",
    "human": "user",
    "assistant": "assistant",
    "ai": "assistant",
    "model": "assistant",
    "bot": "assistant",
    "system": "system",
    "developer": "system",
    "tool": "tool",
    "function": "tool",
}


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):  # list of content parts
        parts = []
        for p in value:
            if isinstance(p, dict):
                parts.append(str(p.get("text") or p.get("content") or p.get("output") or ""))
            else:
                parts.append(str(p))
        return "\n\n".join(x for x in parts if x)
    if isinstance(value, dict):
        return str(value.get("text") or value.get("content") or json.dumps(value, ensure_ascii=False))
    return str(value)


def _as_args(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {"input": parsed}
        except json.JSONDecodeError:
            return {"input": value}
    return {"input": value}


def _content_blocks(content: Any) -> list[Block]:
    if content is None:
        return []
    if isinstance(content, str):
        return [Block.text_block(content)] if content.strip() else []
    if isinstance(content, dict):
        content = [content]
    blocks: list[Block] = []
    for part in content:
        if isinstance(part, str):
            blocks.append(Block.text_block(part))
            continue
        if not isinstance(part, dict):
            continue
        typ = str(part.get("type", "text")).lower()
        if typ in ("text", "output_text", "input_text"):
            blocks.append(Block.text_block(_as_text(part.get("text"))))
        elif typ in ("thinking", "reasoning", "thought"):
            blocks.append(Block.thinking(_as_text(part.get("text") or part.get("thinking") or part.get("content"))))
        elif typ in ("tool_call", "tool_use", "function_call"):
            blocks.append(
                Block.tool_call(
                    str(part.get("name") or part.get("function", {}).get("name") or "tool"),
                    _as_args(part.get("arguments", part.get("input", part.get("function", {}).get("arguments")))),
                    part.get("id") or part.get("call_id"),
                )
            )
        elif typ in ("tool_result", "function_result", "function_call_output"):
            blocks.append(
                Block.tool_result(
                    _as_text(part.get("content", part.get("output", part.get("text")))),
                    part.get("tool_call_id") or part.get("tool_use_id") or part.get("call_id"),
                    part.get("name"),
                    bool(part.get("is_error", False)),
                )
            )
        # images, audio etc. are ignored – nothing to analyse without an LLM
    return [b for b in blocks if b.kind != "text" or b.text.strip()]


def parse_message(raw: dict[str, Any]) -> Message | None:
    role = _ROLE_MAP.get(str(raw.get("role", "")).lower())
    if role is None:
        return None
    blocks: list[Block] = []

    reasoning = raw.get("reasoning_content") or raw.get("reasoning") or raw.get("thinking")
    if isinstance(reasoning, str) and reasoning.strip():
        blocks.append(Block.thinking(reasoning))

    if role == "tool":
        blocks.append(
            Block.tool_result(
                _as_text(raw.get("content")),
                raw.get("tool_call_id"),
                raw.get("name"),
                bool(raw.get("is_error", False)),
            )
        )
    else:
        blocks.extend(_content_blocks(raw.get("content")))

    for tc in raw.get("tool_calls") or []:
        fn = tc.get("function") or {}
        blocks.append(
            Block.tool_call(
                str(fn.get("name") or tc.get("name") or "tool"),
                _as_args(fn.get("arguments", tc.get("arguments"))),
                tc.get("id"),
            )
        )

    # A user message that only carries tool results is really a tool message
    if role == "user" and blocks and all(b.kind == "tool_result" for b in blocks):
        role = "tool"

    return Message(
        role=role,  # type: ignore[arg-type]
        blocks=blocks,
        timestamp=raw.get("timestamp") or raw.get("created_at"),
        model=raw.get("model"),
    )


def parse_conversation(obj: dict[str, Any] | list[Any]) -> Conversation:
    if isinstance(obj, list):
        obj = {"messages": obj}
    messages = [m for m in (parse_message(r) for r in obj.get("messages", []) if isinstance(r, dict)) if m]
    return Conversation(
        messages=messages,
        title=obj.get("title"),
        started_at=obj.get("started_at") or obj.get("created_at"),
        model=obj.get("model"),
        meta={k: v for k, v in obj.items() if k not in ("messages", "conversations") and not isinstance(v, (list, dict))},
    )


@register
class ExampleParser(HarnessParser):
    name = "example"
    label = "Example / generic chat JSON"
    aliases = ("generic", "openai", "chat", "synthsift")
    subagent_tools = ("Task", "Agent", "spawn_agent", "sessions_spawn")
    description = "SynthSift reference format; also reads OpenAI chat and Anthropic Messages style logs."

    def parse(self, raw: bytes, filename: str) -> list[Conversation]:
        if filename.lower().endswith(".jsonl"):
            rows = self.load_jsonl(raw)
            if rows and all(isinstance(r, dict) and "messages" in r for r in rows):
                return [parse_conversation(r) for r in rows]
            return [parse_conversation(rows)]
        data = self.load_json(raw)
        if isinstance(data, dict) and isinstance(data.get("conversations"), list):
            convs = []
            for c in data["conversations"]:
                conv = parse_conversation(c)
                conv.model = conv.model or data.get("model")
                convs.append(conv)
            return convs
        return [parse_conversation(data)]

    def sniff(self, raw: bytes, filename: str) -> float:
        head = raw[:4096].decode("utf-8", errors="ignore")
        if FORMAT_ID in head:
            return 1.0
        if '"messages"' in head and '"role"' in head:
            return 0.6
        if head.lstrip().startswith("[") and '"role"' in head:
            return 0.55
        return 0.0
