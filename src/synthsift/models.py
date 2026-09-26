"""Normalized, harness-independent transcript model (pydantic).

Every harness parser converts its native format into these models. The rest of
SynthSift (NLP, graph building, UI) only ever sees this model, so adding a new
harness never touches anything downstream.

    Conversation
      └─ Message (role = user | assistant | system | tool)
           └─ Block (kind = text | thinking | tool_call | tool_result)
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["user", "assistant", "system", "tool"]
BlockKind = Literal["text", "thinking", "tool_call", "tool_result"]


class Block(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: BlockKind
    text: str = ""
    # tool_call: the call id, tool name and arguments
    # tool_result: the id of the call it answers (and the tool name if known)
    tool_call_id: str | None = None
    tool_name: str | None = None
    arguments: dict[str, Any] | None = None
    is_error: bool = False

    @classmethod
    def text_block(cls, text: str) -> "Block":
        return cls(kind="text", text=text)

    @classmethod
    def thinking(cls, text: str) -> "Block":
        return cls(kind="thinking", text=text)

    @classmethod
    def tool_call(cls, name: str, arguments: dict[str, Any] | None, call_id: str | None = None) -> "Block":
        return cls(kind="tool_call", tool_name=name, arguments=arguments or {}, tool_call_id=call_id)

    @classmethod
    def tool_result(
        cls, text: str, call_id: str | None = None, name: str | None = None, is_error: bool = False
    ) -> "Block":
        return cls(kind="tool_result", text=text, tool_call_id=call_id, tool_name=name, is_error=is_error)


class Message(BaseModel):
    model_config = ConfigDict(extra="ignore")

    role: Role
    blocks: list[Block] = Field(default_factory=list)
    timestamp: str | None = None
    model: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class Conversation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    messages: list[Message] = Field(default_factory=list)
    title: str | None = None
    started_at: str | None = None
    model: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)

    # Provenance, filled in by the ingester from the zip path
    # host/user/harness/<file>. Parsers normally leave these alone.
    id: str = ""
    host: str = "unknown-host"
    user: str = "unknown-user"
    harness: str = "unknown"
    session: str = ""  # the transcript file name (one file = one session)
    source_path: str = ""
    index_in_session: int = 0

    @property
    def display_title(self) -> str:
        if self.title:
            return self.title
        for msg in self.messages:
            if msg.role == "user":
                for b in msg.blocks:
                    if b.kind == "text" and b.text.strip():
                        first = b.text.strip().splitlines()[0]
                        return first[:80] + ("…" if len(first) > 80 else "")
        return self.session or self.id
