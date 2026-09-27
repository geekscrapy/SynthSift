"""The settings field type, shared by :mod:`synthsift.settings` and the
enrichment modules (which declare their own options)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Field:
    key: str
    label: str
    type: str  # bool | int | float | select | multiselect | text | textarea | color | lists
    default: Any
    scope: str  # segment | parse | graph | view
    section: str
    help: str = ""
    options: list[Any] = field(default_factory=list)
    min: float | None = None
    max: float | None = None
    step: float | None = None
    #: enrichment module this option belongs to (shown in that module's card)
    module: str = ""
