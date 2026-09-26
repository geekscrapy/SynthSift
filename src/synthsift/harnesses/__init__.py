"""Harness parsers.  Every module in this package is imported on load so that
its ``@register`` decorator runs – drop in a new file to add a harness."""

import importlib
import pkgutil

from .base import (  # noqa: F401
    HarnessParser,
    ParserNotImplemented,
    all_parsers,
    get_parser,
    register,
    sniff_parser,
)

for _mod in pkgutil.iter_modules(__path__):
    if _mod.name != "base":
        importlib.import_module(f"{__name__}.{_mod.name}")
