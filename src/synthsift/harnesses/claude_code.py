"""Claude Code (Anthropic CLI) session transcripts.

Status: placeholder shell – registered so the UI can list it, but ``parse`` is
not written yet.

Where to find them: ``~/.claude/projects/<escaped-project-path>/<session-uuid>.jsonl``

Format notes for whoever implements this:
* JSONL, one event per line.  ``type`` is ``user`` / ``assistant`` for the
  conversation; other types (``attachment``, ``queue-operation``, ``ai-title``,
  ``last-prompt`` …) are bookkeeping and can be skipped or mined for metadata.
* ``message`` holds an Anthropic Messages API style object whose ``content`` is a
  string or a list of blocks: ``text``, ``thinking``, ``tool_use``
  (``id``/``name``/``input``) and ``tool_result`` (``tool_use_id``/``content``).
* ``isSidechain: true`` rows belong to sub-agents; ``parentUuid`` links rows.
* The ``example`` parser's ``_content_blocks`` already understands these block
  types, so most of the work is walking the JSONL rows.
"""

from __future__ import annotations

from ..models import Conversation
from .base import HarnessParser, ParserNotImplemented, register


@register
class ClaudeCodeParser(HarnessParser):
    name = "claude_code"
    label = "Claude Code"
    aliases = ("claude-code", "claudecode", "claude")
    implemented = False
    description = "Placeholder – parser not implemented yet."

    def parse(self, raw: bytes, filename: str) -> list[Conversation]:
        raise ParserNotImplemented(f"The '{self.name}' harness parser is a placeholder; see {__name__}")
