"""Enrichment modules.  Every module in this package is imported on load so
its ``@register`` decorator runs – drop in a file to add a module."""

import importlib
import pkgutil

from .base import (  # noqa: F401
    Deps,
    enabled_modules,
    Module,
    ParaIn,
    para_hash,
    register,
    registry,
    span_table,
    switch_field,
)

for _mod in pkgutil.iter_modules(__path__):
    if _mod.name not in ("base", "runner"):
        importlib.import_module(f"{__name__}.{_mod.name}")
