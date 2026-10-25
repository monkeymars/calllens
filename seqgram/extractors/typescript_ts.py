"""TypeScript / JavaScript extractor on tree-sitter (optional: `pip install seqgram[ts]`).

Produces the same ModuleFacts as the Python extractor, so resolver, zoom, viewer, diff and hook
work unchanged. Mapping onto the Python-shaped model:
  * module name = path without extension, dotted:  lib/repos/orderRepo.ts -> lib.repos.orderRepo
  * imports are resolved like TypeScript does (relative "./x", tsconfig "paths" such as "@/*",
    index files); packages ("next/server", "pg") stay external and show up as libraries
  * `this.x` is written as `self.x`, so the resolver's self / self.attr rules apply unchanged
  * class field types come from constructor parameter properties (`private repo: OrderRepo`),
    typed fields and `this.x = new X()`; locals from `const x = new X()` and parameter types
  * callbacks passed to map / forEach / filter ... are drawn inside the calling function as a
    loop, `.catch(cb)` as an error branch: that is where those steps happen at runtime
  * entry points are written as decorator strings the Python side already understands:
    Next.js app/**/route.ts (`export async function POST`), pages/api/**, Express `router.get(...)`
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import tree_sitter_typescript as tsts
from tree_sitter import Language, Node, Parser

from ..model import CallSite, FunctionDef, ModuleFacts

_TS = Parser(Language(tsts.language_typescript()))
_TSX = Parser(Language(tsts.language_tsx()))
EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")
HTTP_VERBS = ("get", "post", "put", "patch", "delete")

# globals that are noise in a sequence diagram (like Python's builtins); `fetch` is NOT here: HTTP is a boundary
NOISE = {"console", "JSON", "Math", "Number", "String", "Boolean", "Array", "Object", "Promise", "Symbol", "Date",
         "Error", "TypeError", "Map", "Set", "WeakMap", "WeakSet", "BigInt", "parseInt", "parseFloat", "isNaN",
         "Buffer", "structuredClone", "setTimeout", "clearTimeout", "setInterval", "clearInterval", "require",
         "super", "Reflect", "encodeURIComponent", "decodeURIComponent", "RegExp"}
# globals that ARE boundaries: shown as libraries (Python shows imported `requests.post` the same way)
BOUNDARY_GLOBALS = {"fetch", "WebSocket", "XMLHttpRequest", "EventSource", "localStorage", "sessionStorage",
                    "indexedDB", "navigator"}
LOOP_METHODS = {"map", "forEach", "filter", "reduce", "flatMap", "some", "every", "find", "findIndex"}
CONTROL = {"if_statement", "for_statement", "for_in_statement", "while_statement", "do_statement",
           "try_statement", "switch_statement"}
FUNCTION_VALUES = {"arrow_function", "function_expression", "function"}


def _text(src: bytes, node: Node | None, limit: int | None = None) -> str:
    if node is None:
        return ""
    text = " ".join(src[node.start_byte:node.end_byte].decode("utf-8", "replace").split())
    return text if limit is None or len(text) <= limit else text[: limit - 1] + "…"


def _string_value(src: bytes, node: Node | None) -> str | None:
    if node is None or node.type not in ("string", "template_string"):
        return None
    return _text(src, node)[1:-1]


WRAPPER_TYPES = {"Promise", "Array", "Readonly", "Partial", "Awaited", "Required"}


def _type_name(src: bytes, node: Node | None) -> str | None:
    """`: OrderRepo` / `: Promise<OrderRepo>` / `: OrderRepo | null` -> "OrderRepo" (first named type, pre-order)."""
    stack = [node] if node is not None else []
    while stack:
        n = stack.pop()
        if n.type in ("type_identifier", "nested_type_identifier") and _text(src, n) not in WRAPPER_TYPES:
            return _text(src, n)
        stack.extend(reversed(n.named_children))
    return None


def _dotted(src: bytes, node: Node | None) -> str | None:
    """`this.repo.save` -> "self.repo.save";  `a.b().c` -> "a.b().c";  else None."""
    if node is None:
        return None
    t = node.type
    if t == "identifier":
        return _text(src, node)
    if t == "this":
        return "self"
    if t == "member_expression":
        base = _dotted(src, node.child_by_field_name("object"))
        prop = node.child_by_field_name("property")
        return f"{base}.{_text(src, prop)}" if base and prop is not None else None
    if t == "call_expression":
        base = _dotted(src, node.child_by_field_name("function"))
        return f"{base}()" if base else None
    if t in ("parenthesized_expression", "await_expression", "non_null_expression", "as_expression"):
        named = [c for c in node.children if c.is_named]
        return _dotted(src, named[0]) if named else None
    return None


def _load_aliases(root: Path) -> list[tuple[str, Path]] | None:
    """tsconfig/jsconfig in `root`: "paths": {"@/*": ["./src/*"]} -> [("@/", root/src)]; None if there
    is no config here. Comments and trailing commas are allowed in tsconfig, so strip them first."""
    for name in ("tsconfig.json", "jsconfig.json"):
        cfg = root / name
        if not cfg.exists():
            continue
        raw = re.sub(r"//[^\n]*|/\*.*?\*/", "", cfg.read_text(encoding="utf-8", errors="replace"), flags=re.S)
        raw = re.sub(r",(\s*[}\]])", r"\1", raw)
        try:
            opts = json.loads(raw).get("compilerOptions", {})
        except ValueError:
            return []
        base = root / opts.get("baseUrl", ".")
        out = []
        for key, targets in (opts.get("paths") or {}).items():
            if targets:
                out.append((key.rstrip("*"), base / targets[0].rstrip("*")))
        return sorted(out, key=lambda kv: -len(kv[0]))         # longest prefix first
    return None


class TypeScriptExtractor:
    extensions = EXTS

    def __init__(self):
        self._config: dict[Path, list[tuple[str, Path]] | None] = {}   # dir -> aliases of its tsconfig

    def _aliases(self, file: Path, root: Path) -> list[tuple[str, Path]]:
        """Nearest tsconfig/jsconfig above the file (like tsc), so a build from a monorepo root
        still honours each project's "@/..." paths."""
        for d in [file.parent, *file.parent.parents]:
            if d not in self._config:
                self._config[d] = _load_aliases(d)
            if self._config[d] is not None:
                return self._config[d]
            if d == root:
                break
        return []

    # ------------------------------------------------------------------ paths
    @staticmethod
    def module_name(path: Path, root: Path) -> str:
        return ".".join(path.relative_to(root).with_suffix("").parts)

    def _resolve_spec(self, spec: str, file: Path, root: Path) -> str | None:
        """import specifier -> module name of the file it points to, or None for packages."""
        if spec.startswith("."):
            base = (file.parent / spec)
        else:
            base = next((target / spec[len(prefix):] for prefix, target in self._aliases(file, root)
                         if spec.startswith(prefix)), None)
            if base is None:
                return None
        for cand in [base, *(base.with_name(base.name + e) for e in EXTS), *(base / f"index{e}" for e in EXTS)]:
            if cand.is_file() and cand.suffix in EXTS:
                try:
                    return self.module_name(cand.resolve(), root)
                except ValueError:                             # outside the project
                    return None
        return None

    # ------------------------------------------------------------------ main
    def extract(self, path: Path, root: Path) -> ModuleFacts:
        root = root.resolve()
        path = path.resolve()
        src = path.read_bytes()
        parser = _TSX if path.suffix in (".tsx", ".jsx", ".js", ".mjs", ".cjs") else _TS
        tree = parser.parse(src)
        module = self.module_name(path, root)
        rel = str(path.relative_to(root))
        facts = ModuleFacts(module=module, file=rel, functions=[], imports={}, instance_hints={}, bases={},
                            globals=[], aliases={})
        route_url = _next_route_url(path.relative_to(root))
        pending_routes: dict[str, list[str]] = {}              # named Express handler -> decorators

        def add_function(name: str, node: Node, cls: str | None = None, decorators: list[str] | None = None,
                         params: Node | None = None, body: Node | None = None, class_hints: dict | None = None,
                         prefix: str = "") -> FunctionDef:
            qual_name = f"{prefix}{name}"
            fn = FunctionDef(id=f"{module}:{cls + '.' if cls else ''}{qual_name}", name=qual_name, module=module,
                             cls=cls, file=rel, line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                             decorators=decorators or [], is_async=any(c.type == "async" for c in node.children))
            if params is not None:
                for p in params.named_children:
                    pat, typ = p.child_by_field_name("pattern"), p.child_by_field_name("type")
                    t = _type_name(src, typ)
                    if pat is not None and pat.type == "identifier" and t:
                        fn.local_hints[_text(src, pat)] = t
            facts.functions.append(fn)
            if body is not None:
                _Body(src, fn, class_hints if class_hints is not None else facts.instance_hints,
                      lambda n, b, p, nm: add_function(nm, n, cls, params=p, body=b, class_hints=class_hints,
                                                       prefix=f"{qual_name}.")).walk(body)
            return fn

        def top_function(name: str, node: Node, value: Node | None = None, exported: bool = False):
            fn_node = value or node
            decos = []
            if exported and route_url is not None and name.lower() in HTTP_VERBS + ("head", "options"):
                decos.append(f"route.{name.lower()}({route_url!r})")    # Next.js app/**/route.ts
            add_function(name, fn_node, decorators=decos, params=fn_node.child_by_field_name("parameters")
                         or fn_node.child_by_field_name("parameter"), body=fn_node.child_by_field_name("body"))

        def declaration(node: Node, exported: bool = False, default: bool = False):
            t = node.type
            if t in ("function_declaration", "generator_function_declaration"):
                name = _text(src, node.child_by_field_name("name"))
                top_function(name, node, exported=exported)
                if default:
                    facts.aliases["default"] = name
                    _pages_api(name)
            elif t in ("class_declaration", "abstract_class_declaration"):
                name = _text(src, node.child_by_field_name("name"))
                self._class(src, node, name, facts, add_function)
                if default:
                    facts.aliases["default"] = name
            elif t in ("lexical_declaration", "variable_declaration"):
                for d in node.named_children:
                    if d.type != "variable_declarator":
                        continue
                    name_node, value = d.child_by_field_name("name"), d.child_by_field_name("value")
                    if name_node is None or name_node.type != "identifier":
                        continue
                    name = _text(src, name_node)
                    facts.globals.append(name)
                    if value is not None and value.type in FUNCTION_VALUES:
                        top_function(name, d, value, exported=exported)
                    elif value is not None and value.type == "new_expression":
                        ctor = _dotted(src, value.child_by_field_name("constructor"))
                        if ctor:
                            facts.instance_hints[name] = ctor
                    t_ann = _type_name(src, d.child_by_field_name("type"))
                    if t_ann and name not in facts.instance_hints:
                        facts.instance_hints[name] = t_ann
            elif t == "expression_statement":
                call = node.named_children[0] if node.named_children else None
                if call is not None and call.type == "call_expression":
                    express_route(call)

        def _pages_api(name: str):                             # pages/api/x.ts: default export handles all verbs
            parts = Path(rel).with_suffix("").parts
            if "pages" in parts and "api" in parts[parts.index("pages"):]:
                url = "/" + "/".join(p for p in parts[parts.index("pages") + 1:] if p != "index")
                for fn in facts.functions:
                    if fn.name == name:
                        fn.decorators.append(f"route.route({url!r})")

        def express_route(call: Node):
            """router.get('/x', mw, handler) / app.post(...): handlers become route entry points."""
            fn_node, args = call.child_by_field_name("function"), call.child_by_field_name("arguments")
            if fn_node is None or fn_node.type != "member_expression" or args is None:
                return
            verb = _text(src, fn_node.child_by_field_name("property"))
            obj = _dotted(src, fn_node.child_by_field_name("object")) or "app"
            argv = args.named_children
            url = _string_value(src, argv[0]) if argv else None
            if verb not in HTTP_VERBS or url is None:
                return
            deco = f"{obj}.{verb}({url!r})"
            handler = argv[-1]
            if handler.type in FUNCTION_VALUES:
                add_function(f"{verb.upper()} {url}", handler, decorators=[deco],
                             params=handler.child_by_field_name("parameters") or handler.child_by_field_name("parameter"),
                             body=handler.child_by_field_name("body"))
            elif handler.type == "identifier":
                pending_routes.setdefault(_text(src, handler), []).append(deco)

        for node in tree.root_node.named_children:
            if node.type == "import_statement":
                self._import(src, node, path, root, facts)
            elif node.type == "export_statement":
                default = any(c.type == "default" for c in node.children)
                decl = node.child_by_field_name("declaration")
                value = node.child_by_field_name("value")
                if decl is not None:
                    declaration(decl, exported=True, default=default)
                elif default and value is not None and value.type == "identifier":
                    facts.aliases["default"] = _text(src, value)
                    _pages_api(_text(src, value))
                for clause in (c for c in node.named_children if c.type == "export_clause"):
                    for spec in clause.named_children:
                        name, alias = spec.child_by_field_name("name"), spec.child_by_field_name("alias")
                        if name is not None and alias is not None:
                            facts.aliases[_text(src, alias)] = _text(src, name)
            else:
                declaration(node)

        for fn in facts.functions:                             # named Express handlers in this file
            fn.decorators.extend(pending_routes.get(fn.name, []))
        used = {c.expr.split(".")[0] for fn in facts.functions for c in fn.calls}
        for name in BOUNDARY_GLOBALS & used:
            if name not in facts.imports and name not in facts.globals:
                facts.imports[name] = f"<global>.{name}"
        return facts

    # ------------------------------------------------------------------ pieces
    def _import(self, src: bytes, node: Node, path: Path, root: Path, facts: ModuleFacts):
        spec = _string_value(src, node.child_by_field_name("source"))
        if spec is None:
            return
        target = self._resolve_spec(spec, path, root) or spec     # package: keep the specifier
        clause = next((c for c in node.named_children if c.type == "import_clause"), None)
        if clause is None:
            return
        for part in clause.named_children:
            if part.type == "identifier":                                   # import X from
                facts.imports[_text(src, part)] = f"{target}.default"
            elif part.type == "namespace_import":                           # import * as X from
                ident = next((c for c in part.named_children if c.type == "identifier"), None)
                if ident is not None:
                    facts.imports[_text(src, ident)] = target
            elif part.type == "named_imports":                              # import { a, b as c } from
                for spec_node in part.named_children:
                    name, alias = spec_node.child_by_field_name("name"), spec_node.child_by_field_name("alias")
                    if name is not None:
                        facts.imports[_text(src, alias or name)] = f"{target}.{_text(src, name)}"

    def _class(self, src: bytes, node: Node, name: str, facts: ModuleFacts, add_function):
        heritage = next((c for c in node.named_children if c.type == "class_heritage"), None)
        bases = []
        if heritage is not None:
            for ext in (c for c in heritage.named_children if c.type == "extends_clause"):
                b = _dotted(src, ext.child_by_field_name("value"))
                if b:
                    bases.append(b)
        facts.bases[name] = bases
        hints = _ClassHints(facts.instance_hints, name)
        body = node.child_by_field_name("body")
        if body is None:
            return
        for member in body.named_children:                    # field types first: methods use them
            if member.type == "public_field_definition":
                fname = member.child_by_field_name("name")
                t = _type_name(src, member.child_by_field_name("type"))
                value = member.child_by_field_name("value")
                if value is not None and value.type == "new_expression":
                    t = _dotted(src, value.child_by_field_name("constructor")) or t
                if fname is not None and t:
                    hints[_text(src, fname)] = t
            elif member.type == "method_definition" and _text(src, member.child_by_field_name("name")) == "constructor":
                params = member.child_by_field_name("parameters")
                for p in (params.named_children if params is not None else []):     # constructor(private repo: Repo)
                    if any(c.type in ("accessibility_modifier", "readonly") for c in p.children):
                        pat, t = p.child_by_field_name("pattern"), _type_name(src, p.child_by_field_name("type"))
                        if pat is not None and t:
                            hints[_text(src, pat)] = t
        for member in body.named_children:
            if member.type == "method_definition":
                mname = _text(src, member.child_by_field_name("name"))
                add_function(mname, member, cls=name, params=member.child_by_field_name("parameters"),
                             body=member.child_by_field_name("body"), class_hints=hints)
            elif member.type == "public_field_definition":          # handler = async () => {...}
                value = member.child_by_field_name("value")
                if value is not None and value.type in FUNCTION_VALUES:
                    add_function(_text(src, member.child_by_field_name("name")), value, cls=name,
                                 params=value.child_by_field_name("parameters") or value.child_by_field_name("parameter"),
                                 body=value.child_by_field_name("body"), class_hints=hints)


class _ClassHints(dict):
    """Stores class attribute hints as 'Class.attr' in the module's instance_hints (Python extractor does the same)."""

    def __init__(self, parent: dict, cls: str):
        super().__init__()
        self.parent, self.cls = parent, cls

    def __setitem__(self, k, v):
        self.parent[f"{self.cls}.{k}"] = v


class _Body:
    """Walks ONE function body: calls in evaluation order, with control-flow context."""

    def __init__(self, src: bytes, fn: FunctionDef, class_hints: dict, nested):
        self.src, self.fn, self.class_hints, self.nested = src, fn, class_hints, nested
        self.stack: list[str] = []
        self.order = 0

    def _in(self, label: str, node: Node | None):
        if node is None:
            return
        self.stack.append(label)
        self.walk(node)
        self.stack.pop()

    def _record(self, expr: str | None, node: Node):
        if not expr or expr.split(".")[0].replace("()", "") in NOISE:
            return
        self.fn.calls.append(CallSite(expr=expr, line=node.start_point[0] + 1, order=self.order, context=list(self.stack)))
        self.order += 1

    def walk(self, node: Node):
        t, src = node.type, self.src
        if t == "if_statement":
            self.walk(node.child_by_field_name("condition"))
            self._in(f"if:{_text(src, node.child_by_field_name('condition'), 42)[1:-1]}", node.child_by_field_name("consequence"))
            alt = node.child_by_field_name("alternative")
            if alt is not None:
                self._in("else", alt)
        elif t in ("for_in_statement", "for_statement"):
            if t == "for_in_statement":
                self.walk(node.child_by_field_name("right"))
                label = f"loop:for {_text(src, node.child_by_field_name('left'), 20)} of {_text(src, node.child_by_field_name('right'), 20)}"
            else:
                label = f"loop:for {_text(src, node.child_by_field_name('condition'), 38)}"
            self._in(label, node.child_by_field_name("body"))
        elif t in ("while_statement", "do_statement"):
            cond = node.child_by_field_name("condition")
            self._in(f"loop:while {_text(src, cond, 38)[1:-1]}", node.child_by_field_name("body"))
        elif t == "try_statement":
            self._in("try", node.child_by_field_name("body"))
            handler = node.child_by_field_name("handler")
            if handler is not None:
                self._in("except", handler.child_by_field_name("body"))
            fin = node.child_by_field_name("finalizer")
            if fin is not None:
                self._in("finally", fin.child_by_field_name("body"))
        elif t == "switch_statement":
            self.walk(node.child_by_field_name("value"))
            body = node.child_by_field_name("body")
            for case in (body.named_children if body is not None else []):
                value = case.child_by_field_name("value")
                label = f"if:case {_text(src, value, 30)}" if value is not None else "else"
                self.stack.append(label)
                for c in case.named_children[1 if value is not None else 0:]:
                    self.walk(c)
                self.stack.pop()
        elif t == "call_expression":
            self._call(node)
        elif t == "new_expression":
            for c in node.named_children:
                if c.type == "arguments":
                    self.walk(c)
            self._record(_dotted(src, node.child_by_field_name("constructor")), node)
        elif t in ("function_declaration", "generator_function_declaration"):
            self.nested(node, node.child_by_field_name("body"), node.child_by_field_name("parameters"),
                        _text(src, node.child_by_field_name("name")))
        elif t == "variable_declarator":
            self._declarator(node)
        elif t == "assignment_expression":
            left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
            dotted = _dotted(src, left)
            if dotted and dotted.startswith("self.") and right is not None and right.type == "new_expression":
                ctor = _dotted(src, right.child_by_field_name("constructor"))
                if ctor:
                    self.class_hints[dotted[5:]] = ctor
            if right is not None:
                self.walk(right)
        elif t in FUNCTION_VALUES or t in ("class_declaration", "class"):
            return                                            # stray callbacks are handled by _call
        else:
            for c in node.named_children:
                self.walk(c)

    def _declarator(self, node: Node):
        name, value = node.child_by_field_name("name"), node.child_by_field_name("value")
        if name is not None and name.type == "identifier":
            ident = _text(self.src, name)
            t = _type_name(self.src, node.child_by_field_name("type"))
            if value is not None and value.type in FUNCTION_VALUES:   # const helper = () => {...}
                self.nested(value, value.child_by_field_name("body"),
                            value.child_by_field_name("parameters") or value.child_by_field_name("parameter"), ident)
                return
            if value is not None and value.type == "new_expression":
                t = _dotted(self.src, value.child_by_field_name("constructor")) or t
            if t:
                self.fn.local_hints[ident] = t
        if value is not None:
            self.walk(value)

    def _call(self, node: Node):
        """Receiver chain and arguments run BEFORE the call, so record inner calls first; inline
        callbacks are walked as part of this function (loop context for map / forEach / ...)."""
        func, args = node.child_by_field_name("function"), node.child_by_field_name("arguments")
        expr = _dotted(self.src, func)
        method = expr.rsplit(".", 1)[-1] if expr and "." in expr else None
        if func is not None and func.type == "member_expression":
            self.walk(func.child_by_field_name("object"))
        for arg in (args.named_children if args is not None else []):
            if arg.type in FUNCTION_VALUES:
                body = arg.child_by_field_name("body")
                receiver = expr.rsplit(".", 1)[0] if expr and "." in expr else ""
                if method in LOOP_METHODS:
                    self._in(f"loop:for each in {receiver[-30:]}", body)
                elif method == "catch":
                    self._in("except", body)
                else:
                    self.walk(body) if body is not None else None
            else:
                self.walk(arg)
        if method in LOOP_METHODS | {"then", "catch", "finally"}:
            return                                            # array helpers / promise plumbing: not a step
        self._record(expr, node)


def _next_route_url(rel: Path) -> str | None:
    """app/api/orders/[id]/route.ts -> "/api/orders/[id]"; route groups "(shop)" are not in the URL."""
    parts = rel.with_suffix("").parts
    if not parts or parts[-1] != "route" or "app" not in parts:
        return None
    segs = parts[len(parts) - 1 - parts[::-1].index("app"):-1]
    return "/" + "/".join(s for s in segs[1:] if not (s.startswith("(") and s.endswith(")")))
