"""Python extractor using the standard-library `ast` module (no dependencies).

What it records per function:
  * every call expression, IN SOURCE ORDER  -> this is what makes a sequence
    diagram possible (a plain call graph loses ordering)
  * the control-flow context of each call (if / loop / try / with) -> rendered
    later as alt / loop / opt fragments
  * type hints for `self.x = Foo()`, `x: Foo`, `def f(x: Foo)` and `x = Foo()`
    -> used by the resolver to turn `self.repo.save` into `OrderRepo.save`
"""
from __future__ import annotations

import ast
import builtins
from pathlib import Path

# calls to these are noise in a sequence diagram (len, print, ValueError, ...)
BUILTIN_NAMES = {n for n in dir(builtins) if not n.startswith("_")}

from ..model import CallSite, FunctionDef, ModuleFacts

CONTEXT_OF = {
    ast.If: "if",
    ast.For: "loop",
    ast.AsyncFor: "loop",
    ast.While: "loop",
    ast.Try: "try",
    ast.With: "with",
    ast.AsyncWith: "with",
}


def _dotted(node: ast.AST) -> str | None:
    """Turn `a.b.c` / `self.repo.save` into its dotted string, else None."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    if isinstance(node, ast.Call):           # Foo().bar  -> "Foo().bar"
        base = _dotted(node.func)
        return f"{base}()" if base else None
    return None


def _src(node: ast.AST, limit: int = 40) -> str:
    """Short source text for fragment labels: `if not user`, `for item in items`."""
    text = " ".join(ast.unparse(node).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _type_name(annotation: ast.AST | None) -> str | None:
    if annotation is None:
        return None
    if isinstance(annotation, ast.Subscript):          # Optional[Foo], list[Foo]
        return _type_name(annotation.slice)
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        return annotation.value                        # "Foo" forward ref
    return _dotted(annotation)


def _decorator_name(d: ast.AST) -> str:
    target = d.func if isinstance(d, ast.Call) else d
    name = _dotted(target) or "?"
    if isinstance(d, ast.Call) and d.args and isinstance(d.args[0], ast.Constant):
        return f"{name}({d.args[0].value!r})"
    return name


class _FunctionBodyVisitor(ast.NodeVisitor):
    """Walks ONE function body, collecting calls in order with context."""

    def __init__(self, fn: FunctionDef, class_hints: dict[str, str]):
        self.fn = fn
        self.class_hints = class_hints
        self.stack: list[str] = []
        self.order = 0

    # --- control-flow context -------------------------------------------
    def _with_ctx(self, label: str, nodes):
        self.stack.append(label)
        for n in nodes:
            self.visit(n)
        self.stack.pop()

    def visit_If(self, node: ast.If):
        self.visit(node.test)
        self._with_ctx(f"if:{_src(node.test)}", node.body)
        if node.orelse:
            self._with_ctx("else", node.orelse)

    def visit_For(self, node):
        self.visit(node.iter)
        self._with_ctx(f"loop:for {_src(node.target)} in {_src(node.iter)}", node.body)
        if node.orelse:
            self._with_ctx("else", node.orelse)

    visit_AsyncFor = visit_For

    def visit_While(self, node: ast.While):
        self.visit(node.test)
        self._with_ctx(f"loop:while {_src(node.test)}", node.body)

    def visit_Try(self, node: ast.Try):
        self._with_ctx("try", node.body)
        for h in node.handlers:
            self._with_ctx(f"except:{_src(h.type)}" if h.type else "except", h.body)
        if node.orelse:
            self._with_ctx("else", node.orelse)
        if node.finalbody:
            self._with_ctx("finally", node.finalbody)

    visit_TryStar = visit_Try

    def visit_With(self, node):
        for item in node.items:
            self.visit(item.context_expr)
        self._with_ctx("with:" + ", ".join(_src(i.context_expr) for i in node.items), node.body)

    visit_AsyncWith = visit_With

    # nested defs are extracted separately by the module visitor
    def visit_FunctionDef(self, node):
        return

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef
    visit_Lambda = visit_FunctionDef

    # --- type hints inside the body --------------------------------------
    def visit_Assign(self, node: ast.Assign):
        value = node.value
        if isinstance(value, ast.Call):
            tname = _dotted(value.func)
            if tname and tname[:1].isupper():         # Foo(...) -> probably a class
                for t in node.targets:
                    dotted = _dotted(t)
                    if dotted and dotted.startswith("self."):
                        self.class_hints[dotted[5:]] = tname
                    elif isinstance(t, ast.Name):
                        self.fn.local_hints[t.id] = tname
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign):
        tname = _type_name(node.annotation)
        dotted = _dotted(node.target)
        if tname and dotted:
            if dotted.startswith("self."):
                self.class_hints[dotted[5:]] = tname
            else:
                self.fn.local_hints[dotted] = tname
        self.generic_visit(node)

    # --- the calls themselves -------------------------------------------
    def visit_Call(self, node: ast.Call):
        # Python evaluates receiver chain and arguments BEFORE the call itself,
        # so inner calls are recorded first: a.b().c(d()) -> a.b, d, a.b().c
        self.generic_visit(node)
        expr = _dotted(node.func)
        if expr and expr not in BUILTIN_NAMES:
            self.fn.calls.append(CallSite(
                expr=expr,
                line=node.lineno,
                order=self.order,
                context=list(self.stack),
            ))
            self.order += 1


class PythonAstExtractor:
    extensions = (".py",)

    def extract(self, path: Path, root: Path) -> ModuleFacts:
        rel = path.relative_to(root)
        module = ".".join(rel.with_suffix("").parts)
        if module.endswith(".__init__"):
            module = module[: -len(".__init__")]

        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
        imports: dict[str, str] = {}
        instance_hints: dict[str, str] = {}
        bases: dict[str, list[str]] = {}
        globals_: list[str] = []
        functions: list[FunctionDef] = []

        for node in tree.body:
            if isinstance(node, ast.Import):
                for a in node.names:
                    imports[a.asname or a.name.split(".")[0]] = a.name
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:                                  # relative import
                    pkg = module.split(".") if rel.name == "__init__.py" else module.split(".")[:-1]
                    pkg = pkg[: len(pkg) - (node.level - 1)] if node.level > 1 else pkg
                    base = ".".join(pkg + ([base] if base else []))
                for a in node.names:
                    imports[a.asname or a.name] = f"{base}.{a.name}" if base else a.name
            elif isinstance(node, ast.Assign):
                tname = _dotted(node.value.func) if isinstance(node.value, ast.Call) else None
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        globals_.append(t.id)
                        if tname:
                            instance_hints[t.id] = tname
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                globals_.append(node.target.id)
                tname = _type_name(node.annotation)
                if tname:
                    instance_hints[node.target.id] = tname

        def collect(body, cls: str | None, prefix: str):
            for node in body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    name = f"{prefix}{node.name}"
                    fn = FunctionDef(
                        id=f"{module}:{cls + '.' if cls else ''}{name}",
                        name=name, module=module, cls=cls, file=str(rel),
                        line=node.lineno, end_line=getattr(node, "end_lineno", node.lineno),
                        decorators=[_decorator_name(d) for d in node.decorator_list],
                        is_async=isinstance(node, ast.AsyncFunctionDef),
                    )
                    for arg in node.args.args + node.args.kwonlyargs:
                        t = _type_name(arg.annotation)
                        if t:
                            fn.local_hints[arg.arg] = t
                    class_hints = instance_hints if cls is None else _class_bucket(instance_hints, cls)
                    _FunctionBodyVisitor(fn, class_hints).visit(ast.Module(body=node.body, type_ignores=[]))
                    functions.append(fn)
                    collect(node.body, cls, f"{name}.")             # nested functions
                elif isinstance(node, ast.ClassDef):
                    bases[node.name] = [b for b in (_dotted(x) for x in node.bases) if b]
                    for stmt in node.body:                           # class-level annotations
                        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                            t = _type_name(stmt.annotation)
                            if t:
                                _class_bucket(instance_hints, node.name)[stmt.target.id] = t
                    collect(node.body, node.name, "")

        collect(tree.body, None, "")
        return ModuleFacts(module=module, file=str(rel), functions=functions,
                           imports=imports, instance_hints=instance_hints, bases=bases,
                           globals=globals_)


class _ClassBucket(dict):
    """dict view that stores class attribute hints as 'Class.attr' in the parent."""

    def __init__(self, parent: dict, cls: str):
        super().__init__()
        self.parent, self.cls = parent, cls

    def __setitem__(self, k, v):
        self.parent[f"{self.cls}.{k}"] = v


def _class_bucket(hints: dict, cls: str) -> dict:
    return _ClassBucket(hints, cls)
