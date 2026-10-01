"""Gemini CLI and Google Antigravity parsers: session replay, checkpoints, brain transcripts, API trajectories,
and how ingest routes, pairs and de-duplicates their files."""

import io
import json
import zipfile

from synthsift.harnesses import get_parser, sniff_parser
from synthsift.harnesses.antigravity import AntigravityParser
from synthsift.harnesses.gemini import GeminiCliParser
from synthsift.ingest import read_zip
from synthsift.segment import segment


def rows_jsonl(rows: list[dict]) -> bytes:
    return "".join(json.dumps(r) + "\n" for r in rows).encode()


def blocks(conv):
    return [(m.role, b.kind, b.tool_name, b.tool_call_id, b.text or json.dumps(b.arguments), b.is_error, m.meta.get("kind"))
            for m in conv.messages for b in m.blocks]


def zip_bytes(files: dict[str, bytes | str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, body in files.items():
            zf.writestr(name, body)
    return buf.getvalue()


# ------------------------------------------------------------------ Gemini CLI
CTX = ("<session_context>\nThis is the Gemini CLI.\n- **Workspace Directories:**\n  - /home/dev/shop\n"
       "- **Directory Structure:**\n</session_context>")


def call(cid, name, args, output, status="success", error=False):
    resp = {"error": output} if error else {"output": output}
    return {"id": cid, "name": name, "args": args, "status": status, "timestamp": "2026-06-02T09:00:09Z",
            "result": [{"functionResponse": {"id": cid, "name": name, "response": resp}}]}


GEMINI_ROWS = [
    {"sessionId": "s-1", "projectHash": "shop", "startTime": "2026-06-02T09:00:00Z", "lastUpdated": "2026-06-02T09:00:00Z",
     "kind": "main"},
    {"id": "m0", "timestamp": "2026-06-02T09:00:00Z", "type": "user", "content": [{"text": CTX}]},
    {"id": "m1", "timestamp": "2026-06-02T09:00:05Z", "type": "user",
     "content": [{"text": "why does @src/app.py crash?"}, {"text": "\n--- Content from referenced files ---"},
                 {"text": "\nContent from @src/app.py:\n"}, {"text": "import os\nprint(os.environ['X'])"},
                 {"text": "\n--- End of content ---"}],
     "displayContent": [{"text": "why does @src/app.py crash?"}]},
    {"id": "m2", "timestamp": "2026-06-02T09:00:07Z", "type": "gemini", "content": "Let me run it.", "model": "gemini-3-pro",
     "thoughts": [{"subject": "Reproduce", "description": "Run the script first.", "timestamp": "2026-06-02T09:00:06Z"}]},
    # the same message again, now with its tool calls and their results
    {"id": "m2", "timestamp": "2026-06-02T09:00:07Z", "type": "gemini", "content": "Let me run it.", "model": "gemini-3-pro",
     "thoughts": [{"subject": "Reproduce", "description": "Run the script first.", "timestamp": "2026-06-02T09:00:06Z"}],
     "toolCalls": [call("sh-1", "run_shell_command", {"command": "python src/app.py"}, "KeyError: 'X'"),
                   call("rd-2", "read_file", {"file_path": "/etc/shadow"}, "permission denied", "error", True)]},
    # current releases record the results a second time as a user row
    {"id": "m3", "timestamp": "2026-06-02T09:00:09Z", "type": "user",
     "content": [{"functionResponse": {"id": "sh-1", "name": "run_shell_command", "response": {"output": "KeyError: 'X'"}}}]},
    {"id": "m4", "timestamp": "2026-06-02T09:00:12Z", "type": "gemini", "content": "X is not set.", "model": "gemini-3-pro"},
    {"id": "m5", "timestamp": "2026-06-02T09:01:00Z", "type": "user",
     "content": [{"text": "I ran the following shell command:\n```sh\nexport X=1 && echo \\`date\\`\n```\n\n"
                          "This produced the following result:\n```\nok\n```"}]},
    {"id": "m6", "timestamp": "2026-06-02T09:01:30Z", "type": "user", "content": "use an in-memory store instead"},
    {"$rewindTo": "m6"},
    {"id": "m7", "timestamp": "2026-06-02T09:02:00Z", "type": "info", "content": "Request cancelled."},
    # a rewritten (masked) history must not replace what was recorded
    {"$set": {"summary": "Fix KeyError in app.py", "lastUpdated": "2026-06-02T09:02:05Z",
              "messages": [{"id": "m1", "type": "user", "content": "<masked>"},
                           {"id": "m8", "timestamp": "2026-06-02T09:02:05Z", "type": "user", "content": "/compress"}]}},
]


def test_gemini_session_replays_updates_and_keeps_what_happened():
    p = GeminiCliParser()
    p.source_path = "h/u/gemini/tmp/shop/chats/session-2026-06-02T09-00-s1.jsonl"
    [conv] = p.parse(rows_jsonl(GEMINI_ROWS), "session-2026-06-02T09-00-s1.jsonl")
    assert conv.title == "Fix KeyError in app.py" and conv.model == "gemini-3-pro"
    assert conv.meta["cwd"] == "/home/dev/shop" and conv.meta["project"] == "shop" and conv.meta["session_id"] == "s-1"
    got = blocks(conv)
    assert got[0][:2] == ("system", "text") and got[0][6] == "session_context"
    assert ("user", "text", None, None, "why does @src/app.py crash?", False, None) in got
    refs = next(b for b in got if b[6] == "referenced_files")
    assert "Content from @src/app.py:" in refs[4] and "os.environ" in refs[4]
    assert ("assistant", "thinking", None, None, "Reproduce\n\nRun the script first.", False, None) in got
    # one result per call, although the run_shell_command result was recorded twice
    results = [b for b in got if b[1] == "tool_result"]
    assert [(b[2], b[3], b[5]) for b in results if b[2] != "user_shell"] == [("run_shell_command", "sh-1", False),
                                                                            ("read_file", "rd-2", True)]
    # "!" shell mode: the user's own command, unescaped
    shell = next(b for b in got if b[2] == "user_shell" and b[1] == "tool_call")
    assert json.loads(shell[4]) == {"command": "export X=1 && echo `date`"} and shell[6] == "user_shell"
    # the rewound prompt stays, with a note where the rewind happened; the masked copy did not win
    texts = [b[4] for b in got]
    assert "use an in-memory store instead" in texts and "<masked>" not in texts and "/compress" in texts
    note = next(b for b in got if b[6] == "rewind")
    assert "use an in-memory store instead" in note[4]
    assert conv.messages[[m.meta.get("kind") for m in conv.messages].index("rewind")].timestamp == "2026-06-02T09:01:30Z"
    assert any(b[6] == "info" for b in got)
    assert ("user", "text", None, None, "/compress", False, "command") in got
    events, _ = segment(conv, {})
    assert [e.type for e in events].count("tool_call") == 3


def test_gemini_legacy_json_subagent_and_checkpoint():
    legacy = {"sessionId": "old-1", "projectHash": "3b1f", "startTime": "2025-11-20T14:03:00Z", "lastUpdated": "x",
              "messages": [{"id": "a", "timestamp": "2025-11-20T14:03:10Z", "type": "user", "content": "back up my photos"},
                           {"id": "b", "timestamp": "2025-11-20T14:03:58Z", "type": "gemini", "content": "",
                            "toolCalls": [call("w1", "write_file", {"file_path": "/home/l/bin/b.sh", "content": "rsync"},
                                               "Successfully created")],
                            "model": "gemini-2.5-pro"}]}
    many = call("rm1", "read_many_files", {"include": ["*.md"]}, "Read 1 file.")
    many["result"].append({"text": "--- README.md ---\n# Photos"})
    legacy["messages"][1]["toolCalls"].append(many)
    [conv] = GeminiCliParser().parse(json.dumps(legacy, indent=2).encode(), "session-2025-11-20T14-03-old1.json")
    assert [b[:4] for b in blocks(conv)] == [("user", "text", None, None), ("assistant", "tool_call", "write_file", "w1"),
                                             ("assistant", "tool_call", "read_many_files", "rm1"),
                                             ("tool", "tool_result", "write_file", "w1"),
                                             ("tool", "tool_result", "read_many_files", "rm1")]
    assert blocks(conv)[-1][4] == "Read 1 file.\n\n--- README.md ---\n# Photos"
    sub = GeminiCliParser()
    sub.source_path = "h/u/gemini/tmp/shop/chats/s-1/sub-9.jsonl"
    [sc] = sub.parse(rows_jsonl([{"sessionId": "sub-9", "projectHash": "shop", "kind": "subagent", "directories": ["/w/shop"]},
                                 {"id": "x", "timestamp": "t", "type": "user", "content": "Find the app factory"}]), "sub-9.jsonl")
    assert sc.meta["subagent"] and sc.meta["parent_session_id"] == "s-1" and sc.meta["cwd"] == "/w/shop"
    assert sc.title == "Sub-agent: Find the app factory"
    checkpoint = {"history": [
        {"role": "user", "parts": [{"text": "plan the migration"}]},
        {"role": "model", "parts": [{"text": "**Schema** first", "thought": True},
                                    {"functionCall": {"name": "read_file", "args": {"file_path": "a.sql"}}},
                                    {"functionCall": {"name": "glob", "args": {"pattern": "*.sql"}}}]},
        {"role": "user", "parts": [{"functionResponse": {"name": "glob", "response": {"output": "a.sql"}}},
                                   {"functionResponse": {"name": "read_file", "response": {"output": "CREATE TABLE t"}}}]},
    ]}
    [cp] = GeminiCliParser().parse(json.dumps(checkpoint).encode(), "checkpoint-before%20migration.json")
    assert cp.title == "Saved chat: before migration" and cp.meta["checkpoint"] == "before migration"
    calls = {b[2]: b[3] for b in blocks(cp) if b[1] == "tool_call"}
    results = {b[2]: (b[3], b[4]) for b in blocks(cp) if b[1] == "tool_result"}
    # results without ids answer the open call of the same tool
    assert results == {"glob": (calls["glob"], "a.sql"), "read_file": (calls["read_file"], "CREATE TABLE t")}
    assert sniff_parser(rows_jsonl(GEMINI_ROWS), "x.jsonl") is GeminiCliParser


def test_gemini_upload_reads_the_project_root():
    session = rows_jsonl(GEMINI_ROWS[:1] + [{"id": "u", "timestamp": "t", "type": "user", "content": "hello"}])
    rep = read_zip(zip_bytes({
        "laptop/dev/gemini/tmp/shop/.project_root": "/srv/projects/shop\n",
        "laptop/dev/gemini/tmp/shop/chats/session-1.jsonl": session,
        "laptop/dev/gemini/tmp/shop/chats/session-2.jsonl.tmp-4242": session,  # half-written copy: not a transcript
    }), "ds")
    assert not rep.warnings
    [conv] = rep.conversations
    assert (conv.host, conv.user, conv.harness) == ("laptop", "dev", "gemini")
    assert conv.meta["cwd"] == "/srv/projects/shop"


# ---------------------------------------------------------------- Antigravity
BRAIN = [
    {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "status": "DONE", "created_at": "2026-05-20T21:41:49Z",
     "content": "<USER_REQUEST>\nlist the files and run the tests\n</USER_REQUEST>\n"
                "<ADDITIONAL_METADATA>\nActive Document: /w/p/README.md\n</ADDITIONAL_METADATA>"},
    {"step_index": 1, "source": "SYSTEM", "type": "CONVERSATION_HISTORY", "status": "DONE", "created_at": "2026-05-20T21:41:50Z"},
    {"step_index": 6, "source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-05-20T21:41:58Z",
     "content": "Three files; one test fails.", "thinking": "summarise"},
    {"step_index": 2, "source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-05-20T21:41:51Z",
     "thinking": "list, then test",
     "tool_calls": [{"name": "run_command", "args": {"CommandLine": "pytest -q", "Cwd": "/w/p", "toolAction": "Running"}},
                    {"name": "list_dir", "args": {"DirectoryPath": "/w/p", "toolSummary": "p"}}]},
    # results arrive in the order the tools finish, named after the tool
    {"step_index": 3, "source": "MODEL", "type": "LIST_DIRECTORY", "status": "DONE", "created_at": "2026-05-20T21:41:52Z",
     "content": "README.md\napp.py\ntest_app.py"},
    {"step_index": 4, "source": "MODEL", "type": "RUN_COMMAND", "status": "ERROR", "created_at": "2026-05-20T21:41:55Z",
     "content": "1 failed"},
    {"step_index": 5, "source": "MODEL", "type": "SEARCH_WEB", "status": "DONE", "created_at": "2026-05-20T21:41:56Z",
     "content": "a result whose call was not logged"},
]
AG_ID = "0c1d2e3f-aaaa-bbbb-cccc-111122223333"


def test_antigravity_brain_transcript():
    p = AntigravityParser()
    p.source_path = f"h/u/antigravity/antigravity-cli/brain/{AG_ID}/.system_generated/logs/transcript_full.jsonl"
    [conv] = p.parse(rows_jsonl(BRAIN), "transcript_full.jsonl")
    got = blocks(conv)
    assert got[0] == ("user", "text", None, None, "list the files and run the tests", False, None)
    assert got[1][:2] == ("system", "text") and got[1][6] == "additional_metadata"
    calls = [b for b in got if b[1] == "tool_call"]
    assert [json.loads(b[4]) for b in calls] == [{"CommandLine": "pytest -q", "Cwd": "/w/p"}, {"DirectoryPath": "/w/p"}]
    results = [(b[2], b[3], b[4], b[5]) for b in got if b[1] == "tool_result"]
    assert results == [("list_dir", calls[1][3], "README.md\napp.py\ntest_app.py", False),
                       ("run_command", calls[0][3], "1 failed", True),
                       ("search_web", None, "a result whose call was not logged", False)]
    assert got[-1][4] == "Three files; one test fails."
    assert conv.meta == {"conversation_id": AG_ID, "app": "Antigravity CLI", "cwd": "/w/p", "project": "p"}
    assert conv.started_at == "2026-05-20T21:41:49Z"
    assert sniff_parser(rows_jsonl(BRAIN), "transcript.jsonl") is AntigravityParser


def test_antigravity_upload_reads_the_fullest_transcript_and_the_prompt_log():
    logs = f"studio/noor/antigravity/antigravity-cli/brain/{AG_ID}/.system_generated/logs"
    history = json.dumps({"display": "list the files", "timestamp": 1, "workspace": "file:///Users/noor/code/p",
                          "conversationId": AG_ID}) + "\n"
    ide = "9a8b7c6d-5e4f-4a3b-9c2d-1e0f2a3b4c5d"
    rep = read_zip(zip_bytes({
        f"{logs}/transcript_full.jsonl": rows_jsonl(BRAIN),
        f"{logs}/transcript.jsonl": rows_jsonl(BRAIN[:2]),
        f"{logs}/overview.txt": rows_jsonl(BRAIN[:1]),
        f"studio/noor/antigravity/antigravity-cli/brain/{AG_ID}/task.md": "# Task\n- [x] list files\n",
        "studio/noor/antigravity/antigravity-cli/history.jsonl": history,
        # a zip of ~/.gemini itself: the transcript is Antigravity's although the folder is Gemini's
        f".gemini/antigravity/brain/{ide}/.system_generated/logs/overview.txt": rows_jsonl(BRAIN[:1]),
    }), "ds")
    assert not rep.warnings
    convs = {c.source_path.rsplit("/", 1)[-1]: c for c in rep.conversations}
    assert set(convs) == {"transcript_full.jsonl", "overview.txt"}
    cli, ide_conv = convs["transcript_full.jsonl"], convs["overview.txt"]
    assert (cli.host, cli.user, cli.harness) == ("studio", "noor", "antigravity")
    assert cli.meta["cwd"] == "/Users/noor/code/p" and cli.meta["app"] == "Antigravity CLI"
    assert ide_conv.harness == "antigravity" and ide_conv.meta == {"conversation_id": ide, "app": "Antigravity IDE",
                                                                   "dataset": "ds"}


def test_antigravity_api_trajectory():
    traj = {"trajectory": {"cascadeId": "c1", "steps": [
        {"type": "CORTEX_STEP_TYPE_USER_INPUT", "metadata": {"createdAt": "2026-05-21T10:00:00Z"},
         "userInput": {"items": [{"item": {"text": "fix the import"}}]}},
        {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "metadata": {"createdAt": "2026-05-21T10:00:02Z"},
         "plannerResponse": {"thinking": "edit then test", "toolCalls": [
             {"id": "e1", "name": "replace_file_content", "argumentsJson": json.dumps({"TargetFile": "/w/p/app.py"})},
             {"id": "r1", "name": "run_command", "argumentsJson": json.dumps({"CommandLine": "pytest", "Cwd": "/w/p"})}]}},
        {"type": "CORTEX_STEP_TYPE_RUN_COMMAND", "metadata": {"createdAt": "2026-05-21T10:00:05Z", "executionId": "r1"},
         "runCommand": {"commandLine": "pytest", "combinedOutput": {"full": "2 passed"}, "exitCode": 0}},
        {"type": "CORTEX_STEP_TYPE_CODE_ACTION", "metadata": {"createdAt": "2026-05-21T10:00:04Z", "executionId": "e1"},
         "codeAction": {"actionResult": {"edit": {"diff": {"unifiedDiff": {"lines": [
             {"text": "import os", "type": "UNIFIED_DIFF_LINE_TYPE_DELETE"},
             {"text": "import sys", "type": "UNIFIED_DIFF_LINE_TYPE_INSERT"}]}}}}}},
        {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "metadata": {"createdAt": "2026-05-21T10:00:07Z"},
         "plannerResponse": {"response": "Fixed."}},
    ]}, "generatorMetadata": []}
    [conv] = AntigravityParser().parse(json.dumps(traj).encode(), "fix-import.json")
    got = blocks(conv)
    assert got[0][:5] == ("user", "text", None, None, "fix the import")
    results = {b[3]: b[4] for b in got if b[1] == "tool_result"}
    assert results == {"r1": "2 passed", "e1": "-import os\n+import sys"}
    assert got[-1][4] == "Fixed." and conv.meta["cwd"] == "/w/p"
    assert sniff_parser(json.dumps(traj).encode(), "x.json") is AntigravityParser


def test_routing():
    assert get_parser("gemini-cli") is GeminiCliParser and get_parser("google-antigravity") is AntigravityParser
    assert AntigravityParser.superseded_by("a/brain/x/.system_generated/logs/transcript.jsonl") == (
        "a/brain/x/.system_generated/logs/transcript_full.jsonl",)
    assert not AntigravityParser.owns("a/brain/x/notes/transcript.jsonl")
    assert AntigravityParser().parse(b'{"display": "hi", "conversationId": "x"}\n', "history.jsonl") == []


def test_security_reads_antigravity_command_arguments():
    from synthsift.nlp.security import command_text
    # Antigravity capitalises its argument names; the working directory is not part of the command
    assert command_text({"CommandLine": "pytest -q", "Cwd": "/w/p"}, "run_command") == "pytest -q"
    # what write_to_file writes is data, not a command
    assert command_text({"TargetFile": "/w/a.py", "CodeContent": "import os"}, "write_to_file") == "/w/a.py"
