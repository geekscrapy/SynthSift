r"""Gemini CLI session transcripts.

Status: placeholder shell – registered so the UI can list it, but ``parse`` is
not written yet.

Where to find them (documented by Gemini CLI)::

    ~/.gemini/tmp/<project_hash>/chats/session-<time>-<id>.jsonl   (.json in older releases)
    ~/.gemini/tmp/<project_hash>/                                  manual `/resume save <tag>` checkpoints

``$GEMINI_CLI_HOME`` moves the ``.gemini`` folder; on Windows it is
``C:\Users\<you>\.gemini``.  Sessions are deleted after 30 days by default
(``general.sessionRetention``).  The recorded session keeps prompts, responses,
tool calls with inputs and outputs, token usage and thoughts.

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
