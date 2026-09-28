"""Claude Code (Anthropic's agentic coding CLI) session transcripts.

Where to find them::

    ~/.claude/projects/<escaped-project-path>/<session-uuid>.jsonl
    ~/.claude/projects/<escaped-project-path>/<session-uuid>/subagents/agent-<id>.jsonl

(``$CLAUDE_CONFIG_DIR`` moves ``~/.claude``.)  ``collector/synthsift_collect.py``
packs them into an upload zip (glob list: ``collector/globs/claude_code.txt``).

Format (JSONL, one row per line, append-only):

* ``type: "user" | "assistant"`` rows carry ``message``, an Anthropic Messages
  API object.  ``content`` is a string or a list of ``text`` / ``thinking`` /
  ``tool_use`` / ``tool_result`` / ``image`` blocks.
* One API response is streamed as several ``assistant`` rows, one content block
  each, sharing ``message.id``; they are merged back into one message here.
* ``tool_result`` blocks arrive in ``user`` rows and answer a ``tool_use`` by id.
* ``isSidechain: true`` rows belong to sub-agents (Task / Agent tool).  Older
  versions interleave them in the session file; newer ones write one file per
  sub-agent under ``<session>/subagents/``.  Either way each sub-agent becomes
  its own conversation.
* Rows flagged ``isMeta`` / ``isCompactSummary`` and ``<system-reminder>``
  blocks are harness-generated, so they become ``system`` messages instead of
  user text.  Slash commands (``<command-name>``) keep the user's command,
  ``<local-command-stdout>`` is its output, and ``!`` bash-mode input
  (``<bash-input>`` / ``<bash-stdout>``) becomes a ``user_shell`` tool call so
  commands the *user* ran are analysed like the agent's.
* ``system`` rows (compaction boundaries, notices) become system messages;
  ``attachment`` rows are mostly harness context and are skipped, except
  prompts the user queued while the agent was busy.
* Bookkeeping rows (``ai-title``, ``custom-title``, ``summary``,
  ``last-prompt``, ``mode``, ``cost-state``, ``queue-operation``, …) only feed
  the conversation's title and metadata.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

from ..models import Block, Conversation, Message
from .base import HarnessParser, register

_REMINDER = re.compile(r"<system-reminder>\s*(.*?)\s*</system-reminder>", re.S)
_TAG = lambda name: re.compile(rf"<{name}>(.*?)</{name}>", re.S)  # noqa: E731
_CMD_NAME, _CMD_ARGS, _CMD_MSG = _TAG("command-name"), _TAG("command-args"), _TAG("command-message")
_STDOUT, _STDERR = _TAG("local-command-stdout"), _TAG("local-command-stderr")
_CAVEAT = _TAG("local-command-caveat")
_BASH_IN, _BASH_OUT, _BASH_ERR = _TAG("bash-input"), _TAG("bash-stdout"), _TAG("bash-stderr")
_CONV_ROWS = {"user", "assistant", "system", "attachment"}
_SYNTHETIC_MODEL = "<synthetic>"


def _tool_result_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = []
    for part in content if isinstance(content, list) else [content]:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict):
            typ = part.get("type")
            if typ == "text":
                parts.append(str(part.get("text", "")))
            elif typ == "image":
                parts.append("[image]")
            elif typ == "tool_reference":
                parts.append(f"[tool reference: {part.get('tool_name') or part.get('name') or '?'}]")
            elif "text" in part:
                parts.append(str(part["text"]))
    return "\n\n".join(p for p in parts if p)


def _split_reminders(text: str) -> tuple[str, list[str]]:
    """Separate harness ``<system-reminder>`` blocks from the real text."""
    reminders = [m.group(1) for m in _REMINDER.finditer(text)]
    return (_REMINDER.sub("", text).strip() if reminders else text), reminders


class _Thread:
    """Messages of one conversation (the main session or one sub-agent)."""

    def __init__(self) -> None:
        self.messages: list[Message] = []
        self.models: list[str] = []
        self.first_ts: str | None = None
        self._api_id: str | None = None  # message.id of the assistant message being streamed
        self.pending_shell: str | None = None  # call id of the last "!" command, for its output

    def add(self, role: str, blocks: list[Block], ts: str | None, model: str | None = None, **meta: Any) -> Message:
        msg = Message(role=role, blocks=blocks, timestamp=ts, model=model, meta={k: v for k, v in meta.items() if v})
        self.messages.append(msg)
        self.first_ts = self.first_ts or ts
        self._api_id = None
        return msg

    def system(self, text: str, ts: str | None, **meta: Any) -> None:
        text = text.strip()
        if text:
            self.add("system", [Block.text_block(text)], ts, **meta)

    def assistant(self, api_id: str | None, blocks: list[Block], ts: str | None, model: str | None) -> None:
        if model:
            self.models.append(model)
        last = self.messages[-1] if self.messages else None
        if api_id and api_id == self._api_id and last is not None and last.role == "assistant":
            last.blocks.extend(blocks)  # next streamed block of the same response
            return
        self.add("assistant", blocks, ts, model)
        self._api_id = api_id


@register
class ClaudeCodeParser(HarnessParser):
    name = "claude_code"
    label = "Claude Code"
    aliases = ("claude-code", "claudecode", "claude", ".claude")
    subagent_tools = ("Task", "Agent")
    description = "Claude Code CLI sessions: ~/.claude/projects/<project>/<session>.jsonl (+ subagents/)."

    # ------------------------------------------------------------------ API
    def sniff(self, raw: bytes, filename: str) -> float:
        head = raw[:65536].decode("utf-8", errors="ignore")
        score = 0.0
        if '"sessionId"' in head and '"parentUuid"' in head:
            score = 0.9
        elif '"sessionId"' in head and ('"isSidechain"' in head or '"userType"' in head):
            score = 0.8
        if score and ('"type":"assistant"' in head or '"type":"user"' in head or '"type": "user"' in head):
            score = 0.95
        return score

    def parse(self, raw: bytes, filename: str) -> list[Conversation]:
        raw, filename = self.decompress(raw, filename)
        text = raw.decode("utf-8-sig", errors="replace").lstrip()
        if text.startswith("[") or (text.startswith("{") and "\n{" not in text[:200000] and text.rstrip().endswith("}")):
            # a JSON array of rows (some exporters) – or a plain JSON file that is not a transcript
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = None
            rows = data if isinstance(data, list) else ([data] if isinstance(data, dict) and "type" in data else [])
            bad = 0
        else:
            rows, bad = self.read_jsonl(raw)
        if bad:
            self.warnings.append(f"skipped {bad} unreadable line(s)")
        return self.parse_rows([r for r in rows if isinstance(r, dict)], filename)

    # ---------------------------------------------------------------- rows
    def parse_rows(self, rows: list[dict[str, Any]], filename: str = "") -> list[Conversation]:
        threads: dict[str, _Thread] = {}
        order: list[str] = []
        tool_names: dict[str, str] = {}
        seen: set[str] = set()
        meta: dict[str, Any] = {}
        titles: dict[str, str] = {}
        is_subagent_file = "agent-" in filename.lower() or all(r.get("isSidechain") for r in rows if r.get("type") in ("user", "assistant"))

        def thread_for(row: dict[str, Any]) -> _Thread:
            if row.get("isSidechain") and not is_subagent_file:
                key = "agent:" + str(row.get("agentId") or "sidechain")
            else:
                key = "main"
            if key not in threads:
                threads[key] = _Thread()
                order.append(key)
            return threads[key]

        for row in rows:
            typ = row.get("type")
            # ------------------------------------------------ bookkeeping rows
            if typ == "summary" and row.get("summary"):
                titles.setdefault("summary", str(row["summary"]))
                continue
            if typ == "ai-title" and row.get("aiTitle"):
                titles["ai"] = str(row["aiTitle"])
                continue
            if typ == "custom-title" and (row.get("customTitle") or row.get("title")):
                titles["custom"] = str(row.get("customTitle") or row.get("title"))
                continue
            if typ not in _CONV_ROWS:
                continue
            uid = row.get("uuid")
            if uid:
                if uid in seen:
                    continue  # rows repeated after a resume / fork
                seen.add(uid)
            for src, dst in (("sessionId", "session_id"), ("cwd", "cwd"), ("gitBranch", "git_branch"),
                             ("version", "claude_code_version"), ("entrypoint", "entrypoint"), ("userType", "user_type"),
                             ("slug", "slug")):
                if row.get(src) and dst not in meta:
                    meta[dst] = row[src]
            if row.get("permissionMode"):
                meta["permission_mode"] = row["permissionMode"]
            t = thread_for(row)
            ts = row.get("timestamp")
            if typ == "user":
                self._user_row(t, row, ts, tool_names)
            elif typ == "assistant":
                self._assistant_row(t, row, ts, tool_names)
            elif typ == "system":
                self._system_row(t, row, ts)
            else:
                self._attachment_row(t, row, ts)

        convs: list[Conversation] = []
        main_title = titles.get("custom") or titles.get("ai") or titles.get("summary")
        for key in order:
            t = threads[key]
            if not any(m.role in ("user", "assistant", "tool") for m in t.messages):
                continue
            sub = key != "main" or is_subagent_file
            conv = Conversation(messages=t.messages, started_at=t.first_ts,
                                model=Counter(t.models).most_common(1)[0][0] if t.models else None,
                                meta={k: v for k, v in meta.items() if not (sub and k == "slug")})
            if sub:
                conv.meta["subagent"] = True
                if conv.meta.get("session_id"):
                    conv.meta["parent_session_id"] = conv.meta.pop("session_id")
                agent_id = key.split(":", 1)[1] if key.startswith("agent:") else next(
                    (r.get("agentId") for r in rows if r.get("agentId")), None)
                if agent_id and agent_id != "sidechain":
                    conv.meta["agent_id"] = agent_id
                prompt = conv.display_title
                conv.title = f"Sub-agent: {prompt}" if prompt else "Sub-agent"
            else:
                conv.title = main_title
            if conv.meta.get("cwd"):
                conv.meta["project"] = str(conv.meta["cwd"]).rstrip("/").rsplit("/", 1)[-1]
            convs.append(conv)
        return convs

    # ------------------------------------------------------------ row kinds
    def _user_row(self, t: _Thread, row: dict[str, Any], ts: str | None, tool_names: dict[str, str]) -> None:
        msg = row.get("message") or {}
        content = msg.get("content")
        if row.get("isCompactSummary"):
            t.system(_tool_result_text(content), ts, kind="compact_summary")
            return
        if row.get("isMeta"):
            text = _tool_result_text(content)
            text, reminders = _split_reminders(text)
            text = _CAVEAT.sub(lambda m: m.group(1), text)
            t.system("\n\n".join([text, *reminders]), ts, kind="meta")
            return
        if isinstance(content, str):
            self._user_text(t, content, ts, row)
            return
        blocks: list[Block] = []
        results: list[Block] = []
        reminders: list[str] = []
        for part in content or []:
            if isinstance(part, str):
                part = {"type": "text", "text": part}
            if not isinstance(part, dict):
                continue
            ptype = part.get("type")
            if ptype == "tool_result":
                body, rem = _split_reminders(_tool_result_text(part.get("content")))
                reminders += rem
                call_id = part.get("tool_use_id")
                results.append(Block.tool_result(body, call_id, tool_names.get(call_id or ""), bool(part.get("is_error"))))
            elif ptype == "text":
                body, rem = _split_reminders(str(part.get("text", "")))
                reminders += rem
                if body:
                    blocks.append(Block.text_block(body))
            elif ptype == "image":
                blocks.append(Block.text_block("[image attached]"))
        if results:
            t.add("tool", results, ts)
        if blocks:
            t.add("user", blocks, ts, prompt_id=row.get("promptId"))
        if reminders:
            t.system("\n\n".join(reminders), ts, kind="system_reminder")

    def _user_text(self, t: _Thread, text: str, ts: str | None, row: dict[str, Any]) -> None:
        text, reminders = _split_reminders(text)
        name = _CMD_NAME.search(text)
        if name:  # slash command, e.g. /model opus
            args = _CMD_ARGS.search(text)
            cmd = name.group(1).strip()
            cmd = cmd if cmd.startswith("/") else "/" + cmd
            arg_text = args.group(1).strip() if args else ""
            t.add("user", [Block.text_block(f"{cmd} {arg_text}".strip())], ts, kind="command", command=cmd)
        elif _STDOUT.search(text) or _STDERR.search(text):
            out = "\n".join(m.group(1).strip() for rx in (_STDOUT, _STDERR) for m in rx.finditer(text))
            t.system(out, ts, kind="command_output")
        elif _BASH_IN.search(text):  # "!" bash mode: the user ran a shell command directly
            cmd = _BASH_IN.search(text).group(1).strip()
            call_id = f"user-shell-{row.get('uuid') or len(t.messages)}"
            t.add("user", [Block.tool_call("user_shell", {"command": cmd}, call_id)], ts, kind="user_shell")
            t.pending_shell = call_id
        elif _BASH_OUT.search(text) or _BASH_ERR.search(text):
            out, err = _BASH_OUT.search(text), _BASH_ERR.search(text)
            body = "\n".join(x for x in ((out.group(1).strip() if out else ""), (err.group(1).strip() if err else "")) if x)
            call_id = t.pending_shell
            t.add("tool", [Block.tool_result(body, call_id, "user_shell", bool(err and err.group(1).strip() and not (out and out.group(1).strip())))], ts)
        elif text:
            t.add("user", [Block.text_block(text)], ts, prompt_id=row.get("promptId"))
        if reminders:
            t.system("\n\n".join(reminders), ts, kind="system_reminder")

    def _assistant_row(self, t: _Thread, row: dict[str, Any], ts: str | None, tool_names: dict[str, str]) -> None:
        msg = row.get("message") or {}
        model = msg.get("model")
        content = msg.get("content")
        if row.get("isApiErrorMessage") or model == _SYNTHETIC_MODEL:
            t.system(_tool_result_text(content), ts, kind="api_error" if row.get("isApiErrorMessage") else "synthetic")
            return
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        blocks: list[Block] = []
        for part in content or []:
            if not isinstance(part, dict):
                continue
            ptype = part.get("type")
            if ptype == "text" and str(part.get("text", "")).strip():
                blocks.append(Block.text_block(str(part["text"])))
            elif ptype == "thinking" and str(part.get("thinking", "")).strip():
                blocks.append(Block.thinking(str(part["thinking"])))
            elif ptype in ("tool_use", "server_tool_use"):
                name = str(part.get("name") or "tool")
                call_id = part.get("id")
                if call_id:
                    tool_names[call_id] = name
                args = part.get("input")
                blocks.append(Block.tool_call(name, args if isinstance(args, dict) else {"input": args}, call_id))
            elif ptype and ptype.endswith("_tool_result"):  # server-side tools, e.g. web search
                blocks.append(Block.tool_result(_server_result_text(part.get("content")), part.get("tool_use_id"),
                                                tool_names.get(part.get("tool_use_id") or "")))
        if blocks:
            t.assistant(msg.get("id"), blocks, ts, model)

    def _system_row(self, t: _Thread, row: dict[str, Any], ts: str | None) -> None:
        subtype = row.get("subtype") or ""
        content = row.get("content")
        if subtype == "compact_boundary":
            meta = row.get("compactMetadata") or {}
            detail = f" ({meta.get('trigger')}, {meta.get('preTokens'):,} tokens before)" if meta.get("preTokens") else ""
            t.system(f"Conversation compacted{detail}", ts, kind="compact_boundary")
        elif isinstance(content, str) and content.strip():
            t.system(content, ts, kind=subtype or "system")

    def _attachment_row(self, t: _Thread, row: dict[str, Any], ts: str | None) -> None:
        att = row.get("attachment") or {}
        kind = att.get("type")
        if kind == "queued_command" and att.get("prompt"):
            prompt = att["prompt"]
            text = prompt if isinstance(prompt, str) else _tool_result_text(prompt)
            if text.strip():
                t.add("user", [Block.text_block(text)], att.get("timestamp") or ts, kind="queued_prompt")
        elif kind == "edited_text_file" and att.get("filename"):
            t.system(f"File changed outside the agent: {att['filename']}\n{att.get('snippet', '')}", ts, kind="file_edited")


def _server_result_text(content: Any) -> str:
    if isinstance(content, list):
        lines = []
        for r in content:
            if isinstance(r, dict):
                lines.append(" – ".join(str(x) for x in (r.get("title"), r.get("url")) if x) or json.dumps(r)[:300])
        return "\n".join(lines)
    return _tool_result_text(content)
