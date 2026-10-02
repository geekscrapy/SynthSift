"""Data-flow checks: where data goes in one tool call – out of the host, onto its disk, into an interpreter, or to
another host's shell. Each finding carries a ``source → action → destination`` chain the graph draws as an edge.

The three directions are exclusive, most serious first: a turn that sends data out is not also reported as a
download, and remote execution is only reported when neither applies.
"""

from __future__ import annotations

from typing import Iterable, Iterator

from .base import Check, EventContext, Finding, register


def unique(findings: Iterable[Finding]) -> Iterator[Finding]:
    """Leave out repeats (the same entity named twice in a command)."""
    seen: set[tuple] = set()
    for f in findings:
        sig = (f.rule, tuple(sorted(f.entities)), tuple(f.chain))
        if sig not in seen:
            seen.add(sig)
            yield f


@register
class FetchAndRun(Check):
    name = "fetch_and_run"
    label = "Downloaded content executed"
    description = "Fetched content piped straight into an interpreter, e.g. curl … | sh."
    category = "execution"
    severity = "high"
    events = ("tool_call",)
    order = 300

    def run(self, ctx: EventContext) -> Iterable[Finding]:
        c = ctx.command
        if not c.fetch_and_run:
            return ()
        return unique(self.finding(ctx, f"`{c.base}` piped remote content from {r.text} into an interpreter",
                                   entities=[r.key], chain=[(r.key, "run", ctx.event.id)]) for r in c.remotes)


@register
class Exfiltration(Check):
    name = "exfiltration"
    label = "Data leaving the host"
    description = ("Local data (files read, secrets, or a bulk query / dump / archive) and a real network send in the "
                   "same command: an upload, an object-store copy, scp / rsync to a host, or data piped into a "
                   "network client. Critical when a secret is involved.")
    category = "egress"
    severity = "high"
    events = ("tool_call",)
    order = 310

    def run(self, ctx: EventContext) -> Iterable[Finding]:
        c = ctx.command
        if not (c.sends and c.remotes and (c.sources or c.bulk_read)):
            return ()
        sev = "critical" if c.secrets else self.severity
        if c.sources:
            return unique(self.finding(ctx, f"`{c.base}` moves {s.text} to {r.text}", severity=sev,
                                       entities=[s.key, r.key], chain=[(s.key, "sends to", r.key)])
                          for s in c.sources for r in c.remotes)
        # a bulk read with no file entity: the chain starts at the tool call itself
        return unique(self.finding(ctx, f"`{c.base}` sends bulk query/archive output to {r.text}", severity=sev,
                                   label="Query results leaving the host", entities=[r.key],
                                   chain=[(ctx.event.id, "sends to", r.key)]) for r in c.remotes)


@register
class Download(Check):
    name = "download"
    label = "File downloaded to disk"
    description = "A remote resource written to local disk (or cloned); without a file written, a low 'fetched' note."
    category = "ingress"
    severity = "medium"
    events = ("tool_call",)
    order = 320

    def run(self, ctx: EventContext) -> Iterable[Finding]:
        c = ctx.command
        if ctx.has("exfiltration") or not (c.downloads and c.remotes):
            return ()
        if c.local_writes:
            return unique(self.finding(ctx, f"`{c.base}` downloads {r.text} to {t.text}", entities=[r.key, t.key],
                                       chain=[(r.key, "downloads to", t.key)]) for r in c.remotes for t in c.local_writes)
        return unique(self.finding(ctx, f"`{c.base}` fetched {r.text}", label="Remote resource fetched", severity="low",
                                   entities=[r.key]) for r in c.remotes)


@register
class RemoteExec(Check):
    name = "remote_exec"
    label = "Remote command execution"
    description = "A command run on another host (ssh), when nothing was sent out or downloaded."
    category = "execution"
    severity = "low"
    events = ("tool_call",)
    order = 330

    def run(self, ctx: EventContext) -> Iterable[Finding]:
        c = ctx.command
        if ctx.has("exfiltration") or ctx.has("download") or not (c.remote_exec and c.remotes):
            return ()
        return unique(self.finding(ctx, f"`{c.base}` runs a command on {r.text}", entities=[r.key]) for r in c.remotes)
