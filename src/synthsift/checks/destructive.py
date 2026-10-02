"""Destructive and history / log-clearing commands: ordinary admin operations an analyst reviews for accidental or
intentional data loss. Each check is one command shape (a regular expression over the tool call's command line);
copy one to add another."""

from __future__ import annotations

import re
from typing import ClassVar, Iterable

from .base import Check, EventContext, Finding, register


class CommandPattern(Check):
    """Flags a tool call whose command line matches ``pattern``. Subclass it, set ``pattern`` and the usual
    attributes, and register it: that is a whole check."""

    pattern: ClassVar[re.Pattern[str]]
    category = "destruction"
    events = ("tool_call",)

    def run(self, ctx: EventContext) -> Iterable[Finding]:
        c = ctx.command
        if self.pattern.search(c.text):
            yield self.finding(ctx, f"`{c.base or 'command'}`: {self.label.lower()}", rule=self.rule or f"op.{self.name}",
                               entities=[m.key for m in c.locals])


@register
class RecursiveDelete(CommandPattern):
    name = "recursive_delete"
    label = "Recursive force delete"
    description = "rm -rf and its spellings."
    severity = "high"
    order = 100
    pattern = re.compile(r"\brm\s+(?:-\w*\s+)*-(?:\w*r\w*f|\w*f\w*r)\w*\b|\brm\s+-[rf]\w*\s+-[rf]\w*")


@register
class DiskOverwrite(CommandPattern):
    name = "disk_overwrite"
    label = "Disk / device overwrite"
    description = "dd onto a device, mkfs, or a redirect onto a disk."
    severity = "critical"
    order = 110
    pattern = re.compile(r"\bdd\b[^\n]*\bof=/dev/|\bmkfs\.\w+|\b>\s*/dev/sd[a-z]")


@register
class DropDatabase(CommandPattern):
    name = "drop_database"
    label = "Drop or truncate database"
    description = "DROP TABLE / DATABASE / SCHEMA, TRUNCATE TABLE."
    severity = "high"
    order = 120
    pattern = re.compile(r"\b(?:DROP\s+(?:TABLE|DATABASE|SCHEMA)|TRUNCATE\s+TABLE)\b", re.I)


@register
class UnscopedDelete(CommandPattern):
    name = "unscoped_delete"
    label = "Unscoped delete"
    description = "DELETE FROM a table with no WHERE clause."
    severity = "medium"
    order = 130
    pattern = re.compile(r"\bDELETE\s+FROM\s+\w+\s*(?:;|$)", re.I)


@register
class ObjectStoreDelete(CommandPattern):
    name = "object_store_delete"
    label = "Recursive object-store delete"
    description = "Recursive deletes in S3, Google Cloud Storage or Azure storage."
    severity = "high"
    order = 140
    pattern = re.compile(r"\b(?:aws\s+s3|gsutil|az\s+storage)\b[^\n]*\b(?:rm|rb|delete)\b[^\n]*(?:--recursive|-r\b|/\*)")


@register
class ForcePush(CommandPattern):
    name = "force_push"
    label = "Force history rewrite / push"
    description = "git push --force (or -f, or a +refspec)."
    severity = "medium"
    order = 150
    pattern = re.compile(r"\bgit\s+push\b[^\n]*(?:--force\b|-f\b|\+\w)")


@register
class WorldWritable(CommandPattern):
    name = "world_writable"
    label = "Recursive world-writable permissions"
    description = "chmod -R 777."
    severity = "medium"
    order = 160
    pattern = re.compile(r"\bchmod\s+-R\s+0?777\b")


@register
class HistoryCleared(CommandPattern):
    name = "history_cleared"
    label = "Shell history cleared"
    description = "history -c, unset HISTFILE, HISTSIZE=0, or deleting / truncating .bash_history."
    category = "evasion"
    severity = "high"
    order = 170
    pattern = re.compile(r"\bhistory\s+-c\b|\bunset\s+HISTFILE\b|(?:rm|truncate|>\s*)[^\n]*\.bash_history\b|"
                         r"\bexport\s+HISTSIZE=0\b")


@register
class LogCleared(CommandPattern):
    name = "log_cleared"
    label = "System log cleared"
    description = "Truncating files in /var/log, journalctl --vacuum, wevtutil cl."
    category = "evasion"
    severity = "high"
    order = 180
    pattern = re.compile(r"(?::>|>\s*|\btruncate\b[^\n]*)/var/log/\S+|\bjournalctl\b[^\n]*--vacuum|\bwevtutil\s+cl\b", re.I)
