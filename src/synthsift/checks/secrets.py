"""Exposed secrets: credential-shaped strings (private-key headers, cloud keys, tokens, password assignments) in a
message, a tool argument or tool output – standard secret scanning. One check per kind of secret; the first match
in a turn is reported, shortened so the secret itself is never shown in full."""

from __future__ import annotations

import re
from typing import ClassVar, Iterable

from .base import Check, EventContext, Finding, register
from .sensitive import SCAN_OUTPUT

_WHERE = {"tool_result": "tool output", "user": "a user message", "tool_call": "a tool argument"}


class SecretPattern(Check):
    """Flags the first match of ``pattern`` in a turn's text. Subclass it with a ``pattern`` to add a kind of secret."""

    pattern: ClassVar[re.Pattern[str]]
    label = "Secret exposed"
    category = "data_exposure"
    options = (SCAN_OUTPUT,)

    def run(self, ctx: EventContext) -> Iterable[Finding]:
        if not ctx.text or (ctx.type == "tool_result" and not ctx.opt("sec_scan_results", True)):
            return
        m = self.pattern.search(ctx.text)
        if m:
            s = m.group(0)
            shown = s[:6] + "…" if len(s) > 8 else s
            yield self.finding(ctx, f"{self.name.replace('_', ' ')} in {_WHERE.get(ctx.type, 'the conversation')}: `{shown}`",
                               rule=self.rule or f"secret.{self.name}")


@register
class PrivateKey(SecretPattern):
    name = "private_key"
    description = "A PEM private-key header (RSA, EC, DSA, OpenSSH, PGP)."
    severity = "critical"
    order = 400
    pattern = re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----")


@register
class AwsAccessKey(SecretPattern):
    name = "aws_access_key"
    description = "An AWS access key id (AKIA… / ASIA…)."
    severity = "high"
    order = 410
    pattern = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")


@register
class GcpApiKey(SecretPattern):
    name = "gcp_api_key"
    description = "A Google API key (AIza…)."
    severity = "high"
    order = 420
    pattern = re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")


@register
class GithubToken(SecretPattern):
    name = "github_token"
    description = "A GitHub token (ghp_, gho_, ghu_, ghs_, ghr_)."
    severity = "high"
    order = 430
    pattern = re.compile(r"\bgh[pousr]_[0-9A-Za-z]{36,}\b")


@register
class SlackToken(SecretPattern):
    name = "slack_token"
    description = "A Slack token (xoxb-, xoxp- …)."
    severity = "high"
    order = 440
    pattern = re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b")


@register
class BearerToken(SecretPattern):
    name = "bearer_token"
    description = "An HTTP bearer token."
    severity = "medium"
    order = 450
    pattern = re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{20,}\b")


@register
class Jwt(SecretPattern):
    name = "jwt"
    description = "A JSON Web Token (three base64url parts)."
    severity = "medium"
    order = 460
    pattern = re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{6,}\b")


@register
class PasswordAssignment(SecretPattern):
    name = "password_assignment"
    description = "password= / secret: / api_key= / token= followed by a value."
    severity = "medium"
    order = 470
    pattern = re.compile(r"(?i)\b(?:password|passwd|pwd|secret|api[_-]?key|token|access[_-]?key)\b\s*[=:]\s*"
                         r"['\"]?[^\s'\"]{6,}['\"]?")
