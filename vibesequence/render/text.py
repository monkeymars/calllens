"""Message tree -> compact indented text, the format meant for AI assistants.

Same flow as the Mermaid output, minus what only a renderer needs (participant
aliases, activate/deactivate), plus what an agent needs to edit code without
grepping: the definition `file:line` of every call into your own code.

    OrderService.place  app/services/orders.py:20
      loop for item in items
        ProductRepo.get …  app/repos/base.py:12
      on error PaymentError
        OrderRepo.mark_failed  app/repos/order_repo.py:30
"""
from __future__ import annotations

from ..flow import Message, display_name, short_module
from ..model import Graph
from .mermaid import fragment_label


def _name(m: Message, level: str) -> str:
    dst = display_name(m.dst, level)
    if m.dst.startswith("ext:"):        # bare call `HTTPException()` -> once, not "HTTPException.HTTPException"
        return dst if dst == m.label else f"{dst}.{m.label}"
    if level == "class":
        return f"{dst}.{m.label}"
    if level == "function":             # free function: "db.execute", not a bare "execute"
        return dst if "." in dst else f"{short_module(m.dst.partition(':')[0])}.{dst}"
    return f"{dst}:{m.label}"           # module / package: "orders:OrderService.place"


def render(graph: Graph, root: Message, level: str, max_messages: int = 150, locations: bool = True) -> str:
    lines: list[str] = []
    budget = [max_messages]

    def emit_msg(m: Message, indent: str):
        if budget[0] <= 0:
            return
        budget[0] -= 1
        text = _name(m, level)
        if m.count > 1:
            text += f" ×{m.count}"
        text += {"cycle": " ↺", "depth": " …"}.get(m.truncated or "", "")
        fn = graph.functions.get(m.callee)
        if fn and locations:
            text += f"  {fn.file}:{fn.line}"
        lines.append(indent + text)
        emit_group(m.children, indent + "  ", 0)

    def emit_group(msgs: list[Message], indent: str, depth: int):
        i = 0
        while i < len(msgs):
            tag = msgs[i].context[depth] if len(msgs[i].context) > depth else None
            if tag is None:
                emit_msg(msgs[i], indent)
                i += 1
                continue
            j = i
            while j < len(msgs) and len(msgs[j].context) > depth and msgs[j].context[depth] == tag:
                j += 1
            lines.append(indent + fragment_label(tag))
            emit_group(msgs[i:j], indent + "  ", depth + 1)
            i = j

    emit_msg(root, "")
    if budget[0] <= 0:
        lines.append(f"(truncated at {max_messages} messages, zoom in with `show <callee>`)")
    return "\n".join(lines)
