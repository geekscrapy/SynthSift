"""Gemini CLI session transcripts.

Status: placeholder shell – registered so the UI can list it, but ``parse`` is
not written yet.

Where to look: Gemini CLI keeps per-project state under ``~/.gemini/tmp/<hash>/``
(saved chats / checkpoints).  Confirm the exact file layout against a real
export before implementing – it has changed between releases.

Expected mapping: user turns -> ``user``; model turns -> ``assistant`` with
``thoughts`` -> ``thinking`` blocks and ``functionCall`` / ``toolCalls`` ->
``tool_call`` blocks; ``functionResponse`` -> ``tool_result``.
"""

from __future__ import annotations

from ..models import Conversation
from .base import HarnessParser, ParserNotImplemented, register


@register
class GeminiCliParser(HarnessParser):
    name = "gemini"
    label = "Gemini CLI"
    aliases = ("gemini-cli", "gemini_cli", "geminicli")
    implemented = False
    description = "Placeholder – parser not implemented yet."

    def parse(self, raw: bytes, filename: str) -> list[Conversation]:
        raise ParserNotImplemented(f"The '{self.name}' harness parser is a placeholder; see {__name__}")
