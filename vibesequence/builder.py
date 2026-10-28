"""Build pipeline:  files -> ModuleFacts (cached) -> resolve -> entry points -> Graph

Scalability notes:
  * Extraction is per-file and cached by content hash in vibesequence-out/cache.json.
    A rebuild on a 2000-file repo touches only the files whose hash changed.
  * Resolution is a cheap dictionary chain, O(total calls).
  * Flows are NOT precomputed. The viewer / CLI expands a flow lazily from the
    graph, so output size is O(functions + calls), not O(entries × depth).
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
import sys
import time
from dataclasses import asdict
from pathlib import Path

from .entrypoints import detect
from .extractors.base import get_extractors
from .model import CallSite, FunctionDef, Graph, ModuleFacts
from .resolver import Resolver

CACHE_VERSION = 2          # bump when extractor output changes, so stale facts are reparsed
IGNORE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
               "vibesequence-out", "graphify-out", ".mypy_cache", ".pytest_cache", "site-packages",
               ".next", ".nuxt", ".svelte-kit", ".turbo", ".vercel", "coverage", ".cache", ".strapi",
               "vendor", "testdata"}
# files we could parse with an optional extra: counted when it is missing, so the build can say so
OPTIONAL = {**dict.fromkeys((".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts"), ("TypeScript/JavaScript", "ts")),
            ".go": ("Go", "go")}


def _hash(p: Path) -> str:
    return hashlib.sha1(p.read_bytes()).hexdigest()


def _facts_from_cache(d: dict) -> ModuleFacts:
    fns = []
    for f in d["functions"]:
        f = dict(f)
        f["calls"] = [CallSite(**c) for c in f["calls"]]
        fns.append(FunctionDef(**f))
    return ModuleFacts(module=d["module"], file=d["file"], functions=fns,
                       imports=d["imports"], instance_hints=d["instance_hints"],
                       bases=d.get("bases", {}), globals=d.get("globals", []), aliases=d.get("aliases", {}))


def build(root: Path, out_dir: Path, use_cache: bool = True) -> Graph:
    t0 = time.time()
    root = root.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_path = out_dir / "cache.json"
    cache: dict = json.loads(cache_path.read_text()) if (use_cache and cache_path.exists()) else {}
    if cache.get("__version__") != CACHE_VERSION:
        cache = {}
    extractors = get_extractors()

    modules: list[ModuleFacts] = []
    new_cache: dict = {"__version__": CACHE_VERSION}
    files_seen = files_parsed = 0
    skipped: Counter[str] = Counter()                     # optional language -> files we could not parse
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.name.endswith(".d.ts"):
            continue
        if p.suffix not in extractors:
            if p.suffix in OPTIONAL and not any(part in IGNORE_DIRS for part in p.relative_to(root).parts):
                skipped[OPTIONAL[p.suffix]] += 1
            continue
        if any(part in IGNORE_DIRS for part in p.relative_to(root).parts):
            continue
        files_seen += 1
        rel = str(p.relative_to(root))
        h = _hash(p)
        if rel in cache and cache[rel]["hash"] == h:
            facts = _facts_from_cache(cache[rel]["facts"])
        else:
            try:
                facts = extractors[p.suffix].extract(p, root)
            except SyntaxError as e:
                print(f"  skip {rel}: {e.msg} (line {e.lineno})", file=sys.stderr)
                continue
            files_parsed += 1
        for fn in facts.functions:            # reset resolution; it depends on other files
            for c in fn.calls:
                c.resolved = None
        modules.append(facts)
        new_cache[rel] = {"hash": h, "facts": asdict(facts)}

    import_roots, import_warning = _apply_import_roots(modules)
    resolver = Resolver(modules)
    res_stats = resolver.resolve_all()
    entries = detect(resolver.functions)

    stats = {
        "root": str(root), "files": files_seen, "files_reparsed": files_parsed,
        "functions": len(resolver.functions), "entries": len(entries),
        **res_stats, "seconds": round(time.time() - t0, 3),
        "import_roots": import_roots, "import_warning": import_warning or "; ".join(
            f"{n} {lang} files skipped: pip install 'vibesequence[{extra}]'" for (lang, extra), n in skipped.items()),
    }
    graph = Graph(functions=resolver.functions, entries=entries, stats=stats)
    cache_path.write_text(json.dumps(new_cache))
    graph_path = out_dir / "graph.json"
    shape = _shape(graph.to_json())
    old = _shape(json.loads(graph_path.read_text())) if graph_path.exists() else None
    # version: changes only when functions/calls/entries change (not timings), so a live viewer
    # and `serve` can tell "the flows changed" from "someone ran build again"
    stats["version"] = hashlib.sha1(json.dumps(shape, sort_keys=True).encode()).hexdigest()[:12]
    stats["changed"] = shape != old
    # keep the graph from before the last CHANGE (not the last build) for `vibesequence diff`;
    # a no-op rebuild must not overwrite it
    if old is not None and stats["changed"]:
        graph_path.replace(out_dir / "graph.prev.json")
    graph_path.write_text(json.dumps(graph.to_json(), indent=1))
    return graph


def _apply_import_roots(modules: list[ModuleFacts]) -> tuple[list[str], str]:
    """Built one level too high (monorepo, `vibesequence build .` above the project)? Then imports say
    `app.repos.base` while the file's module is `services.app.repos.base`, and nothing resolves.
    Find such prefixes (>= 3 imports match one folder unambiguously) and strip them from module
    names, as if vibesequence had been run inside that folder. Skipped, with a warning, when stripping
    would collide with an existing module or the match is ambiguous."""
    known = {m.module for m in modules}
    prefixes_of: dict[str, set[str]] = defaultdict(set)     # "app.repos.base" -> {"services"}
    for name in known:
        parts = name.split(".")
        for i in range(1, len(parts)):
            prefixes_of[".".join(parts[i:])].add(".".join(parts[:i]))
    hits: Counter[str] = Counter()
    ambiguous: Counter[str] = Counter()
    for m in modules:
        for target in m.imports.values():
            candidates = [t for t in (target, target.rpartition(".")[0]) if t]
            if any(t in known for t in candidates):
                continue
            for t in candidates:
                if len(prefixes_of.get(t, ())) == 1:
                    hits[next(iter(prefixes_of[t]))] += 1
                    break
                if prefixes_of.get(t):
                    ambiguous[" / ".join(sorted(prefixes_of[t]))] += 1
                    break
    applied, warning = [], ""
    for prefix, n in hits.most_common():
        if n < 3:
            break
        dot = prefix + "."
        renamed = {m.module: m.module[len(dot):] for m in modules if m.module.startswith(dot)}
        if set(renamed.values()) & (known - set(renamed)) or len(set(renamed.values())) < len(renamed):
            warning = f"imports match {prefix.replace('.', '/')}/ but stripping it collides with other modules: build that folder"
            continue
        cut = lambda s: s[len(dot):] if s.startswith(dot) else s   # noqa: E731
        for m in modules:
            m.module = cut(m.module)
            m.imports = {k: cut(v) for k, v in m.imports.items()}
            for fn in m.functions:
                fn.module = cut(fn.module)
                fn.id = f"{fn.module}:{fn.id.partition(':')[2]}"
        known = {m.module for m in modules}
        applied.append(prefix.replace(".", "/"))
    if not applied and sum(ambiguous.values()) >= 3:
        where = ambiguous.most_common(1)[0][0].replace(".", "/")
        warning = f"imports match several folders ({where}): build one project at a time"
    return applied, warning


def _shape(d: dict) -> tuple:
    # ponytail: line shifts count as a change, so a comment-only edit rotates graph.prev.json and
    # `vibesequence diff` then says "no flow changes"; compare line-free shapes if that bites
    return d["functions"], d["entries"]          # everything but stats (timings)


def load(out_dir: Path, name: str = "graph.json") -> Graph:
    d = json.loads((out_dir / name).read_text())
    fns = {}
    for k, f in d["functions"].items():
        f = dict(f)
        f["calls"] = [CallSite(**c) for c in f["calls"]]
        fns[k] = FunctionDef(**f)
    from .model import EntryPoint
    return Graph(functions=fns, entries=[EntryPoint(**e) for e in d["entries"]], stats=d["stats"])
