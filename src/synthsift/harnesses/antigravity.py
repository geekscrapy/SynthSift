"""Google Antigravity (agentic IDE) transcripts.

Status: placeholder shell – registered so the UI can list it, but ``parse`` is
not written yet.

Google does not document where the Antigravity IDE stores conversations, so
there is no confirmed on-disk location yet.  The CLI keeps its configuration in
``~/.gemini/antigravity-cli/``.  The documented capture is headless mode::

    agy -p "…" --output-format stream-json > run.jsonl

which prints NDJSON events: one ``init``, any number of ``step_update`` (text
deltas, tool calls, token usage) and one ``result``.  That stream is the most
reliable input to implement against: map text deltas to ``assistant``
messages, reasoning to ``thinking`` blocks and tool / browser actions to
``tool_call`` + ``tool_result`` blocks.
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
