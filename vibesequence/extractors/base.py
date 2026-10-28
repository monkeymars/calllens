"""Extractor interface.

An extractor turns ONE source file into ModuleFacts. Scaling to other
languages means adding a class here (e.g. a tree-sitter based extractor for
TypeScript/Go) and registering it in `EXTRACTORS` by file extension. Nothing
else in the pipeline changes.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..model import ModuleFacts


class Extractor(Protocol):
    extensions: tuple[str, ...]

    def extract(self, path: Path, root: Path) -> ModuleFacts: ...


def get_extractors() -> dict[str, Extractor]:
    from .python_ast import PythonAstExtractor
    available: list[Extractor] = [PythonAstExtractor()]
    try:                                                  # optional: pip install vibesequence[ts]
        from .typescript_ts import TypeScriptExtractor
        available.append(TypeScriptExtractor())
    except ImportError:
        pass
    try:                                                  # optional: pip install vibesequence[go]
        from .go_ts import GoExtractor
        available.append(GoExtractor())
    except ImportError:
        pass
    table: dict[str, Extractor] = {}
    for ex in available:
        for ext in ex.extensions:
            table[ext] = ex
    return table
