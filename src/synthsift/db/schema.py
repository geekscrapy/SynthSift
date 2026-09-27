"""Core tables: the corpus itself.  Enrichment modules declare their own."""

from __future__ import annotations

from .base import table

DATASETS = table("datasets", "id", "name", ("uploaded_at", "float"), ("size", "bigint"), ("files", "int"),
                 ("conversations", "int"), ("warnings", "json"), ("ingested", "bool"),
                 description="Uploaded zips (the zip files stay in <data dir>/uploads)")
CONVERSATIONS = table("conversations", "id", "dataset", ("ord", "int"), "host", "user", "harness", "session",
                      ("doc", "json"), index=["dataset"], description="Parsed conversations (full document as JSON)")
EVENTS = table("events", "id", "conv", ("seq", "int"), "type", "label", "tool_name", "timestamp", ("doc", "json"),
               index=["conv"], description="Turns: messages, thoughts, tool calls and results")
PARAGRAPHS = table("paragraphs", "id", "conv", "event", ("seq", "int"), "hash", index=["hash"],
                   description="Paragraphs of each turn; text lives in para_text under the content hash")
PARA_TEXT = table("para_text", "hash", "text", "role", ("code", "bool"), "arg", index=["hash"],
                  description="Distinct paragraph contents; every module result is keyed by this hash")
MODULE_DONE = table("module_done", "module", "para_hash", index=["module"],
                    description="Which paragraphs each paragraph-scope module has processed")

CORE = (DATASETS, CONVERSATIONS, EVENTS, PARAGRAPHS, PARA_TEXT, MODULE_DONE)
