"""Flow building and semantic zoom.

Two separate steps, on purpose:

  expand()   call graph  ->  call TREE rooted at an entry point
             (ordered DFS, bounded depth, cycle guard). A tree is what a
             sequence diagram is: time runs downward, nesting = activation.

  project()  call tree   ->  message list at a chosen ZOOM LEVEL
             Every function has a hierarchy path  package > module > class > function.
             A zoom level picks one rung as "the participant". Calls whose caller
             and callee fall into the same participant are *internal* and get
             flattened away (their children bubble up), so a module-level view
             of the same tree shows only the hand-offs BETWEEN modules.
             That is the whole zoom trick: one tree, many projections.

Both steps are pure functions over the Graph, so they run identically in the
CLI (Python) and in the HTML viewer (JavaScript port in render/html.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .model import FunctionDef, Graph

LEVELS = ("function", "class", "module", "package")


@dataclass
class Node:
    callee: str                 # FunctionDef.id, or "ext:<expr>" for unresolved
    label: str                  # qualified name shown on the arrow
    line: int
    context: list[str]
    children: list["Node"] = field(default_factory=list)
    truncated: str | None = None  # "depth" | "cycle" | None
    receiver: str | None = None   # "module:Class" the call was dispatched on
    ext_kind: str | None = None   # for externals: lib | global | local

    @property
    def external(self) -> bool:
        return self.callee.startswith("ext:")


def _own_cls(fn: FunctionDef) -> str | None:
    return f"{fn.module}:{fn.cls}" if fn.cls else None


def expand(graph: Graph, root_id: str, max_depth: int = 4) -> Node:
    """Ordered DFS from an entry point.

    `self_cls` is the DYNAMIC class of `self` while walking a method body: when
    Flask.handle_exception calls App._find_error_handler via `self`, inside that
    body `self` is still a Flask, so `self._get_exc_class_and_code` is shown as
    Flask -> Flask (internal), not Flask -> Scaffold. Static receiver hints from
    the resolver are used when the call is not through `self`.
    """
    fns = graph.functions

    def rec(fid: str, depth: int, path: frozenset[str], self_cls: str | None) -> Node:
        fn = fns[fid]
        node = Node(callee=fid, label=fn.qualname, line=fn.line, context=[])
        if depth >= max_depth:
            node.truncated = "depth"
            return node
        for call in fn.calls:
            if call.resolved and call.resolved in fns:
                target = fns[call.resolved]
                via_self = call.expr.startswith("self.") and call.expr.count(".") == 1
                if via_self and self_cls and _own_cls(target) != self_cls:
                    receiver: str | None = self_cls
                else:
                    receiver = call.receiver
                child_self = receiver or _own_cls(target)
                if call.resolved in path or call.resolved == fid:
                    child = Node(call.resolved, target.qualname, call.line, call.context, truncated="cycle")
                else:
                    child = rec(call.resolved, depth + 1, path | {fid}, child_self)
                    child.line, child.context = call.line, call.context
                child.receiver = receiver
            else:
                child = Node(f"ext:{call.expr}", call.expr, call.line, call.context, ext_kind=call.ext_kind)
            node.children.append(child)
        return node

    return rec(root_id, 0, frozenset(), _own_cls(fns[root_id]))


# ----------------------------------------------------------------------- zoom
def ext_split(expr: str) -> tuple[str, str]:
    """'self.products.get' -> ('products', 'get');  'requests.post' -> ('requests', 'post')
    'a.b().c' -> ('a', 'b().c')"""
    expr = expr.replace("()", "")
    parts = expr.split(".")
    if parts[0] == "self" and len(parts) >= 3:
        return parts[1], ".".join(parts[2:])
    if len(parts) == 1:
        return parts[0], parts[0]
    return parts[0], ".".join(parts[1:])


def participant_of(graph: Graph, callee: str, level: str, receiver: str | None = None) -> str:
    if callee.startswith("ext:"):
        return f"ext:{ext_split(callee[4:])[0]}"
    fn: FunctionDef = graph.functions[callee]
    if level == "function":
        return f"{fn.module}:{fn.qualname}"
    if level == "class":
        if receiver:                                  # inherited: show the receiving class
            return receiver
        return f"{fn.module}:{fn.cls}" if fn.cls else fn.module
    if receiver:
        fn_module = receiver.partition(":")[0]
    else:
        fn_module = fn.module
    if level == "module":
        return fn_module
    # package = everything but the last module segment ("app.services")
    return fn_module.rpartition(".")[0] or fn_module


def message_label(graph: Graph, node: Node, level: str) -> str:
    """What is written on the arrow at this zoom level."""
    if node.external:
        return ext_split(node.callee[4:])[1]
    fn = graph.functions[node.callee]
    if level in ("function", "class"):
        return fn.name
    if node.receiver:                               # inherited: UserRepo.get, not BaseRepo.get
        return f"{node.receiver.partition(':')[2]}.{fn.name}"
    return fn.qualname


# framework file names that say nothing on their own (Next.js app/api/orders/route.ts, index.ts):
# show them with their folder, "orders/route", or every route file is just "route"
GENERIC_FILES = {"route", "index", "page", "layout"}


def short_module(module: str) -> str:
    parts = module.split(".")
    return "/".join(parts[-2:]) if len(parts) > 1 and parts[-1] in GENERIC_FILES else parts[-1]


def display_name(participant: str, level: str) -> str:
    if participant.startswith("ext:"):
        return participant[4:]
    mod, _, qual = participant.partition(":")
    if level == "function":
        return qual
    if level == "class":
        return qual or short_module(mod)
    if level == "module":
        return short_module(participant)
    return participant


@dataclass
class Message:
    src: str
    dst: str
    label: str
    callee: str                 # original callee id, for zoom-in
    line: int
    context: list[str]
    children: list["Message"] = field(default_factory=list)
    truncated: str | None = None
    count: int = 1              # run-length collapsed duplicates


EXTERNAL_MODES = ("none", "lib", "global", "all")   # each includes the previous


def project(graph: Graph, root: Node, level: str, actor: str = "User",
            externals: str = "lib") -> Message:
    """externals: which unresolved calls to keep as participants.
         none   - only your own code
         lib    - + imported libraries (requests, sqlalchemy, HTTPException)   [default]
         global - + module-level objects (_conn.execute)
         all    - + calls on local variables (resp.json(), order.to_dict())"""
    allowed = set(EXTERNAL_MODES[1:EXTERNAL_MODES.index(externals) + 1])

    def rec(node: Node, parent_participant: str, inherited: list[str]) -> list[Message]:
        p = participant_of(graph, node.callee, level, node.receiver)
        ctx = inherited + node.context
        if node.external and (node.ext_kind or "local") not in allowed:
            return []
        if p == parent_participant:                  # internal hand-off -> flatten,
            out: list[Message] = []                  # children inherit its control-flow context
            for ch in node.children:
                out.extend(rec(ch, p, ctx))
            return out
        msg = Message(src=parent_participant, dst=p, label=message_label(graph, node, level),
                      callee=node.callee, line=node.line, context=ctx, truncated=node.truncated)
        for ch in node.children:
            msg.children.extend(rec(ch, p, []))
        msg.children = _collapse(msg.children)
        return [msg]

    root_participant = participant_of(graph, root.callee, level)
    top = Message(src=actor, dst=root_participant, label=message_label(graph, root, level), callee=root.callee,
                  line=root.line, context=[], truncated=root.truncated)
    for ch in root.children:
        top.children.extend(rec(ch, root_participant, []))
    top.children = _collapse(top.children)
    return top


def _collapse(msgs: list[Message]) -> list[Message]:
    """Merge consecutive identical leaf messages: 10x `log.debug` -> 1 arrow with x10."""
    out: list[Message] = []
    for m in msgs:
        prev = out[-1] if out else None
        if (prev and not prev.children and not m.children
                and (prev.src, prev.dst, prev.label, tuple(prev.context)) == (m.src, m.dst, m.label, tuple(m.context))):
            prev.count += 1
        else:
            out.append(m)
    return out


def count_messages(m: Message) -> int:
    return 1 + sum(count_messages(c) for c in m.children)
