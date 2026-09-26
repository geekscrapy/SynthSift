import json

import pytest

from synthsift.harnesses import ParserNotImplemented, all_parsers, get_parser, sniff_parser
from synthsift.harnesses.example import ExampleParser

STUBS = ["gemini", "antigravity", "hermes"]


def test_registry_has_example_and_stubs():
    names = {p.name: p for p in all_parsers()}
    for ready in ("example", "claude_code", "openclaw"):
        assert names[ready].implemented
    for stub in STUBS:
        assert stub in names and not names[stub].implemented


@pytest.mark.parametrize("folder,expected", [
    ("example", "example"), ("Claude-Code", "claude_code"), ("claudecode", "claude_code"),
    ("gemini-cli", "gemini"), ("OpenClaw", "openclaw"), ("hermes_agent", "hermes"), ("openai", "example"),
    (".claude", "claude_code"), (".openclaw", "openclaw"), ("clawdbot", "openclaw"),
])
def test_folder_aliases(folder, expected):
    assert get_parser(folder).name == expected


@pytest.mark.parametrize("stub", STUBS)
def test_stubs_raise(stub):
    with pytest.raises(ParserNotImplemented):
        get_parser(stub)().parse(b"{}", "x.json")


def test_example_canonical_format():
    doc = {
        "format": "synthsift.example/v1", "title": "T", "messages": [
            {"role": "user", "content": "read /etc/hosts"},
            {"role": "assistant", "content": [
                {"type": "thinking", "text": "use the tool"},
                {"type": "tool_call", "id": "c1", "name": "read_file", "arguments": {"path": "/etc/hosts"}},
            ]},
            {"role": "tool", "tool_call_id": "c1", "name": "read_file", "content": "127.0.0.1 localhost"},
        ],
    }
    [conv] = ExampleParser().parse(json.dumps(doc).encode(), "t.json")
    assert conv.title == "T"
    kinds = [(m.role, [b.kind for b in m.blocks]) for m in conv.messages]
    assert kinds == [("user", ["text"]), ("assistant", ["thinking", "tool_call"]), ("tool", ["tool_result"])]
    call = conv.messages[1].blocks[1]
    assert call.tool_name == "read_file" and call.arguments == {"path": "/etc/hosts"} and call.tool_call_id == "c1"


def test_example_reads_openai_and_anthropic_styles():
    openai = [
        {"role": "user", "content": "count rows"},
        {"role": "assistant", "content": None, "reasoning_content": "use sql",
         "tool_calls": [{"id": "q1", "type": "function", "function": {"name": "run_sql", "arguments": "{\"query\": \"SELECT 1\"}"}}]},
        {"role": "tool", "tool_call_id": "q1", "content": "1"},
    ]
    [conv] = ExampleParser().parse(json.dumps(openai).encode(), "o.json")
    blocks = conv.messages[1].blocks
    assert [b.kind for b in blocks] == ["thinking", "tool_call"]
    assert blocks[1].arguments == {"query": "SELECT 1"}

    anthropic = {"messages": [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": "a.txt"}]}]},
    ]}
    [conv] = ExampleParser().parse(json.dumps(anthropic).encode(), "a.json")
    assert conv.messages[1].role == "tool"
    assert conv.messages[1].blocks[0].text == "a.txt"


def test_example_jsonl_and_multi_conversation():
    rows = "\n".join(json.dumps(r) for r in [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}])
    [conv] = ExampleParser().parse(rows.encode(), "c.jsonl")
    assert len(conv.messages) == 2
    multi = {"conversations": [{"messages": [{"role": "user", "content": "a"}]}, {"messages": [{"role": "user", "content": "b"}]}]}
    assert len(ExampleParser().parse(json.dumps(multi).encode(), "m.json")) == 2


def test_sniff_recognises_example_format():
    assert sniff_parser(b'{"format": "synthsift.example/v1", "messages": []}', "x.json").name == "example"
    assert sniff_parser(b"not json at all", "x.json") is None
