from synthsift.ingest import locate, read_zip

CONV = {"messages": [{"role": "user", "content": "Hello there, can you check /var/log/syslog?"},
                     {"role": "assistant", "content": "Sure."}]}


def test_locate_standard_layout():
    assert locate("laptop/alice/example/transcript1-xyz.json") == ("laptop", "alice", "example", "transcript1-xyz.json")


def test_locate_ignores_wrapper_folders_and_keeps_subfolders():
    assert locate("export/laptop/alice/claude-code/proj/a.jsonl") == ("laptop", "alice", "claude-code", "proj/a.jsonl")


def test_locate_unknown_harness_is_positional():
    assert locate("h/u/mystery/t.json") == ("h", "u", "mystery", "t.json")
    assert locate("t.json") == ("unknown-host", "unknown-user", None, "t.json")


def test_read_zip_routes_by_folder(zip_of):
    rep = read_zip(zip_of({
        "h1/u1/example/a.json": CONV,
        "h1/u2/openai/b.json": CONV,
        "__MACOSX/h1/u1/example/._a.json": "junk",
        "h1/u1/example/notes.txt": "ignored",
    }), "ds")
    assert [(c.host, c.user, c.harness, c.session) for c in rep.conversations] == [
        ("h1", "u1", "example", "a.json"), ("h1", "u2", "example", "b.json")]
    assert rep.skipped == 1 and not rep.warnings
    assert len({c.id for c in rep.conversations}) == 2


def test_read_zip_reports_stub_harnesses_and_bad_files(zip_of):
    rep = read_zip(zip_of({
        "h/u/hermes/s.jsonl": "{}",
        "h/u/gemini/s.json": "{}",
        "h/u/example/broken.json": "{not json",
        "h/u/whatever/sniffed.json": {"format": "synthsift.example/v1", **CONV},
    }), "ds")
    assert [c.session for c in rep.conversations] == ["sniffed.json"]
    text = "\n".join(rep.warnings)
    assert "hermes parser is a placeholder" in text
    assert "gemini parser is a placeholder" in text
    assert "broken.json" in text


def test_read_zip_rejects_non_zip():
    rep = read_zip(b"hello", "ds")
    assert rep.warnings and not rep.conversations
