"""Google Antigravity (agentic IDE) transcripts.

Status: placeholder shell – registered so the UI can list it, but ``parse`` is
not written yet.

The on-disk format is not documented here yet.  Drop a sample export into the
``antigravity`` folder of an upload zip, inspect it, then map agent steps to
``assistant`` messages, plans/reasoning to ``thinking`` blocks and tool / browser
actions to ``tool_call`` + ``tool_result`` blocks.
"""

from __future__ import annotations

from ..models import Conversation
from .base import HarnessParser, ParserNotImplemented, register


@register
class AntigravityParser(HarnessParser):
    name = "antigravity"
    label = "Google Antigravity"
    aliases = ("google-antigravity", "google_antigravity")
    implemented = False
    description = "Placeholder – parser not implemented yet."

    def parse(self, raw: bytes, filename: str) -> list[Conversation]:
        raise ParserNotImplemented(f"The '{self.name}' harness parser is a placeholder; see {__name__}")
