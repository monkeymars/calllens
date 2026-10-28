"""Go extractor on tree-sitter (optional: `pip install vibesequence[go]`).

Produces the same ModuleFacts as the Python and TypeScript extractors, so resolver, zoom, viewer, diff
and hook work unchanged. Mapping Go onto that model:
  * module = PACKAGE (its directory), not the file: Go calls functions in sibling files without an
    import, so every file of a package gets the same module name and the resolver merges their facts
  * imports resolve through the nearest go.mod: "<module path>/internal/repos" -> that local package;
    everything else (net/http, database/sql, github.com/...) stays a library participant
  * the method receiver is written as `self` (`s.orders.Create` -> `self.orders.Create`), so the
    resolver's self / self.attr rules apply; struct field types become class hints and embedded
    structs (Go's inheritance) become bases
  * locals are typed from parameters, `var x T`, `&T{}` / `T{}`, `new(T)` and the `NewT(...)`
    constructor convention
  * `if err != nil {...}` is drawn as an error branch, `go f()` as a goroutine, `defer f()` as finally
  * route registrations become small entry functions that call the handler: net/http
    HandleFunc / Handle (Go 1.22 "POST /x" patterns), Gin / Echo / Fiber / Chi verb methods
    (incl. `r.Group("/v1")` prefixes), and cobra.Command{Use, Run / RunE}
"""
from __future__ import annotations

import re
from pathlib import Path

import tree_sitter_go as tsgo
from tree_sitter import Language, Node, Parser

from ..model import CallSite, FunctionDef, ModuleFacts

_GO = Parser(Language(tsgo.language()))

# builtins and conversions: noise in a sequence diagram (like Python's builtins)
BUILTINS = {"append", "cap", "clear", "close", "complex", "copy", "delete", "imag", "len", "make", "max", "min",
            "new", "panic", "print", "println", "real", "recover", "string", "bool", "byte", "rune", "error", "any",
            "int", "int8", "int16", "int32", "int64", "uint", "uint8", "uint16", "uint32", "uint64", "uintptr",
            "float32", "float64", "complex64", "complex128"}
# standard packages that are plumbing, not steps; HTTP, SQL, os and friends stay visible as boundaries
NOISE_IMPORTS = {"fmt", "errors", "strings", "strconv", "bytes", "sort", "slices", "maps", "math", "unicode",
                 "unicode/utf8", "time", "context", "encoding/json", "sync", "sync/atomic", "reflect", "regexp",
                 "path", "path/filepath", "log", "log/slog"}
HTTP_VERBS = {"get", "post", "put", "patch", "delete", "head", "options"}
ERR_CHECK = re.compile(r"^\w*(err|Err)\w*\s*!=\s*nil$")


def _text(src: bytes, node: Node | None, limit: int | None = None) -> str:
    if node is None:
        return ""
    text = " ".join(src[node.start_byte:node.end_byte].decode("utf-8", "replace").split())
    return text if limit is None or len(text) <= limit else text[: limit - 1] + "…"


def _string(src: bytes, node: Node | None) -> str | None:
    if node is None or node.type not in ("interpreted_string_literal", "raw_string_literal"):
        return None
    return _text(src, node)[1:-1]


def _type_name(src: bytes, node: Node | None) -> str | None:
    """`*repos.OrderRepo` -> "repos.OrderRepo", `Repo[T]` -> "Repo"; slices, maps, funcs -> None."""
    while node is not None and node.type in ("pointer_type", "parenthesized_type"):
        node = node.named_children[0] if node.named_children else None
    if node is not None and node.type == "generic_type":
        node = node.child_by_field_name("type")
    if node is not None and node.type in ("type_identifier", "qualified_type", "identifier", "selector_expression"):
        return _text(src, node)
    return None


def _dotted(src: bytes, node: Node | None, recv: str | None = None) -> str | None:
    """`s.orders.Create` -> "self.orders.Create" (s = receiver);  `a.B().C` -> "a.B().C";  else None."""
    if node is None:
        return None
    t = node.type
    if t in ("identifier", "package_identifier", "field_identifier", "type_identifier"):
        name = _text(src, node)
        return "self" if recv and name == recv else name
    if t == "selector_expression":
        base = _dotted(src, node.child_by_field_name("operand"), recv)
        field = node.child_by_field_name("field")
        return f"{base}.{_text(src, field)}" if base and field is not None else None
    if t == "call_expression":
        base = _dotted(src, node.child_by_field_name("function"), recv)
        return f"{base}()" if base else None
    if t in ("parenthesized_expression", "index_expression", "generic_type"):
        inner = node.child_by_field_name("operand") or node.child_by_field_name("type") or \
            (node.named_children[0] if node.named_children else None)
        return _dotted(src, inner, recv)
    return None


def _value_type(src: bytes, node: Node | None) -> str | None:
    """Type a value has, as far as the syntax tells: `&T{}`, `T{}`, `new(T)`, `NewT(...)`, `pkg.NewT(...)`."""
    if node is not None and node.type == "unary_expression":
        node = node.child_by_field_name("operand")
    if node is None:
        return None
    if node.type == "composite_literal":
        return _type_name(src, node.child_by_field_name("type"))
    if node.type == "call_expression":
        name = _dotted(src, node.child_by_field_name("function"))
        args = node.child_by_field_name("arguments")
        if name == "new" and args is not None and args.named_children:
            return _type_name(src, args.named_children[0])
        head, _, last = (name or "").rpartition(".")
        if re.match(r"New[A-Z]", last):                       # Go constructor convention
            return f"{head}.{last[3:]}" if head else last[3:]
        if name and "()" not in name and not name.startswith("self."):
            return f"{name}()"                                 # "return type of db.Open", looked up by the resolver
    return None


def _result_type(src: bytes, node: Node | None) -> str | None:
    """`*Conn` / `(*Order, error)` -> the first named result type."""
    if node is not None and node.type == "parameter_list":
        first = next(iter(_children(node, "parameter_declaration")), None)
        node = first.child_by_field_name("type") if first is not None else None
    return _type_name(src, node)


def _children(node: Node | None, *types: str) -> list[Node]:
    return [c for c in (node.named_children if node is not None else []) if c.type in types]


class GoExtractor:
    extensions = (".go",)

    def __init__(self):
        self._gomod: dict[Path, tuple[str, Path] | None] = {}    # dir -> (module path, go.mod dir)

    def _go_mod(self, file: Path, root: Path) -> tuple[str, Path] | None:
        """Nearest go.mod above the file (like the go tool), so a monorepo build honours each module."""
        for d in [file.parent, *file.parent.parents]:
            if d not in self._gomod:
                f = d / "go.mod"
                m = re.search(r"^module\s+(\S+)", f.read_text(encoding="utf-8", errors="replace"), re.M) \
                    if f.is_file() else None
                self._gomod[d] = (m.group(1).strip('"'), d) if m else None
            if self._gomod[d] is not None:
                return self._gomod[d]
            if d == root:
                break
        return None

    @staticmethod
    def module_name(pkg_dir: Path, root: Path, fallback: str) -> str:
        parts = pkg_dir.relative_to(root).parts
        return ".".join(parts) if parts else fallback

    # ------------------------------------------------------------------ main
    def extract(self, path: Path, root: Path) -> ModuleFacts:
        root, path = root.resolve(), path.resolve()
        src = path.read_bytes()
        tree = _GO.parse(src)
        top = tree.root_node.named_children
        pkg = next((_text(src, c.named_children[0]) for c in top if c.type == "package_clause" and c.named_children),
                   "main")
        module = self.module_name(path.parent, root, pkg)
        rel = str(path.relative_to(root))
        facts = ModuleFacts(module=module, file=rel, functions=[], imports={}, instance_hints={}, bases={},
                            globals=[], aliases={})
        if path.name.endswith("_test.go"):                     # tests are not user flows
            return facts

        def add_function(name: str, node: Node, cls: str | None = None, recv: str | None = None,
                         decorators: list[str] | None = None, params: Node | None = None,
                         body: Node | None = None, hints: dict | None = None, prefix: str = "") -> FunctionDef:
            qual = f"{prefix}{name}"
            fn = FunctionDef(id=f"{module}:{cls + '.' if cls else ''}{qual}", name=qual, module=module, cls=cls,
                             file=rel, line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                             decorators=decorators or [])
            fn.local_hints.update(hints or {})
            for p in _children(params, "parameter_declaration", "variadic_parameter_declaration"):
                t = _type_name(src, p.child_by_field_name("type"))
                for n in p.children_by_field_name("name"):
                    if t:
                        fn.local_hints[_text(src, n)] = t
            facts.functions.append(fn)
            if body is not None:
                nested = lambda n, nm: add_function(nm, n, cls, recv, params=n.child_by_field_name("parameters"),  # noqa: E731
                                                    body=n.child_by_field_name("body"), prefix=f"{qual}.")
                _Body(src, fn, recv, facts, nested, entry).walk(body)
            return fn

        def entry(name: str, decorators: list[str], handler: Node, enclosing: FunctionDef, recv: str | None):
            """A route / command registration becomes its own function: the flow starts there and calls the
            handler, which usually lives in another package."""
            hints = dict(enclosing.local_hints)               # `h := handlers.New(...)` then `HandleFunc(.., h.Create)`
            if handler.type == "func_literal":
                add_function(name, handler, enclosing.cls, recv, decorators, handler.child_by_field_name("parameters"),
                             handler.child_by_field_name("body"), hints)
                return
            if handler.type == "call_expression":             # http.HandlerFunc(h.X) / auth(h.X): the wrapped handler
                args = handler.child_by_field_name("arguments")
                if args is not None and args.named_children:
                    handler = args.named_children[-1]
            fn = add_function(name, handler, enclosing.cls, recv, decorators, hints=hints)
            expr = _dotted(src, handler, recv)
            if expr:
                fn.calls.append(CallSite(expr=expr, line=handler.start_point[0] + 1, order=0, context=[]))

        for node in top:                                       # imports and types first: bodies use them
            if node.type == "import_declaration":
                specs = _children(node, "import_spec")
                specs += [s for lst in _children(node, "import_spec_list") for s in _children(lst, "import_spec")]
                for spec in specs:
                    self._import(src, spec, path, root, facts)
            elif node.type == "type_declaration":
                self._types(src, node, facts)
            elif node.type == "var_declaration":
                for spec in _children(node, "var_spec") + [s for lst in _children(node, "var_spec_list")
                                                           for s in _children(lst, "var_spec")]:
                    t = _type_name(src, spec.child_by_field_name("type"))
                    value = spec.child_by_field_name("value")
                    values = value.named_children if value is not None else []
                    for i, n in enumerate(spec.children_by_field_name("name")):
                        name = _text(src, n)
                        facts.globals.append(name)
                        ty = t or (_value_type(src, values[i]) if i < len(values) else None)
                        if ty:
                            facts.instance_hints[name] = ty
        for node in top:                                       # result types: `conn := db.Open()` -> *Conn
            if node.type == "function_declaration" and (rt := _result_type(src, node.child_by_field_name("result"))):
                facts.instance_hints[_text(src, node.child_by_field_name("name")) + "()"] = rt
        for node in top:
            if node.type == "function_declaration":
                add_function(_text(src, node.child_by_field_name("name")), node,
                             params=node.child_by_field_name("parameters"), body=node.child_by_field_name("body"))
            elif node.type == "method_declaration":
                recv_decl = next(iter(_children(node.child_by_field_name("receiver"), "parameter_declaration")), None)
                cls = _type_name(src, recv_decl.child_by_field_name("type")) if recv_decl is not None else None
                rname = recv_decl.child_by_field_name("name") if recv_decl is not None else None
                add_function(_text(src, node.child_by_field_name("name")), node, cls=cls,
                             recv=_text(src, rname) if rname is not None else None,
                             params=node.child_by_field_name("parameters"), body=node.child_by_field_name("body"))
        return facts

    # ------------------------------------------------------------------ pieces
    def _import(self, src: bytes, spec: Node, path: Path, root: Path, facts: ModuleFacts):
        ipath = _string(src, spec.child_by_field_name("path"))
        name_node = spec.child_by_field_name("name")
        alias = _text(src, name_node) if name_node is not None else None
        if not ipath or alias in ("_", "."):                   # blank / dot imports name nothing
            return
        segs = ipath.split("/")
        last = segs[-2] if len(segs) > 1 and re.fullmatch(r"v\d+", segs[-1]) else segs[-1]   # chi/v5 -> chi
        default = re.sub(r"^go-|\.v\d+$", "", last).replace("-", "_")                       # go-sqlite3, yaml.v3
        target = ipath
        gm = self._go_mod(path, root)
        if gm and (ipath == gm[0] or ipath.startswith(gm[0] + "/")):
            try:
                target = self.module_name((gm[1] / ipath[len(gm[0]):].lstrip("/")).resolve(), root, default)
            except ValueError:                                 # package outside the built folder
                pass
        facts.imports[alias or default] = target

    def _types(self, src: bytes, node: Node, facts: ModuleFacts):
        for spec in _children(node, "type_spec"):
            name = _text(src, spec.child_by_field_name("name"))
            struct = spec.child_by_field_name("type")
            if struct is None or struct.type != "struct_type":
                continue
            bases = facts.bases.setdefault(name, [])
            for field in (f for lst in _children(struct, "field_declaration_list")
                          for f in _children(lst, "field_declaration")):
                t = _type_name(src, field.child_by_field_name("type"))
                names = field.children_by_field_name("name")
                if not names and t:                            # embedded struct: its methods are promoted
                    bases.append(t)
                for n in names:
                    if t:
                        facts.instance_hints[f"{name}.{_text(src, n)}"] = t


class _Body:
    """Walks ONE function body: calls in evaluation order, with control-flow context."""

    def __init__(self, src: bytes, fn: FunctionDef, recv: str | None, facts: ModuleFacts, nested, entry):
        self.src, self.fn, self.recv, self.facts, self.nested, self.entry = src, fn, recv, facts, nested, entry
        self.stack: list[str] = []
        self.order = 0
        self.groups: dict[str, str] = {}                       # gin / echo: v1 := r.Group("/v1")

    def _in(self, label: str, node: Node | None):
        if node is None:
            return
        self.stack.append(label)
        self.walk(node)
        self.stack.pop()

    def _record(self, expr: str | None, node: Node):
        if not expr:
            return
        head = expr.split(".")[0].replace("()", "")
        if ("." not in expr.replace("()", "") and head in BUILTINS) or self.facts.imports.get(head) in NOISE_IMPORTS:
            return
        self.fn.calls.append(CallSite(expr=expr, line=node.start_point[0] + 1, order=self.order,
                                      context=list(self.stack)))
        self.order += 1

    def walk(self, node: Node | None):
        if node is None:
            return
        t, src = node.type, self.src
        if t == "if_statement":
            self.walk(node.child_by_field_name("initializer"))       # `if x, err := f(); ...`: f runs first
            cond = node.child_by_field_name("condition")
            self.walk(cond)
            label = "except" if ERR_CHECK.match(_text(src, cond)) else f"if:{_text(src, cond, 42)}"
            self._in(label, node.child_by_field_name("consequence"))
            self._in("else", node.child_by_field_name("alternative"))
        elif t == "for_statement":
            clause = next((c for c in node.named_children if c.type != "block"), None)
            if clause is None:
                label = "loop:forever"
            elif clause.type == "range_clause":
                self.walk(clause.child_by_field_name("right"))
                label = f"loop:for {_text(src, clause, 40)}"
            elif clause.type == "for_clause":
                label = f"loop:for {_text(src, clause.child_by_field_name('condition'), 38)}".rstrip()
            else:
                label = f"loop:for {_text(src, clause, 38)}"
            self._in(label, node.child_by_field_name("body"))
        elif t in ("expression_switch_statement", "type_switch_statement", "select_statement"):
            self.walk(node.child_by_field_name("initializer"))
            self.walk(node.child_by_field_name("value"))
            for case in node.named_children:
                header = {c.id for i, c in enumerate(case.children)
                          if case.field_name_for_child(i) in ("value", "type", "communication")}
                if case.type in ("expression_case", "type_case", "communication_case"):
                    first = next(c for c in case.children if c.id in header) if header else None
                    label = f"if:case {_text(src, first, 30)}"
                elif case.type == "default_case":
                    label = "else"
                else:
                    continue
                self.stack.append(label)
                for c in case.named_children:
                    if c.id not in header:
                        self.walk(c)
                self.stack.pop()
        elif t == "go_statement":
            self._in("go", node.named_children[0] if node.named_children else None)
        elif t == "defer_statement":
            self._in("finally", node.named_children[0] if node.named_children else None)
        elif t == "call_expression":
            self._call(node)
        elif t in ("short_var_declaration", "assignment_statement"):
            self._assign(node.child_by_field_name("left"), node.child_by_field_name("right"))
        elif t == "var_spec":                                  # var x T / var x = NewT()
            t_decl = _type_name(src, node.child_by_field_name("type"))
            value = node.child_by_field_name("value")
            values = value.named_children if value is not None else []
            for i, n in enumerate(node.children_by_field_name("name")):
                ty = t_decl or (_value_type(src, values[i]) if i < len(values) else None)
                if ty:
                    self.fn.local_hints[_text(src, n)] = ty
            self.walk(value)
        elif t == "composite_literal":
            ty = _type_name(src, node.child_by_field_name("type")) or ""
            if ty.split(".")[-1] == "Command" and self._cobra(node):
                return
            self.walk(node.child_by_field_name("body"))
        elif t == "func_literal":
            return                                             # stray closures: callbacks are handled by _call
        else:
            for c in node.named_children:
                self.walk(c)

    def _assign(self, left: Node | None, right: Node | None):
        lefts = left.named_children if left is not None else []
        rights = right.named_children if right is not None else []
        for i, lhs in enumerate(lefts):
            value = rights[i] if len(rights) == len(lefts) else (rights[0] if i == 0 and rights else None)
            name = _dotted(self.src, lhs, self.recv)
            if not name or value is None:
                continue
            if value.type == "func_literal" and "." not in name:   # helper := func() {...}
                self.nested(value, name)
                continue
            ty = _value_type(self.src, value)
            if "." not in name:
                if ty:
                    self.fn.local_hints[name] = ty
                group = self._group_prefix(value)
                if group is not None:
                    self.groups[name] = group
            elif name.startswith("self.") and name.count(".") == 1 and ty and self.fn.cls:
                self.facts.instance_hints[f"{self.fn.cls}.{name[5:]}"] = ty   # s.client = &http.Client{}
        for value in rights:
            if value.type != "func_literal":
                self.walk(value)

    def _group_prefix(self, value: Node) -> str | None:
        """`v1 := r.Group("/v1")` (gin, echo, fiber): routes on v1 get the prefix."""
        if value.type != "call_expression":
            return None
        func, args = value.child_by_field_name("function"), value.child_by_field_name("arguments")
        if func is None or func.type != "selector_expression" or _text(self.src, func.child_by_field_name("field")) != "Group":
            return None
        path = _string(self.src, args.named_children[0]) if args is not None and args.named_children else None
        if path is None:
            return None
        return self.groups.get(_dotted(self.src, func.child_by_field_name("operand"), self.recv) or "", "") + path

    def _call(self, node: Node):
        """Receiver chain and arguments run BEFORE the call, so record inner calls first."""
        func, args = node.child_by_field_name("function"), node.child_by_field_name("arguments")
        argv = args.named_children if args is not None else []
        if func is not None and func.type == "func_literal":  # func() {...}(): runs right here
            for a in argv:
                self.walk(a)
            self.walk(func.child_by_field_name("body"))
            return
        if self._route(func, argv):
            return
        if func is not None and func.type == "selector_expression":
            self.walk(func.child_by_field_name("operand"))
        for a in argv:
            if a.type == "func_literal":                       # callback (errgroup, sync.Once...): walked in place
                self.walk(a.child_by_field_name("body"))
            else:
                self.walk(a)
        self._record(_dotted(self.src, func, self.recv), node)

    def _route(self, func: Node | None, argv: list[Node]) -> bool:
        """HandleFunc("POST /x", h) / r.GET("/x", mw, h) / e.Post(...): register an entry, not a step."""
        if func is None or func.type != "selector_expression" or len(argv) < 2:
            return False
        path = _string(self.src, argv[0])
        handler = argv[-1]
        if path is None or handler.type not in ("func_literal", "identifier", "selector_expression", "call_expression"):
            return False
        method = _text(self.src, func.child_by_field_name("field"))
        obj = _dotted(self.src, func.child_by_field_name("operand"), self.recv) or "app"
        if method in ("HandleFunc", "Handle"):
            verb, _, rest = path.partition(" ")
            if rest and verb.lower() in HTTP_VERBS:            # Go 1.22: "POST /orders"
                name, deco = f"{verb.upper()} {rest}", f"{obj}.{verb.lower()}({rest!r})"
            else:
                name, deco = path, f"{obj}.route({path!r})"
        elif method.lower() in HTTP_VERBS and path.startswith("/") and (method.isupper() or method.istitle()):
            full = self.groups.get(obj, "") + path
            name, deco = f"{method.upper()} {full}", f"{obj}.{method.lower()}({full!r})"
        else:
            return False
        self.entry(name, [deco], handler, self.fn, self.recv)
        return True

    def _cobra(self, node: Node) -> bool:
        """cobra.Command{Use: "serve", RunE: runServe}: a CLI flow named after the command."""
        fields = {}
        for kv in _children(node.child_by_field_name("body"), "keyed_element"):
            parts = [c.named_children[0] if c.type == "literal_element" and c.named_children else c
                     for c in kv.named_children]
            if len(parts) == 2:
                fields[_text(self.src, parts[0])] = parts[1]
        use = _string(self.src, fields.get("Use"))
        handler = fields.get("RunE") or fields.get("Run")
        if not use or handler is None:
            return False
        self.entry(use.split()[0], ["cobra.command"], handler, self.fn, self.recv)
        return True
