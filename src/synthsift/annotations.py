"""Analyst annotations: tags and comments on sessions, turns and terms.

Targets are addressed by a string id:

* ``conv:<conversation id>`` – a whole session / conversation
* ``event:<event id>``       – one conversation turn (user, LLM, thought, tool call, result)
* ``term:<entity node id>``  – an extracted term (entity), across the corpus

Ids are derived from the uploaded file path and content, so annotations survive
re-analysis after settings changes. Each annotation keeps a small snapshot
(label, conversation, timestamp) so the timeline can list it even when the
target is filtered out of the current graph.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

BUILTIN_TAGS: list[dict[str, str]] = [
    {"name": "bad", "color": "#D93025", "icon": "dangerous"},
    {"name": "suspicious", "color": "#E37400", "icon": "report"},
    {"name": "seen", "color": "#1E8E3E", "icon": "visibility"},
    {"name": "ignore", "color": "#80868B", "icon": "do_not_disturb_on"},
]
BUILTIN_NAMES = {t["name"] for t in BUILTIN_TAGS}
Kind = Literal["conv", "event", "term"]


def _clean_tag(name: str) -> str:
    return " ".join(str(name).strip().lower().split())[:40]


class Annotation(BaseModel):
    target: str
    kind: Kind
    tags: list[str] = Field(default_factory=list)
    comment: str = ""
    label: str = ""          # snapshot of what was tagged, for the timeline
    conv: str | None = None  # owning conversation (terms may span several)
    ts: str | None = None    # event timestamp, when known
    updated: float = Field(default_factory=time.time)

    @field_validator("tags")
    @classmethod
    def _norm_tags(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for t in v:
            t = _clean_tag(t)
            if t and t not in out:
                out.append(t)
        return out

    @field_validator("comment")
    @classmethod
    def _limit_comment(cls, v: str) -> str:
        return v[:5000]

    @property
    def empty(self) -> bool:
        return not self.tags and not self.comment.strip()


class CustomTag(BaseModel):
    name: str
    color: str = "#5F6368"

    @field_validator("name")
    @classmethod
    def _norm(cls, v: str) -> str:
        v = _clean_tag(v)
        if not v:
            raise ValueError("tag name is empty")
        return v


class AnnotationFile(BaseModel):
    annotations: dict[str, Annotation] = Field(default_factory=dict)
    custom_tags: list[CustomTag] = Field(default_factory=list)


def kind_of(target: str) -> Kind:
    prefix = target.split(":", 1)[0]
    if prefix not in ("conv", "event", "term"):
        raise ValueError(f"unknown annotation target {target!r}")
    return prefix  # type: ignore[return-value]


class AnnotationStore:
    def __init__(self, path: Path | None):
        self.path = path
        self._lock = threading.Lock()
        self.data = AnnotationFile()
        if path and path.exists():
            try:
                self.data = AnnotationFile.model_validate_json(path.read_text())
            except (OSError, ValueError):
                pass

    # ------------------------------------------------------------- queries
    def tags(self) -> list[dict[str, str]]:
        return [*BUILTIN_TAGS, *({"name": t.name, "color": t.color, "icon": "sell"} for t in self.data.custom_tags)]

    def to_json(self) -> dict:
        return {
            "annotations": {k: v.model_dump() for k, v in self.data.annotations.items()},
            "tags": self.tags(),
        }

    # ------------------------------------------------------------ mutation
    def upsert(self, target: str, tags: list[str], comment: str = "", label: str = "",
               conv: str | None = None, ts: str | None = None) -> Annotation | None:
        ann = Annotation(target=target, kind=kind_of(target), tags=tags, comment=comment, label=label,
                         conv=conv, ts=ts)
        with self._lock:
            known = {t["name"] for t in self.tags()}
            for t in ann.tags:  # a new tag typed in the UI becomes a custom tag
                if t not in known:
                    self.data.custom_tags.append(CustomTag(name=t))
                    known.add(t)
            if ann.empty:
                self.data.annotations.pop(target, None)
                result = None
            else:
                prev = self.data.annotations.get(target)
                if prev and not label:
                    ann.label = prev.label
                if prev and conv is None:
                    ann.conv = prev.conv
                if prev and ts is None:
                    ann.ts = prev.ts
                self.data.annotations[target] = ann
                result = ann
            self._save()
        return result

    def delete(self, target: str) -> bool:
        with self._lock:
            found = self.data.annotations.pop(target, None) is not None
            self._save()
        return found

    def add_tag(self, name: str, color: str = "#5F6368") -> CustomTag:
        tag = CustomTag(name=name, color=color)
        with self._lock:
            if tag.name not in BUILTIN_NAMES:
                self.data.custom_tags = [t for t in self.data.custom_tags if t.name != tag.name] + [tag]
                self._save()
        return tag

    def remove_tag(self, name: str) -> None:
        name = _clean_tag(name)
        with self._lock:
            self.data.custom_tags = [t for t in self.data.custom_tags if t.name != name]
            for key, ann in list(self.data.annotations.items()):
                if name in ann.tags:
                    ann.tags = [t for t in ann.tags if t != name]
                    if ann.empty:
                        del self.data.annotations[key]
            self._save()

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(self.data.model_dump_json(indent=2))
        os.replace(tmp, self.path)  # atomic on POSIX and Windows

    def export(self) -> str:
        return json.dumps(self.to_json(), indent=2)
