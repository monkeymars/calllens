"""Entry point detection.

A "user sequence" needs a starting actor. We treat these as flow roots:

  * decorated HTTP routes (Flask / FastAPI / Django-ish / Starlette)    -> kind "route"
  * CLI commands (click / typer / argparse subcommands)                 -> kind "cli"
  * event / message handlers (celery task, on_*, handle_*)               -> kind "handler"
  * `main`                                                               -> kind "main"
  * functions nobody calls (in-degree 0), excluding tests & dunder       -> kind "root"

Ordering above is also priority: a route beats a generic root. Roots are then ranked by score:
how many of your functions they reach, scaled down for private (`_x`), nested (`f.inner`) and
private-module functions, so `run()` driving 40 functions sorts above a 2-line `_callback`.
"""
from __future__ import annotations

import re
from collections import Counter

from .model import EntryPoint, FunctionDef

ROUTE_RE = re.compile(r"(?:^|\.)(route|get|post|put|patch|delete|api_route|websocket)\(")
CLI_RE = re.compile(r"(?:^|\.)(command|group)(\(|$)")
TASK_RE = re.compile(r"(?:^|\.)(task|shared_task|on_event|listener|subscribe|consumer)(\(|$)")
HTTP_VERB = {"get", "post", "put", "patch", "delete"}


def _route_label(fn: FunctionDef, deco: str) -> str:
    m = re.search(r"\.(\w+)\(['\"](.+?)['\"]\)", deco)
    if m:
        verb, path = m.group(1), m.group(2)
        verb = verb.upper() if verb in HTTP_VERB else "ROUTE"
        return f"{verb} {path}"
    return fn.qualname


def _reach(functions: dict[str, FunctionDef], fid: str, limit: int = 1000) -> int:
    seen, stack = {fid}, [fid]
    while stack and len(seen) < limit:
        for c in functions[stack.pop()].calls:
            if c.resolved in functions and c.resolved not in seen:
                seen.add(c.resolved)
                stack.append(c.resolved)
    return len(seen) - 1


def _score(functions: dict[str, FunctionDef], fn: FunctionDef) -> float:
    score = _reach(functions, fn.id) + len(fn.calls) / 100          # calls: tie-break between leaves
    if fn.name.split(".")[-1].startswith("_"):
        score *= 0.3
    if "." in fn.name:                                              # nested def / callback
        score *= 0.2
    if any(part.startswith("_") and not part.startswith("__") for part in fn.module.split(".")):
        score *= 0.5
    return round(score, 2)


def detect(functions: dict[str, FunctionDef]) -> list[EntryPoint]:
    indegree: Counter[str] = Counter()
    for fn in functions.values():
        for c in fn.calls:
            if c.resolved:
                indegree[c.resolved] += 1

    entries: list[EntryPoint] = []
    seen: set[str] = set()

    def add(fn: FunctionDef, kind: str, label: str):
        if fn.id not in seen:
            seen.add(fn.id)
            entries.append(EntryPoint(id=fn.id, kind=kind, label=label))

    for fn in functions.values():
        for d in fn.decorators:
            if ROUTE_RE.search(d + "(") and not d.startswith("property"):
                add(fn, "route", _route_label(fn, d))
                break
            if CLI_RE.search(d):
                add(fn, "cli", f"cli {fn.name}")
                break
            if TASK_RE.search(d):
                add(fn, "handler", f"task {fn.qualname}")
                break
        else:
            if fn.name == "main" and fn.cls is None:
                add(fn, "main", "main()")
            elif re.match(r"^(on_|handle_)", fn.name):
                add(fn, "handler", fn.qualname)

    for fn in functions.values():
        if fn.id in seen or indegree[fn.id]:
            continue
        if fn.name.startswith("__") or fn.name.startswith("test_") or \
                {"test", "tests", "__tests__", "spec"} & set(fn.module.split(".")):
            continue
        if not fn.calls:                       # leaf with no callers = dead or util, not a flow
            continue
        if any(d.split("(")[0].split(".")[-1] in ("property", "cached_property", "setter") for d in fn.decorators):
            continue
        add(fn, "root", fn.qualname)

    for e in entries:
        e.score = _score(functions, functions[e.id])
    kind_rank = {"route": 0, "cli": 1, "handler": 2, "main": 3, "root": 4}
    entries.sort(key=lambda e: (kind_rank[e.kind], -e.score if e.kind == "root" else 0, e.label))
    return entries
