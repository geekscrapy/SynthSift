r"""Google Antigravity (agentic IDE and the ``agy`` CLI) conversations.

Where to find them (same layout on macOS, Linux and Windows, under
``%USERPROFILE%\.gemini`` on Windows)::

    ~/.gemini/antigravity/          the IDE (``antigravity-ide/`` in newer builds)
    ~/.gemini/antigravity-cli/      the CLI
        brain/<conversation id>/.system_generated/logs/transcript_full.jsonl   every step, untruncated
        brain/<conversation id>/.system_generated/logs/transcript.jsonl        large outputs truncated
        brain/<conversation id>/.system_generated/logs/overview.txt            same records (IDE)
        brain/<conversation id>/*.md                 task, implementation plan, walkthrough
        conversations/<conversation id>.pb | .db     the agent's own store (see below)
        history.jsonl                                CLI prompt log: workspace per conversation

``collector/synthsift_collect.py`` packs the transcripts and the CLI's
``history.jsonl`` into an upload zip (glob list:
``collector/globs/antigravity.txt``).

The brain transcript is plaintext JSONL, one row per step::

    {"step_index": 3, "source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE",
     "created_at": "2026-05-20T21:41:51Z", "content": "…", "thinking": "…",
     "tool_calls": [{"name": "run_command", "args": {"CommandLine": "…", "Cwd": "…"}}]}

``source`` is ``USER_EXPLICIT`` (the user), ``MODEL`` or ``SYSTEM``.  A
``USER_INPUT`` step is a prompt (wrapped in ``<USER_REQUEST>``; other tagged
blocks beside it are harness context and become ``system`` messages).  A
``PLANNER_RESPONSE`` is the model's reply, reasoning and tool calls.  The
steps that follow it and are named after a tool (``RUN_COMMAND``,
``VIEW_FILE``, ``SEARCH_WEB``, …) carry that tool's result in ``content``;
they answer the open calls in order, matched by tool name.  When a folder
holds several of the transcripts, only the fullest is read.

``conversations/<id>.pb`` is AES-encrypted and ``.db`` holds protobuf in an
unpublished schema, so neither is read.  Antigravity's local language-server
API (``GetCascadeTrajectory``), and export tools built on it, return the same
conversation as JSON ``steps`` typed ``CORTEX_STEP_TYPE_*``; such a file is
read too, and carries the tool results (command output, file contents,
diffs) in full.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import unquote

from ..models import Block, Conversation, Message
from .base import HarnessParser, register

_TRANSCRIPTS = ("transcript_full.jsonl", "transcript.jsonl", "overview.txt")  # fullest first
_ROOTS = {"antigravity-ide": "IDE", "antigravity-cli": "CLI"}
_USER_REQUEST = re.compile(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.S)
_TAGGED = re.compile(r"<([A-Z][A-Z0-9_]+)>\s*(.*?)\s*</\1>", re.S)
_STEP_PREFIX = "CORTEX_STEP_TYPE_"
# UI hints the agent adds to every call; they describe the call, they are not its input
_UI_ARGS = {"toolAction", "toolSummary", "waitForPreviousTools"}
# steps that are the harness talking, not a tool answering
_SYSTEM_STEPS = {"SYSTEM_MESSAGE", "EPHEMERAL_MESSAGE", "CONVERSATION_HISTORY", "CHECKPOINT", "KNOWLEDGE_ARTIFACTS",
                 "MEMORY", "RETRIEVE_MEMORY", "USER_SETTINGS_CHANGE"}
# result step -> the tool names that produce it, when the two are not simply the same word
_RESULT_OF = {
    "LIST_DIRECTORY": ("list_dir",), "FIND": ("find_by_name",), "VIEW_FILE": ("view_file", "view_file_outline"),
    "CODE_ACTION": ("write_to_file", "replace_file_content", "multi_replace_file_content"),
    "COMMAND_STATUS": ("command_status",), "SEND_COMMAND_INPUT": ("send_command_input",),
}
_ERROR_STATUS = {"ERROR", "FAILED", "CANCELED", "CANCELLED"}
_ABSOLUTE = re.compile(r"^(/|~|[A-Za-z]:[\\/])")


def _brain_id(path: str) -> str | None:
    """The conversation id of ``…/brain/<id>/.system_generated/logs/<transcript>``."""
    parts = PurePosixPath(path).parts
    if len(parts) >= 5 and parts[-2] == "logs" and parts[-3] == ".system_generated" and parts[-5] == "brain":
        return parts[-4]
    return None


def _clean_args(args: Any) -> dict[str, Any]:
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return {"input": args}
    if not isinstance(args, dict):
        return {"input": args} if args not in (None, "") else {}
    return {k: v for k, v in args.items() if k not in _UI_ARGS}


def _uri_path(uri: Any) -> str:
    return unquote(str(uri or "")).removeprefix("file://")


class _Builder:
    def __init__(self) -> None:
        self.messages: list[Message] = []
        self.models: list[str] = []
        self.pending: list[tuple[str, str]] = []  # (call id, tool name) still waiting for a result
        self.cwd: str | None = None
        self.calls = 0

    def add(self, role: str, blocks: list[Block], ts: str | None, model: str | None = None, **meta: Any) -> None:
        if blocks:
            self.messages.append(Message(role=role, blocks=blocks, timestamp=ts, model=model,
                                         meta={k: v for k, v in meta.items() if v}))

    def system(self, text: str, ts: str | None, kind: str) -> None:
        if text and text.strip():
            self.add("system", [Block.text_block(text.strip())], ts, kind=kind)

    def user(self, text: str, ts: str | None) -> None:
        text = (text or "").strip()
        if not text:
            return
        req = _USER_REQUEST.search(text)
        if req:
            self.add("user", [Block.text_block(req.group(1))], ts)
            rest = _USER_REQUEST.sub("", text).strip()
            for m in _TAGGED.finditer(rest):
                self.system(m.group(2), ts, m.group(1).lower())
            leftover = _TAGGED.sub("", rest).strip()
            self.system(leftover, ts, "context")
        else:
            self.add("user", [Block.text_block(text)], ts)

    def planner(self, text: str, thinking: str, calls: list[tuple[str | None, str, dict[str, Any]]],
                ts: str | None, model: str | None) -> None:
        if model:
            self.models.append(model)
        blocks: list[Block] = []
        if thinking and thinking.strip():
            blocks.append(Block.thinking(thinking))
        if text and text.strip():
            blocks.append(Block.text_block(text))
        for cid, name, args in calls:
            self.calls += 1
            cid = cid or f"call-{self.calls}"
            if not self.cwd:  # where the agent worked: the first absolute directory a call names
                self.cwd = next((args[k] for k in ("Cwd", "cwd", "DirectoryPath", "SearchDirectory")
                                 if isinstance(args.get(k), str) and _ABSOLUTE.match(args[k])), None)
            blocks.append(Block.tool_call(name, args, cid))
            self.pending.append((cid, name))
        self.add("assistant", blocks, ts, model)

    def result(self, step_type: str, text: str, ts: str | None, error: bool, call_id: str | None = None) -> bool:
        """Answer an open call with a result step; False when no call is open."""
        if not self.pending:
            return False
        names = {step_type.lower(), *_RESULT_OF.get(step_type, ())}
        pick = next((p for p in self.pending if p[0] == call_id), None) if call_id else None
        pick = pick or next((p for p in self.pending if p[1] in names), None) or self.pending[0]
        self.pending.remove(pick)
        self.add("tool", [Block.tool_result(text, pick[0], pick[1], error)], ts)
        return True

    def step(self, step_type: str, text: str, ts: str | None, status: str, call_id: str | None = None) -> None:
        """A step that is neither a prompt nor a reply: a tool's result, or the harness talking."""
        error = status.upper() in _ERROR_STATUS or step_type == "ERROR_MESSAGE"
        if step_type in _SYSTEM_STEPS or not text.strip() and not self.pending:
            self.system(text, ts, step_type.lower())
        elif not self.result(step_type, text, ts, error, call_id):
            # a result whose call was not logged (or the transcript starts mid-run)
            self.add("tool", [Block.tool_result(text, None, step_type.lower(), error)], ts)


@register
class AntigravityParser(HarnessParser):
    name = "antigravity"
    label = "Google Antigravity"
    aliases = ("google-antigravity", "google_antigravity")
    subagent_tools = ("invoke_subagent", "browser_subagent")
    hidden_folders = (".system_generated",)
    file_names = ("overview.txt",)
    description = "Antigravity IDE / CLI: ~/.gemini/antigravity*/brain/<id>/.system_generated/logs/transcript*.jsonl."

    @classmethod
    def owns(cls, path: str) -> bool:
        return _brain_id(path) is not None and PurePosixPath(path).name in _TRANSCRIPTS

    @classmethod
    def superseded_by(cls, path: str) -> tuple[str, ...]:
        p = PurePosixPath(path)
        if p.name not in _TRANSCRIPTS:
            return ()
        return tuple(str(p.with_name(n)) for n in _TRANSCRIPTS[: _TRANSCRIPTS.index(p.name)])

    @classmethod
    def companion_paths(cls, path: str) -> dict[str, str]:
        parts = PurePosixPath(path).parts
        if _brain_id(path) is None:
            return {}
        return {"history": str(PurePosixPath(*parts[:-5], "history.jsonl"))} if len(parts) > 5 else {}

    def sniff(self, raw: bytes, filename: str) -> float:
        head = raw[:65536].decode("utf-8", errors="ignore")
        if _STEP_PREFIX in head and ('"steps"' in head or head.lstrip().startswith("[")):
            return 0.9
        if '"step_index"' in head and re.search(r'"source"\s*:\s*"(USER_EXPLICIT|MODEL|SYSTEM)"', head):
            return 0.95
        return 0.0

    def parse(self, raw: bytes, filename: str) -> list[Conversation]:
        raw, filename = self.decompress(raw, filename)
        if filename.lower() == "history.jsonl":
            return []  # the CLI's prompt log: read as a companion of each transcript
        text = raw.decode("utf-8-sig", errors="replace").strip()
        data: Any = None
        if text.startswith("[") or text.startswith("{") and "\n{" not in text:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = None
        if data is not None and not (isinstance(data, dict) and "step_index" in data):
            conv = self._trajectory(data)
            return [conv] if conv else []
        rows, bad = self.read_jsonl(raw)
        if bad:
            self.warnings.append(f"skipped {bad} unreadable line(s)")
        conv = self._brain([r for r in rows if isinstance(r, dict) and ("source" in r or "type" in r)])
        return [conv] if conv else []

    # ------------------------------------------------------ brain transcript
    def _brain(self, rows: list[dict[str, Any]]) -> Conversation | None:
        rows = sorted(rows, key=lambda r: r.get("step_index") if isinstance(r.get("step_index"), int) else 0)
        b = _Builder()
        for r in rows:
            ts, typ, source = r.get("created_at"), str(r.get("type") or ""), str(r.get("source") or "")
            content = r.get("content") if isinstance(r.get("content"), str) else ""
            if typ == "USER_INPUT" or source == "USER_EXPLICIT":
                b.user(content, ts)
            elif typ == "PLANNER_RESPONSE":
                calls = [(None, str(c.get("name")), _clean_args(c.get("args")))
                         for c in r.get("tool_calls") or [] if isinstance(c, dict) and c.get("name")]
                b.planner(content, r.get("thinking") if isinstance(r.get("thinking"), str) else "", calls, ts, None)
            else:
                b.step(typ, content, ts, str(r.get("status") or ""))
        return self._finish(b, rows[0].get("created_at") if rows else None)

    # ------------------------------------------------- API trajectory (JSON)
    def _trajectory(self, data: Any) -> Conversation | None:
        if isinstance(data, dict):
            data = data.get("trajectory") if isinstance(data.get("trajectory"), dict) else data
            steps, gen = data.get("steps"), data.get("generatorMetadata") or []
        else:
            steps, gen = data, []
        if not isinstance(steps, list):
            return None
        models = [str(((g or {}).get("chatModel") or {}).get("model") or "") for g in gen if isinstance(g, dict)]
        b = _Builder()
        b.models.extend(m for m in models if m)
        first_ts = None
        for step in steps:
            if not isinstance(step, dict):
                continue
            typ = str(step.get("type") or "").removeprefix(_STEP_PREFIX)
            md = step.get("metadata") if isinstance(step.get("metadata"), dict) else {}
            ts = md.get("createdAt")
            first_ts = first_ts or ts
            status = str(step.get("status") or "").removeprefix("CORTEX_STEP_STATUS_")
            if typ == "USER_INPUT":
                b.user(_user_input_text(step.get("userInput") or {}), ts)
            elif typ == "PLANNER_RESPONSE":
                pr = step.get("plannerResponse") or {}
                calls = [(c.get("id"), str(c.get("name")), _clean_args(c.get("argumentsJson") or c.get("args")))
                         for c in pr.get("toolCalls") or [] if isinstance(c, dict) and c.get("name")]
                text = pr.get("modifiedResponse") or pr.get("response") or pr.get("text") or ""
                b.planner(text, pr.get("thinking") or "", calls, ts, None)
            elif typ:
                call_id = md.get("toolCallId") or (md.get("toolCall") or {}).get("id") or md.get("executionId")
                b.step(typ, _step_text(typ, step), ts, status, call_id)
        return self._finish(b, first_ts)

    # ------------------------------------------------------------------ both
    def _finish(self, b: _Builder, started: str | None) -> Conversation | None:
        if not any(m.role in ("user", "assistant", "tool") for m in b.messages):
            return None
        conv = Conversation(messages=b.messages, started_at=started or b.messages[0].timestamp,
                            model=Counter(b.models).most_common(1)[0][0] if b.models else None)
        cid = _brain_id(self.source_path)
        parts = PurePosixPath(self.source_path).parts
        if cid:
            conv.meta["conversation_id"] = cid
            root = parts[-6] if len(parts) >= 6 else ""
            # the IDE's folder is plain "antigravity": only telling when it sits in .gemini (or in the
            # zip's antigravity folder), not when it is that zip folder itself
            app = _ROOTS.get(root) or ("IDE" if root == "antigravity" and len(parts) >= 7
                                       and parts[-7] in ("antigravity", ".gemini") else None)
            if app:
                conv.meta["app"] = f"Antigravity {app}"
        cwd = self._workspace(cid) or b.cwd
        if cwd:
            conv.meta["cwd"] = cwd
            conv.meta["project"] = cwd.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        return conv

    def _workspace(self, cid: str | None) -> str | None:
        """The workspace the CLI's prompt log recorded for this conversation."""
        raw = self.companions.get("history")
        if not raw or not cid:
            return None
        rows, _ = self.read_jsonl(raw)
        for r in rows:
            if isinstance(r, dict) and r.get("conversationId") == cid and r.get("workspace"):
                return _uri_path(r["workspace"])
        return None


def _user_input_text(ui: dict[str, Any]) -> str:
    if isinstance(ui.get("userResponse"), str) and ui["userResponse"].strip():
        return ui["userResponse"]
    parts: list[str] = []
    for wrapper in ui.get("items") or []:
        item = wrapper.get("item", wrapper) if isinstance(wrapper, dict) else {}
        if not isinstance(item, dict):
            continue
        if (item.get("recipe") or {}).get("title"):
            parts.append(f"/{item['recipe']['title']}")
        text = item.get("text")
        if isinstance(text, dict):
            text = text.get("text")
        if isinstance(text, str) and text:
            parts.append(text)
        if (item.get("textBlock") or {}).get("content"):
            parts.append(item["textBlock"]["content"])
    return "\n\n".join(parts)


def _step_text(typ: str, step: dict[str, Any]) -> str:
    """The readable result of a trajectory step: command output, file content, diff, search hits, …"""
    key = re.sub(r"_(\w)", lambda m: m.group(1).upper(), typ.lower())  # RUN_COMMAND -> runCommand
    body = step.get(key)
    if not isinstance(body, dict):
        return ""
    if typ == "RUN_COMMAND":
        out = body.get("combinedOutput")
        if isinstance(out, dict):
            out = "\n".join(str(out[k]) for k in ("full", "stdout", "stderr", "output", "delta") if out.get(k))
        code = body.get("exitCode")
        return (str(out or "") + (f"\n(exit code {code})" if code not in (None, "", 0) else "")).strip()
    if typ == "COMMAND_STATUS":
        return str(body.get("combined") or body.get("delta") or "")
    if typ == "VIEW_FILE":
        return str(body.get("content") or "")
    if typ == "CODE_ACTION":
        res = body.get("actionResult")
        lines = (((res or {}).get("edit") or {}).get("diff") or {}).get("unifiedDiff", {}).get("lines") if isinstance(res, dict) else None
        if lines:
            sign = {"UNIFIED_DIFF_LINE_TYPE_INSERT": "+", "UNIFIED_DIFF_LINE_TYPE_DELETE": "-"}
            return "\n".join(sign.get(str(ln.get("type")), " ") + str(ln.get("text", "")) for ln in lines if isinstance(ln, dict))
        return res if isinstance(res, str) else str(body.get("description") or "")
    if typ in ("GREP_SEARCH", "FIND"):
        hits = [r.get("relativePath") or r.get("absolutePath") or json.dumps(r) if isinstance(r, dict) else str(r)
                for r in body.get("results") or []]
        return "\n".join(map(str, hits))
    if typ == "LIST_DIRECTORY":
        kids = body.get("children") or body.get("results") or []
        return "\n".join(str(k.get("name") or k) if isinstance(k, dict) else str(k) for k in kids) or _uri_path(body.get("directoryPathUri"))
    if typ == "NOTIFY_USER":
        return str(body.get("notificationContent") or "")
    if typ == "ERROR_MESSAGE":
        err = body.get("error") or {}
        return "\n".join(str(err[k]) for k in ("userErrorMessage", "modelErrorMessage") if err.get(k))
    if typ == "SYSTEM_MESSAGE":
        return str(body.get("message") or "")
    if typ == "CHECKPOINT":
        reqs = body.get("userRequests") or []
        return "\n".join(filter(None, ["User requests: " + "; ".join(map(str, reqs)) if reqs else "",
                                       str(body.get("sessionSummary") or "")]))
    for k in ("content", "output", "result", "text", "message", "response"):  # MCP tools, web search, browser, …
        if isinstance(body.get(k), str) and body[k].strip():
            return body[k]
    return json.dumps(body, ensure_ascii=False)[:20000]
