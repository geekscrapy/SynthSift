r"""Gemini CLI (Google's open-source coding agent) session transcripts.

Where to find them (``$GEMINI_CLI_HOME`` moves the home directory; on Windows
it is ``C:\Users\<you>\.gemini``)::

    ~/.gemini/tmp/<project>/chats/session-<time>-<id>.jsonl          a session
    ~/.gemini/tmp/<project>/chats/<parent session id>/<id>.jsonl     its sub-agents
    ~/.gemini/tmp/<project>/checkpoint-<tag>.json                    /resume save <tag>
    ~/.gemini/tmp/<project>/.project_root                            the project's path

``<project>`` is a short slug of the project folder (a SHA-256 of its path in
older releases).  Sessions are deleted after 30 days by default
(``general.sessionRetention``).  ``collector/synthsift_collect.py`` packs them
into an upload zip (glob list: ``collector/globs/gemini.txt``).

Session format (JSONL, append-only; a single JSON object in ``.json`` files
written before mid-2026):

* a metadata row ``{sessionId, projectHash, startTime, lastUpdated, kind?,
  directories?}`` (``kind: "subagent"`` for a sub-agent);
* message rows ``{id, timestamp, type, content, displayContent?}`` where
  ``type`` is ``user``, ``gemini``, ``info``, ``warning`` or ``error``.
  ``content`` is a string or a list of Gemini API parts (``text``,
  ``thought``, ``functionCall``, ``functionResponse``, ``inlineData``).  A
  ``gemini`` row adds ``thoughts`` (``{subject, description, timestamp}``),
  ``toolCalls`` (``{id, name, args, result, status, timestamp, …}``),
  ``tokens`` and ``model``.  A message is appended again each time it changes
  (tool calls and token counts arrive later), so the last copy wins;
* ``{"$set": {...}}`` metadata updates (``summary`` is the session's title; a
  ``messages`` list rewrites the history after compression or masking) and
  ``{"$rewindTo": id}`` when the user rewinds.

Everything that happened is kept: messages a rewind or a history rewrite
dropped stay in place (a rewind adds a system note where it happened), and
tool output the CLI later masked keeps its original text.  Tool results are
read from ``toolCalls[].result``; current releases also record them as a
synthetic ``user`` row of ``functionResponse`` parts, which is skipped when it
repeats a result already seen.

The CLI's own context turns become ``system`` messages: the
``<session_context>`` preamble (its workspace directory fills ``cwd`` when no
``.project_root`` came with the upload), ``<hook_context>`` and the files an
``@path`` reference pulled in.  ``!`` shell-mode commands the *user* ran
become ``user_shell`` tool calls so they are analysed like the agent's.

A checkpoint is the raw API history ``{"history": [{role, parts}], …}`` (a
bare list in older releases), without timestamps.
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

_REF_START = "--- Content from referenced files ---"
_REF_END = "--- End of content ---"
_SHELL = re.compile(r"^I ran the following shell command:\n```sh\n(.*?)\n```\n\n"
                    r"This produced the following result:\n```\n(.*)\n```\s*$", re.S)
_WORKSPACE = re.compile(r"\*\*Workspace Directories:\*\*\s*\n\s*-\s*(\S[^\n]*)|currently working in the directory:\s*(\S[^\n]*)")
_SYSTEM_TYPES = {"info", "warning", "error"}


def _unescape_shell(text: str) -> str:
    """Undo the backslash escaping the CLI applies before quoting shell text."""
    return re.sub(r"\\([\\`])", r"\1", text)


def _parts(content: Any) -> list[Any]:
    if content is None or content == "":
        return []
    return content if isinstance(content, list) else [content]


def _response_text(resp: Any) -> tuple[str, bool]:
    """Text of a ``functionResponse.response`` and whether it reports an error."""
    if resp is None:
        return "", False
    if isinstance(resp, str):
        return resp, False
    if isinstance(resp, dict):
        if resp.get("error") not in (None, ""):
            err = resp["error"]
            return (err if isinstance(err, str) else json.dumps(err, ensure_ascii=False)), True
        for key in ("output", "llmContent", "content", "result"):
            val = resp.get(key)
            if isinstance(val, str):
                return val, False
            if val is not None:
                return _parts_text(_parts(val))[0] or json.dumps(val, ensure_ascii=False), False
    return json.dumps(resp, ensure_ascii=False), False


def _parts_text(parts: list[Any]) -> tuple[str, list[str]]:
    """Plain text and thought text of a list of parts (function calls/responses left out)."""
    text, thoughts = [], []
    for part in parts:
        if isinstance(part, str):
            text.append(part)
        elif isinstance(part, dict):
            if part.get("functionCall") or part.get("functionResponse"):
                continue
            if isinstance(part.get("text"), str):
                (thoughts if part.get("thought") else text).append(part["text"])
            elif part.get("inlineData") or part.get("fileData"):
                data = part.get("inlineData") or part.get("fileData") or {}
                text.append(f"[{data.get('mimeType') or 'file'} attached]")
            elif part.get("executableCode"):
                text.append(str(part["executableCode"].get("code", "")))
            elif part.get("codeExecutionResult"):
                text.append(str(part["codeExecutionResult"].get("output", "")))
    return "".join(text), thoughts


def _thought_text(t: Any) -> str:
    if isinstance(t, str):
        return t
    if not isinstance(t, dict):
        return ""
    subject, desc = str(t.get("subject") or "").strip(), str(t.get("description") or "").strip()
    return f"{subject}\n\n{desc}" if subject and desc else subject or desc


class _Builder:
    """Messages of one conversation, with the tool results already seen."""

    def __init__(self) -> None:
        self.messages: list[Message] = []
        self.models: list[str] = []
        self.answered: set[str] = set()
        self.names: dict[str, str] = {}
        self.pending: list[str] = []  # call ids still waiting for a result, oldest first
        self.cwd: str | None = None
        self.calls = 0

    def add(self, role: str, blocks: list[Block], ts: str | None, model: str | None = None, **meta: Any) -> None:
        if blocks:
            self.messages.append(Message(role=role, blocks=blocks, timestamp=ts, model=model,
                                         meta={k: v for k, v in meta.items() if v}))

    def system(self, text: str, ts: str | None, kind: str) -> None:
        if text.strip():
            self.add("system", [Block.text_block(text.strip())], ts, kind=kind)

    def call_id(self, given: Any) -> str:
        self.calls += 1
        return str(given) if given else f"call-{self.calls}"

    # ------------------------------------------------------------- user side
    def user(self, content: Any, ts: str | None, display: Any = None) -> None:
        parts = _parts(content)
        results = [self.result_block(p["functionResponse"]) for p in parts
                   if isinstance(p, dict) and isinstance(p.get("functionResponse"), dict)]
        results = [b for b in results if b is not None]
        if results:
            self.add("tool", results, ts)
        text = _parts_text(parts)[0]
        if not text.strip() and display is not None:
            text = _parts_text(_parts(display))[0]
        self.user_text(text, ts)

    def user_text(self, text: str, ts: str | None) -> None:
        stripped = text.strip()
        if not stripped:
            return
        if stripped.startswith("<session_context>"):
            m = _WORKSPACE.search(stripped)
            if m and not self.cwd:
                self.cwd = (m.group(1) or m.group(2)).strip()
            self.system(re.sub(r"</?session_context>", "", stripped), ts, "session_context")
            return
        if stripped.startswith("<hook_context>"):
            self.system(re.sub(r"</?hook_context>", "", stripped), ts, "hook_context")
            return
        shell = _SHELL.match(stripped)
        if shell:  # "!" shell mode: the user ran a command and its output went to the model
            self.calls += 1
            cid = f"user-shell-{self.calls}"
            self.add("user", [Block.tool_call("user_shell", {"command": _unescape_shell(shell.group(1))}, cid)], ts,
                     kind="user_shell")
            self.add("tool", [Block.tool_result(_unescape_shell(shell.group(2)), cid, "user_shell")], ts)
            return
        typed, refs = stripped, ""
        if _REF_START in stripped:
            typed, refs = stripped.split(_REF_START, 1)
            refs = refs.replace(_REF_END, "").strip()
        typed = typed.strip()
        if typed:
            self.add("user", [Block.text_block(typed)], ts, kind="command" if typed.startswith("/") else None)
        if refs:
            self.system(refs, ts, "referenced_files")

    # ---------------------------------------------------------- model side
    def result_block(self, fr: dict[str, Any], status: str | None = None, display: Any = None) -> Block | None:
        cid = str(fr.get("id") or "")
        if cid and cid in self.answered:
            return None
        if not cid:  # checkpoints carry no call ids: answer the oldest open call of that tool
            cid = next((c for c in self.pending if self.names.get(c) == fr.get("name")), self.pending[0] if self.pending else "")
        text, err = _response_text(fr.get("response"))
        extra = _parts_text(_parts(fr.get("parts")))[0]
        if extra:
            text = f"{text}\n\n{extra}" if text else extra
        if not text and isinstance(display, str):
            text = display
        if cid:
            self.answered.add(cid)
            if cid in self.pending:
                self.pending.remove(cid)
        name = fr.get("name") or self.names.get(cid)
        return Block.tool_result(text, cid or None, name, err or status == "error")

    def gemini(self, rec: dict[str, Any], ts: str | None) -> None:
        model = rec.get("model") or None
        if model:
            self.models.append(model)
        blocks: list[Block] = [Block.thinking(t) for t in map(_thought_text, rec.get("thoughts") or []) if t.strip()]
        parts = _parts(rec.get("content"))
        text, thoughts = _parts_text(parts)
        blocks += [Block.thinking(t) for t in thoughts if t.strip()]
        if text.strip():
            blocks.append(Block.text_block(text))
        records = [tc for tc in rec.get("toolCalls") or [] if isinstance(tc, dict)]
        recorded = {str(tc.get("id")) for tc in records if tc.get("id")}
        for p in parts:  # calls only present as parts (checkpoints, synthetic rows)
            fc = p.get("functionCall") if isinstance(p, dict) else None
            if isinstance(fc, dict) and str(fc.get("id") or "") not in recorded:
                records.append({"id": fc.get("id"), "name": fc.get("name"), "args": fc.get("args")})
        results: list[Block] = []
        for tc in records:
            name = str(tc.get("name") or "tool")
            cid = self.call_id(tc.get("id"))
            self.names[cid] = name
            self.pending.append(cid)
            args = tc.get("args")
            blocks.append(Block.tool_call(name, args if isinstance(args, dict) else {"input": args}, cid))
            result = _parts(tc.get("result"))
            # parts beside the functionResponse (e.g. the files read_many_files returns) belong to the result
            siblings = [p for p in result if not (isinstance(p, dict) and p.get("functionResponse"))]
            for p in result:
                fr = p.get("functionResponse") if isinstance(p, dict) else None
                if isinstance(fr, dict):
                    block = self.result_block({**fr, "id": fr.get("id") or cid, "name": fr.get("name") or name,
                                               "parts": siblings}, tc.get("status"), tc.get("resultDisplay"))
                    siblings = []
                    if block:
                        results.append(block)
            display = tc.get("resultDisplay") if isinstance(tc.get("resultDisplay"), str) else ""
            if cid not in self.answered and (display or tc.get("status") in ("error", "cancelled")):
                self.answered.add(cid)
                self.pending.remove(cid)
                results.append(Block.tool_result(display or f"({tc['status']})", cid, name,
                                                 tc.get("status") in ("error", "cancelled")))
        self.add("assistant", blocks, ts, model)
        if results:
            self.add("tool", results, (records[-1].get("timestamp") if records else None) or ts)


@register
class GeminiCliParser(HarnessParser):
    name = "gemini"
    label = "Gemini CLI"
    aliases = ("gemini-cli", "gemini_cli", "geminicli")
    subagent_tools = ("invoke_agent", "delegate_to_agent", "codebase_investigator")
    description = "Gemini CLI sessions: ~/.gemini/tmp/<project>/chats/session-*.jsonl (+ sub-agents, saved checkpoints)."

    @classmethod
    def companion_paths(cls, path: str) -> dict[str, str]:
        parts = PurePosixPath(path).parts
        if "chats" in parts:
            project = parts[: len(parts) - 1 - parts[::-1].index("chats")]
        else:
            project = parts[:-1]
        return {"project_root": str(PurePosixPath(*project, ".project_root"))} if project else {}

    def sniff(self, raw: bytes, filename: str) -> float:
        head = raw[:65536].decode("utf-8", errors="ignore")
        if '"sessionId"' in head and '"projectHash"' in head:
            return 0.95
        if re.search(r'"role"\s*:\s*"model"', head) and '"parts"' in head:
            return 0.6
        return 0.0

    def parse(self, raw: bytes, filename: str) -> list[Conversation]:
        raw, filename = self.decompress(raw, filename)
        text = raw.decode("utf-8-sig", errors="replace").strip()
        data: Any = None
        if text.startswith("["):
            data = json.loads(text)
        elif text.startswith("{") and "\n{" not in text:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = None
        if isinstance(data, list) or (isinstance(data, dict) and "history" in data):
            conv = self._checkpoint(data if isinstance(data, list) else data.get("history") or [], filename)
            return [conv] if conv else []
        if isinstance(data, dict):
            rows = [data]
        else:
            rows, bad = self.read_jsonl(raw)
            if bad:
                self.warnings.append(f"skipped {bad} unreadable line(s)")
        return self._session([r for r in rows if isinstance(r, dict)])

    # ------------------------------------------------------------ sessions
    def _session(self, rows: list[dict[str, Any]]) -> list[Conversation]:
        meta: dict[str, Any] = {}
        records: dict[str, dict[str, Any]] = {}  # message id -> latest copy, in first-seen order
        order: list[tuple[str, Any]] = []  # ("msg", id) | ("rewind", (ts, target id))
        last_ts: str | None = None  # the latest time seen, for rows that carry none

        def put(msg: dict[str, Any], overwrite: bool = True) -> None:
            mid = msg["id"]
            if mid not in records:
                order.append(("msg", mid))
                records[mid] = msg
            elif overwrite:
                records[mid] = {**records[mid], **msg} if msg.get("type") == "gemini" else msg

        for row in rows:
            for stamp in (row.get("timestamp"), (row.get("$set") or {}).get("lastUpdated") if isinstance(row.get("$set"), dict) else None):
                if isinstance(stamp, str) and stamp > (last_ts or ""):
                    last_ts = stamp
            if isinstance(row.get("$rewindTo"), str):
                order.append(("rewind", (last_ts, row["$rewindTo"])))
            elif isinstance(row.get("$set"), dict):
                upd = dict(row["$set"])
                for msg in upd.pop("messages", None) or []:
                    if isinstance(msg, dict) and isinstance(msg.get("id"), str):
                        put(msg, overwrite=False)  # a rewritten history: keep what was recorded first
                meta.update(upd)
            elif isinstance(row.get("id"), str) and "type" in row:
                put(row)
            elif "sessionId" in row:
                for msg in row.get("messages") or []:
                    if isinstance(msg, dict) and isinstance(msg.get("id"), str):
                        put(msg)
                meta.update({k: v for k, v in row.items() if k != "messages"})

        b = _Builder()
        for kind, ref in order:
            if kind == "rewind":
                ts, target = ref
                first = records.get(target)
                what = _parts_text(_parts(first.get("content")))[0].strip().splitlines()[0][:120] if first and first.get("content") else ""
                b.system(f"Rewound to before {('“' + what + '”') if what else 'an earlier turn'}: that turn and "
                         "everything after it were taken out of the model's context", ts, "rewind")
                continue
            rec = records[ref]
            ts = rec.get("timestamp")
            typ = rec.get("type")
            if typ == "user":
                b.user(rec.get("content"), ts, rec.get("displayContent"))
            elif typ == "gemini":
                b.gemini(rec, ts)
            elif typ in _SYSTEM_TYPES:
                b.system(_parts_text(_parts(rec.get("content")))[0], ts, typ)
        if not any(m.role in ("user", "assistant", "tool") for m in b.messages):
            return []

        conv = Conversation(messages=b.messages, started_at=meta.get("startTime") or b.messages[0].timestamp,
                            model=Counter(b.models).most_common(1)[0][0] if b.models else None)
        conv.meta = {k: v for k, v in {
            "session_id": meta.get("sessionId"), "project_hash": meta.get("projectHash"),
            "last_updated": meta.get("lastUpdated"),
        }.items() if v}
        self._place(conv, b.cwd, meta.get("directories"))
        parts = PurePosixPath(self.source_path).parts
        sub = meta.get("kind") == "subagent" or (len(parts) >= 3 and parts[-3] == "chats")
        if sub:
            conv.meta["subagent"] = True
            if len(parts) >= 3 and parts[-3] == "chats":
                conv.meta["parent_session_id"] = parts[-2]
            prompt = conv.display_title
            conv.title = f"Sub-agent: {prompt}" if prompt else "Sub-agent"
        else:
            conv.title = meta.get("summary") or None
        return [conv]

    # ---------------------------------------------------------- checkpoints
    def _checkpoint(self, history: list[Any], filename: str) -> Conversation | None:
        b = _Builder()
        for content in history:
            if not isinstance(content, dict):
                continue
            if content.get("role") == "model":
                b.gemini({"content": content.get("parts") or []}, None)
            else:
                b.user(content.get("parts") or [], None)
        if not any(m.role in ("user", "assistant", "tool") for m in b.messages):
            return None
        stem = PurePosixPath(filename).stem
        tag = unquote(stem[len("checkpoint-"):]) if stem.startswith("checkpoint-") else stem
        conv = Conversation(messages=b.messages, title=f"Saved chat: {tag}", meta={"checkpoint": tag})
        self._place(conv, b.cwd, None)
        return conv

    def _place(self, conv: Conversation, cwd: str | None, directories: Any) -> None:
        """The project folder: the upload's .project_root, else the workspace the session reported."""
        root = self.companions.get("project_root", b"").decode("utf-8", errors="replace").strip()
        if not root and isinstance(directories, list) and directories:
            root = str(directories[0])
        root = root or cwd or ""
        if root:
            conv.meta["cwd"] = root
            conv.meta["project"] = root.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        if isinstance(directories, list) and len(directories) > 1:
            conv.meta["directories"] = ", ".join(map(str, directories))
