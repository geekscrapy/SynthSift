"""Hermes agent (Nous Research) transcripts.

Status: placeholder shell – registered so the UI can list it, but ``parse`` is
not written yet.

Hermes-style models emit tool calls inline as ``<tool_call>{json}</tool_call>``
and results as ``<tool_response>…</tool_response>``, with reasoning in
``<think>…</think>``.  An implementation will likely need to split assistant
text on those tags into ``thinking`` / ``tool_call`` / ``tool_result`` blocks in
addition to reading the agent's session files.
"""

from __future__ import annotations

from ..models import Conversation
from .base import HarnessParser, ParserNotImplemented, register


@register
class HermesParser(HarnessParser):
    name = "hermes"
    label = "Hermes Agent"
    aliases = ("hermes-agent", "hermes_agent", "hermesagent")
    implemented = False
    description = "Placeholder – parser not implemented yet."

    def parse(self, raw: bytes, filename: str) -> list[Conversation]:
        raise ParserNotImplemented(f"The '{self.name}' harness parser is a placeholder; see {__name__}")
