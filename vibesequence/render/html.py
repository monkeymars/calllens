"""Write the self-contained interactive viewer (vibesequence-out/index.html).

The viewer embeds graph.json and re-implements expand/project/render in
JavaScript so zooming is instant and needs no server. Mermaid is loaded from
cdnjs; everything else is inline.
"""
from __future__ import annotations

import html as htmllib
import json
from pathlib import Path

from .. import editor
from ..model import Graph

TEMPLATE = Path(__file__).with_name("viewer.html")


def write(graph: Graph, path: Path) -> None:
    s = graph.stats
    html = (TEMPLATE.read_text(encoding="utf-8")
            # every "<" escaped: "</script" and "<!--<script" in code strings cannot end or swallow the block
            .replace("__GRAPH_JSON__", json.dumps(graph.to_json()).replace("<", "\\u003c"))
            .replace("__PROJECT__", htmllib.escape(Path(s["root"]).name))
            .replace("__STATS__", f"{s['functions']} functions · {s['entries']} flows")
            .replace("__EDITOR__", editor.detect()[0]))
    path.write_text(html, encoding="utf-8")
