"""OpenClaw personal-agent session transcripts.

Status: placeholder shell – registered so the UI can list it, but ``parse`` is
not written yet.

Sessions are stored as JSONL files in the agent's state directory (one file
per session).  Confirm the row schema against a real export, then map message
rows to :class:`~synthsift.models.Message` and tool invocations/results to
``tool_call`` / ``tool_result`` blocks.
"""

from __future__ import annotations

from ..models import Conversation
from .base import HarnessParser, ParserNotImplemented, register


@register
class OpenClawParser(HarnessParser):
    name = "openclaw"
    label = "OpenClaw"
    aliases = ("open-claw", "open_claw", "clawdbot", "moltbot")
    implemented = False
    description = "Placeholder – parser not implemented yet."

    def parse(self, raw: bytes, filename: str) -> list[Conversation]:
        raise ParserNotImplemented(f"The '{self.name}' harness parser is a placeholder; see {__name__}")
