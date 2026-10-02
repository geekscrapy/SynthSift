"""Sensitive files: well-known credential locations (``/etc/shadow``, ``~/.ssh/id_rsa``, ``.aws/credentials``,
``.env`` …) touched by a command, or named anywhere else in the conversation. The locations are
``base.SENSITIVE_PATHS``."""

from __future__ import annotations

from typing import Iterable

from ..fields import Field
from .base import Check, EventContext, Finding, register

#: shared with the secret checks: whether tool / command output is scanned too
SCAN_OUTPUT = Field("sec_scan_results", "Scan tool output", "bool", True, "parse", "",
                    "Also look for secrets and sensitive files in command / tool output, not just prompts and arguments.")


@register
class SensitiveFileAccess(Check):
    name = "sensitive_file_access"
    rule = "sensitive_path"
    label = "Sensitive file accessed"
    description = "A command reads, copies or sends a credential file."
    category = "credential_access"
    severity = "high"
    events = ("tool_call",)
    order = 200

    def run(self, ctx: EventContext) -> Iterable[Finding]:
        c = ctx.command
        for m in c.locals:
            if m.sensitive:
                yield self.finding(ctx, f"`{c.base or 'command'}` touched {m.text}", entities=[m.key])


@register
class SensitiveFileReference(Check):
    name = "sensitive_file_reference"
    rule = "sensitive_path"
    label = "Sensitive file referenced"
    description = "A credential file named in a message, a thought or tool output."
    category = "credential_access"
    severity = "medium"
    events = ("user", "assistant", "system", "thought", "tool_result")
    order = 480
    options = (SCAN_OUTPUT,)

    def run(self, ctx: EventContext) -> Iterable[Finding]:
        if ctx.type == "tool_result" and not ctx.opt("sec_scan_results", True):
            return
        for m in ctx.mentions:
            if m.local and m.sensitive:
                yield self.finding(ctx, f"reference to {m.text}", entities=[m.key])
