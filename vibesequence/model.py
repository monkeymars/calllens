"""Data model for vibesequence.

Everything downstream (resolver, flow builder, zoom projection, renderers)
works only on these structures, so a new language extractor only has to
produce them. The model is deliberately language-agnostic.

Hierarchy used for zoom levels (coarse -> fine):

    package  >  module  >  class  >  function

A FunctionDef carries its full path so any zoom level can be derived by
truncating the path (see zoom.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class CallSite:
    """One call expression found inside a function body, in source order."""
    expr: str                  # textual callee as written: "repo.save", "self.validate", "notify"
    line: int
    order: int                 # position among calls in the enclosing body
    context: list[str]         # control-flow stack, e.g. ["if", "for"]; keeps alt/loop info
    resolved: Optional[str] = None   # FunctionDef.id after resolution, None = external/unknown
    # receiver class when dispatch differs from the defining class (inheritance):
    # self.orders.get -> resolved BaseRepo.get, receiver "app.repos.order_repo:OrderRepo"
    receiver: Optional[str] = None
    # for unresolved calls: "lib" (imported name: requests.post), "global" (module-level
    # object: _conn.execute), "local" (local var / attribute: resp.json, self.x.y)
    ext_kind: Optional[str] = None


@dataclass
class FunctionDef:
    id: str                    # stable id: "pkg.module:Class.method" or "pkg.module:func"
    name: str                  # "method"
    module: str                # "pkg.module"
    cls: Optional[str]         # "Class" or None
    file: str                  # relative path
    line: int
    end_line: int
    decorators: list[str] = field(default_factory=list)
    is_async: bool = False
    calls: list[CallSite] = field(default_factory=list)
    # local variable / parameter -> type name, e.g. "svc" -> "OrderService"
    local_hints: dict[str, str] = field(default_factory=dict)

    @property
    def package(self) -> str:
        return self.module.split(".")[0]

    @property
    def qualname(self) -> str:
        return f"{self.cls}.{self.name}" if self.cls else self.name


@dataclass
class ModuleFacts:
    """Raw output of an extractor for one source file."""
    module: str
    file: str
    functions: list[FunctionDef]
    # import map: local name -> fully-qualified target ("Repo" -> "app.repos.order_repo.OrderRepo")
    imports: dict[str, str]
    # attributes assigned at module/class level that are instances, e.g. "repo" -> "OrderRepo"
    instance_hints: dict[str, str]
    # class name -> base class names as written ("BaseRepo", "models.Model")
    bases: dict[str, list[str]] = field(default_factory=dict)
    # names assigned at module level (globals), e.g. ["router", "_conn"]
    globals: list[str] = field(default_factory=list)
    # exported name -> local name: {"default": "router"} (JS default export), {"b": "a"} (export { a as b })
    aliases: dict[str, str] = field(default_factory=dict)


@dataclass
class EntryPoint:
    id: str                    # FunctionDef.id
    kind: str                  # "route" | "main" | "cli" | "handler" | "root"
    label: str                 # human label e.g. "POST /orders"
    score: float = 0.0         # how much of your code it drives (reach, penalised if private/nested)


@dataclass
class Graph:
    functions: dict[str, FunctionDef]
    entries: list[EntryPoint]
    stats: dict

    def to_json(self) -> dict:
        return {
            "functions": {k: asdict(v) for k, v in self.functions.items()},
            "entries": [asdict(e) for e in self.entries],
            "stats": self.stats,
        }
