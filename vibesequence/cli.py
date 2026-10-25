"""vibesequence CLI

  vibesequence build <path>                 parse repo -> vibesequence-out/{graph.json, index.html, FLOWS.md}
  vibesequence build <path> --hook          same, then print what flows changed as Claude Code hook JSON
  vibesequence list [--all]                 list entry points (flows); --all includes uncalled helpers
  vibesequence show <entry> [--level L] [--depth N]   print Mermaid for one flow
  vibesequence show <entry> --format text   compact tree with file:line (cheapest for an AI assistant)
  vibesequence show <entry> --json          same, as a message tree (for tooling)
  vibesequence callers <function>           direct callers + every flow that reaches it (impact of a change)
  vibesequence diff [<entry>]               how flows changed in the last build that changed anything
  vibesequence serve [--port 8765]          viewer over http; source links open files via your editor's CLI

`list`, `show`, `callers` rebuild incrementally first, so they never read a stale graph.

`show` is the piece an AI assistant uses: it reads the Mermaid text instead
of grepping files, then zooms in by calling `show` again on a deeper callee.
"""
from __future__ import annotations

import argparse
import difflib
import json
import shutil
import signal
import sys
from dataclasses import asdict
from pathlib import Path

from . import builder
from .flow import EXTERNAL_MODES, LEVELS, count_messages, expand, project
from .render import html as html_render
from .render.mermaid import render as render_mermaid
from .render.text import render as render_text


def _find_entry(graph, needle: str) -> str:
    needle = needle.strip()
    if needle in graph.functions:
        return needle
    hits = [e.id for e in graph.entries if needle.lower() == e.label.lower()]
    if not hits:
        hits = [e.id for e in graph.entries if needle.lower() in (e.label + " " + e.id).lower()]
    if not hits:
        hits = [fid for fid in graph.functions if needle.lower() in fid.lower()]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        sys.exit(f"no flow or function matches {needle!r}; run `vibesequence list`")
    sys.exit("ambiguous, pick one:\n  " + "\n  ".join(hits[:20]))


def main(argv=None):
    if hasattr(signal, "SIGPIPE"):                 # `vibesequence list | head`: exit quietly, no BrokenPipeError
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    ap = argparse.ArgumentParser(prog="vibesequence")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build")
    b.add_argument("path", nargs="?", default=".")
    b.add_argument("--out", default="vibesequence-out")
    b.add_argument("--no-cache", action="store_true")
    b.add_argument("--hook", action="store_true",
                   help="print changed flows as Claude Code PostToolUse hook JSON (silent if nothing changed)")

    ls = sub.add_parser("list")
    ls.add_argument("--out", default="vibesequence-out")
    ls.add_argument("--all", action="store_true", help="include `root` entries (functions nobody calls)")

    sh = sub.add_parser("show")
    sh.add_argument("entry")
    sh.add_argument("--out", default="vibesequence-out")
    sh.add_argument("--level", choices=LEVELS, default="class")
    sh.add_argument("--depth", type=int, default=4)
    sh.add_argument("--externals", choices=EXTERNAL_MODES, default="lib",
                    help="none | lib (imported libs) | global (+module objects) | all (+local vars)")
    sh.add_argument("--returns", action="store_true")
    sh.add_argument("--format", choices=("mermaid", "text"), default="mermaid")
    sh.add_argument("--json", action="store_true")

    ca = sub.add_parser("callers", help="who calls this function, and which flows reach it")
    ca.add_argument("function")
    ca.add_argument("--out", default="vibesequence-out")

    df = sub.add_parser("diff", help="flows changed by the last build that changed the graph")
    df.add_argument("entry", nargs="?")
    df.add_argument("--out", default="vibesequence-out")
    df.add_argument("--all", action="store_true")

    sv = sub.add_parser("serve", help="serve the viewer on 127.0.0.1 so source links can open your editor")
    sv.add_argument("--out", default="vibesequence-out")
    sv.add_argument("--port", type=int, default=8765)

    ins = sub.add_parser("install", help="add vibesequence instructions to CLAUDE.md / AGENTS.md so your assistant uses the flows")
    ins.add_argument("--file", default="CLAUDE.md")
    ins.add_argument("--no-hook", action="store_true", help="only write the instructions, no Claude Code hook")

    a = ap.parse_args(argv)
    if a.cmd == "install":
        _install(Path(a.file))
        if not a.no_hook:
            _install_hook(Path(".claude/settings.local.json"))
        return
    out = Path(a.out)

    if a.cmd == "build":
        old = builder.load(out) if a.hook and (out / "graph.json").exists() else None
        graph = _build(Path(a.path), out, use_cache=not a.no_cache)
        if a.hook:
            changes = _diff(old, graph) if old else ""
            if changes:
                print(json.dumps({
                    "systemMessage": "vibesequence: flow changed\n" + changes,
                    "hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext":
                                           "vibesequence: this edit changed these flows (function level):\n" + changes},
                }))
            return
        s = graph.stats
        print(f"vibesequence: {s['files']} files ({s['files_reparsed']} reparsed), {s['functions']} functions, "
              f"{s['entries']} flows, {s['resolved_calls']} resolved / {s['external_calls']} external calls "
              f"in {s['seconds']}s")
        if s.get("import_roots"):
            print(f"  import root: {', '.join(s['import_roots'])}/ (imports resolved as if vibesequence ran there)")
        if s.get("import_warning"):
            print(f"  warning: {s['import_warning']}")
        print(f"  {out/'index.html'}   interactive viewer · {out/'FLOWS.md'}   flow index for your AI assistant")
        print("  next:  vibesequence serve      viewer on http://127.0.0.1 (VS Code: Cmd+Shift+P > Simple Browser: Show)")
        print("         vibesequence install    teach your AI assistant + show changed flows after each edit")
        return

    if a.cmd == "diff":
        if not (out / "graph.prev.json").exists():
            sys.exit("no earlier graph yet: change some code and run `vibesequence build` again")
        graph = builder.load(out)
        only = _find_entry(graph, a.entry) if a.entry else None
        print(_diff(builder.load(out, "graph.prev.json"), graph, only=only, all_=a.all) or "no flow changes")
        return

    if a.cmd == "serve":
        from . import serve
        serve.run(out, a.port, refresh=lambda: _fresh(out))
        return

    graph = _fresh(out)
    if a.cmd == "list":
        for e in _visible(graph.entries, a.all):
            print(f"[{e.kind:7}] {e.label:40} {e.id}")
        return

    if a.cmd == "callers":
        _callers(graph, _find_entry(graph, a.function))
        return

    fid = _find_entry(graph, a.entry)
    tree = expand(graph, fid, a.depth)
    msg = project(graph, tree, a.level, externals=a.externals)
    if a.json:
        print(json.dumps(asdict(msg), indent=1))
    elif a.format == "text":
        print(render_text(graph, msg, a.level))
    else:
        print(render_mermaid(msg, a.level, show_returns=a.returns))
        print(f"%% {count_messages(msg)} messages, level={a.level}, depth={a.depth}", file=sys.stderr)


def _build(path: Path, out: Path, use_cache: bool = True, always_write: bool = True):
    graph = builder.build(path, out, use_cache=use_cache)
    # auto-rebuilds (show / serve polling) skip rewriting the viewer when no flow changed
    if always_write or graph.stats["changed"] or not (out / "index.html").exists():
        html_render.write(graph, out / "index.html")
        _write_flows_md(graph, out / "FLOWS.md")
    return graph


def _fresh(out: Path):
    """Incremental rebuild before reading, so an assistant never reads a stale graph (~20 ms warm)."""
    if not (out / "graph.json").exists():
        print(f"vibesequence: no {out}/ yet, building {Path.cwd().name}/ first", file=sys.stderr)
        return _build(Path("."), out)
    root = json.loads((out / "graph.json").read_text())["stats"]["root"]
    return _build(Path(root), out, always_write=False)


ROOT_LIMIT = 20


def _visible(entries, all_: bool = False):
    """Routes / commands / handlers / main are the real flows; `root` (uncalled function) is
    mostly helpers and framework callbacks. With nothing else (libraries, non-web code) show the
    top ROOT_LIMIT roots; entries arrive ranked by score."""
    if all_:
        return entries
    return [e for e in entries if e.kind != "root"] or entries[:ROOT_LIMIT]


def _callers(graph, fid: str):
    rev: dict[str, list[tuple[str, int]]] = {}
    for fn in graph.functions.values():
        for c in fn.calls:
            if c.resolved:
                rev.setdefault(c.resolved, []).append((fn.id, c.line))
    fn = graph.functions[fid]
    print(f"{fid}  {fn.file}:{fn.line}")
    print("called by:")
    for caller, line in rev.get(fid, []):
        print(f"  {caller}  {graph.functions[caller].file}:{line}")
    if not rev.get(fid):
        print("  (no resolved callers; may be called dynamically)")
    seen, stack = {fid}, [fid]
    while stack:
        for caller, _ in rev.get(stack.pop(), []):
            if caller not in seen:
                seen.add(caller)
                stack.append(caller)
    print("flows that reach it:")
    for e in _visible(graph.entries):
        if e.id in seen:
            print(f"  [{e.kind}] {e.label}  {e.id}")


def _flow_text(graph, fid: str) -> list[str]:
    """Function-level text without file:line: every new/removed step shows (class level would flatten
    calls inside one class away), and moving code around is not reported as a flow change."""
    msg = project(graph, expand(graph, fid, 4), "function")
    return render_text(graph, msg, "function", locations=False).splitlines()


def _diff(old, new, only: str | None = None, all_: bool = False) -> str:
    labels = {e.id: e.label for e in _visible(old.entries, all_) + _visible(new.entries, all_)}
    out: list[str] = []
    for fid, label in labels.items():
        if only and fid != only:
            continue
        a = _flow_text(old, fid) if fid in old.functions else []
        b = _flow_text(new, fid) if fid in new.functions else []
        d = ["⋮" if line.startswith("@@") else line                    # hunk offsets mean nothing to a reader
             for line in list(difflib.unified_diff(a, b, lineterm="", n=1))[2:]]   # drop ---/+++ header
        if d:
            out += [f"## {label}", *d, ""]
    return "\n".join(out).rstrip()


ASSISTANT_SNIPPET = """
## vibesequence (sequence-diagram map of this codebase)

Before reading source files to understand *how a request flows*, consult the
flows instead; they cost far fewer tokens than the files:

- `vibesequence-out/FLOWS.md` is the index: one line per flow (route / command / handler) with its location.
- `python -m vibesequence show "<flow or function>" --format text` prints one flow as an indented call tree
  with the `file:line` of every step: read ONLY those lines of source when you need to edit.
  `--level function --depth 6` zooms in, `--level module|package` zooms out, `--externals all` adds library/local calls.
- Steps ending in `…` are deeper than the current depth: run `show` on that callee to zoom in.
- Before changing a function, run `python -m vibesequence callers "<function>"` to see which flows it affects.
- These commands rebuild incrementally, so the flows always match the current code.
- When implementing a feature: first show the closest existing flow, then write the INTENDED sequence
  (Mermaid `sequenceDiagram`, class level) in your plan so the user can approve the steps before code.
  Mirror existing hand-offs (route -> service -> repo -> infra) instead of inventing a new path.
- After each edit a hook reports which flows changed; after the change, summarise it for the user in one
  or two lines (`python -m vibesequence diff` shows it again).
- The user follows along in the viewer: `python -m vibesequence serve` (http://127.0.0.1:8765, also works in
  VS Code's Simple Browser). Point them to the flow you changed; its Play button walks it step by step.
"""


def _install(path: Path):
    existing = path.read_text() if path.exists() else ""
    if "## vibesequence" in existing:                   # replace an older vibesequence section, keep everything else
        start = existing.index("## vibesequence")
        nxt = existing.find("\n## ", start + 1)
        existing = existing[:start].rstrip() + ("\n\n" + existing[nxt + 1:] if nxt != -1 else "")
        verb = "updated"
    else:
        verb = "added"
    path.write_text(existing.rstrip() + "\n" + ASSISTANT_SNIPPET)
    print(f"{verb} vibesequence section in {path}")


def _install_hook(path: Path):
    """PostToolUse hook: after every Edit/Write, rebuild and report changed flows (to user and assistant).
    Written to .claude/settings.local.json: personal (not committed), since the command path is per machine."""
    exe = "vibesequence" if shutil.which("vibesequence") else f'"{sys.executable}" -m vibesequence'
    command = f'cd "$CLAUDE_PROJECT_DIR" && {exe} build . --hook'
    settings = json.loads(path.read_text()) if path.exists() else {}
    hooks = settings.setdefault("hooks", {}).setdefault("PostToolUse", [])
    if any("vibesequence" in h.get("command", "") for entry in hooks for h in entry.get("hooks", [])):
        print(f"{path} already has the vibesequence hook")
        return
    hooks.append({"matcher": "Edit|Write|MultiEdit", "hooks": [{"type": "command", "command": command}]})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n")
    print(f"added Claude Code hook to {path}: changed flows are shown after each edit")


def _write_flows_md(graph, path: Path):
    """An index, not the diagrams: reading every flow up front is exactly the token spend we avoid."""
    lines = ["# Flows", "",
             "Generated by vibesequence. One line per flow. Print one with",
             '`python -m vibesequence show "<flow>" --format text` (or without `--format` for Mermaid).', ""]
    for e in _visible(graph.entries):
        fn = graph.functions[e.id]
        lines.append(f"- `{e.label}` [{e.kind}] `{e.id}` {fn.file}:{fn.line}")
    hidden = len(graph.entries) - len(_visible(graph.entries))
    if hidden:
        lines += ["", f"{hidden} more entry points, ranked lower: `python -m vibesequence list --all`"]
    path.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
