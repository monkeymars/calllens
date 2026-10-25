"""Message tree -> Mermaid `sequenceDiagram` text.

Mermaid text is the lingua franca here: the HTML viewer renders it, and an
AI coding assistant can READ it directly (it is ~50 tokens for a flow that
would cost thousands of tokens of raw source).

Control-flow fragments: consecutive sibling messages that share the same
leading context tag are wrapped together:
    if/else -> alt if <cond> ... else ... end
    loop    -> loop ... end
    try     -> opt try ... end,  except -> opt on error ... end
"""
from __future__ import annotations

from ..flow import Message, display_name

FRAGMENT = {"loop": "loop", "if": "alt", "else": "else", "try": "opt", "except": "opt",
            "with": "opt", "finally": "opt"}
FRAGMENT_LABEL = {"if": "if", "else": "else", "loop": "for each", "try": "try",
                  "except": "on error", "with": "with", "finally": "finally"}


def fragment_label(tag: str) -> str:
    """'if:not user' -> 'if not user', 'loop:for x in xs' -> 'for x in xs', 'except:E' -> 'on error E'."""
    kind, _, text = tag.partition(":")
    if not text:
        return FRAGMENT_LABEL.get(kind, kind)
    return text if kind == "loop" else f"{FRAGMENT_LABEL.get(kind, kind)} {text}"


def _esc(text: str) -> str:
    return text.replace(";", "#semi;").replace("\n", " ")


def render(root: Message, level: str, actor: str = "User", show_returns: bool = False,
           max_messages: int = 150) -> str:
    participants: dict[str, str] = {}      # participant key -> mermaid alias
    lines: list[str] = ["sequenceDiagram", "    autonumber"]
    budget = [max_messages]

    def alias(p: str) -> str:
        if p not in participants:
            participants[p] = f"p{len(participants)}"
        return participants[p]

    body: list[str] = []

    def emit_msg(m: Message, indent: str):
        if budget[0] <= 0:
            return
        budget[0] -= 1
        a, b = alias(m.src), alias(m.dst)
        label = m.label + (f" ×{m.count}" if m.count > 1 else "")
        if m.truncated == "cycle":
            label += " ↺"
        elif m.truncated == "depth":
            label += " …"
        body.append(f"{indent}{a}->>{b}: {_esc(label)}")
        if m.children:
            body.append(f"{indent}activate {b}")
            emit_group(m.children, indent + "    ", depth=0)
            if show_returns:
                body.append(f"{indent}{b}-->>{a}: return")
            body.append(f"{indent}deactivate {b}")

    def emit_group(msgs: list[Message], indent: str, depth: int):
        """Group consecutive siblings by context[depth] into fragments."""
        i = 0
        while i < len(msgs):
            m = msgs[i]
            tag = m.context[depth] if len(m.context) > depth else None
            if tag is None:
                emit_msg(m, indent)
                i += 1
                continue
            j = i
            while j < len(msgs) and len(msgs[j].context) > depth and msgs[j].context[depth] == tag:
                j += 1
            group = msgs[i:j]
            kind = FRAGMENT.get(tag.partition(":")[0], "opt")
            has_else = j < len(msgs) and len(msgs[j].context) > depth and msgs[j].context[depth] == "else"
            if kind == "alt" and not has_else:
                kind = "opt"
            # `else` directly after `alt` continues the same block; an orphan else
            # (if-body had no calls, or for/try ... else) becomes its own opt block
            if tag == "else":
                if _last_block_is_alt(body, indent):
                    body.pop()                               # remove "end" of the alt
                    body.append(f"{indent}else else")
                else:
                    body.append(f"{indent}opt else")
            else:
                body.append(f"{indent}{kind} {_esc(fragment_label(tag))}")
            emit_group(group, indent + "    ", depth + 1)
            body.append(f"{indent}end")
            i = j

    alias(actor)
    emit_msg(root, "    ")

    for p, al in participants.items():
        kind = "actor" if p == actor else "participant"
        name = display_name(p, level) if p != actor else actor
        lines.append(f"    {kind} {al} as {_esc(name).replace(':', '.')}")
    lines.extend(body)
    if budget[0] <= 0:
        lines.append(f"    Note over {participants[actor]}: diagram truncated at {max_messages} messages, zoom in for more")
    return "\n".join(lines)


def _last_block_is_alt(body: list[str], indent: str) -> bool:
    if not body or body[-1] != f"{indent}end":
        return False
    depth = 0
    for line in reversed(body):
        s = line.strip()
        if s == "end":
            depth += 1
        elif s.split(" ")[0] in ("alt", "loop", "opt", "par", "critical"):
            depth -= 1
            if depth == 0:
                return s.startswith("alt") and line.startswith(indent + "alt")
    return False
