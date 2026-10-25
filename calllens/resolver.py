"""Name resolution: `self.repo.save(...)`  ->  "app.repos.order_repo:OrderRepo.save".

This is deliberately HEURISTIC, not a full type checker. The strategy is a
chain of cheap lookups, each one only firing when the previous failed:

  1. bare name          `validate()`         -> same module, then imports, then nested def
  2. self.method        `self.validate()`    -> same class (incl. inherited later)
  3. self.attr.method   `self.repo.save()`   -> class hint "Class.repo" = OrderRepo -> OrderRepo.save
  4. local.method       `svc.place()`        -> function local hint / param annotation
  5. Module.func        `utils.slugify()`    -> import map to module -> func
  6. Class.method       `OrderRepo.save()` or `OrderRepo().save()` -> class method
  7. module global      `repo.save()`        -> module-level instance hint

Anything unresolved stays as an *external* participant (e.g. `requests.post`,
`db.execute`). Those still appear in the diagram: they are often the most
interesting boundaries (DB, HTTP, queue).
"""
from __future__ import annotations

from collections import defaultdict

from .model import FunctionDef, ModuleFacts


class Resolver:
    def __init__(self, modules: list[ModuleFacts]):
        self.modules = {m.module: m for m in modules}
        self.functions: dict[str, FunctionDef] = {}
        # (module, qualname) -> id      qualname = "Class.method" | "func"
        self.by_qual: dict[tuple[str, str], str] = {}
        # class simple name -> list of modules defining it
        self.class_modules: dict[str, list[str]] = defaultdict(list)
        # method name -> ids (last-resort lookup, flagged ambiguous)
        self.by_method: dict[str, list[str]] = defaultdict(list)

        for m in modules:
            for fn in m.functions:
                self.functions[fn.id] = fn
                self.by_qual[(fn.module, fn.qualname)] = fn.id
                if fn.cls and fn.module not in self.class_modules[fn.cls]:
                    self.class_modules[fn.cls].append(fn.module)
                self.by_method[fn.name].append(fn.id)

    # ------------------------------------------------------------------ helpers
    def _class_method(self, cls: str, method: str, prefer_module: str,
                      _seen: frozenset[str] = frozenset()) -> str | None:
        """Find Class.method; prefer the class defined in / imported by prefer_module.
        Falls back to base classes (inheritance), e.g. OrderRepo.get -> BaseRepo.get."""
        mod = self.modules.get(prefer_module)
        candidates: list[str] = []
        if mod and cls in mod.imports:                      # imported class -> exact module
            target = mod.imports[cls]
            tmod, _, tcls = target.rpartition(".")
            candidates.append(tmod)
            cls = tcls or cls
        candidates += [prefer_module] + self.class_modules.get(cls, [])
        for cm in candidates:
            fid = self.by_qual.get((cm, f"{cls}.{method}"))
            if fid:
                return fid
        # inheritance: look for the class's bases in the module that defines it
        key = f"{cls}"
        if key in _seen:
            return None
        for cm in candidates:
            facts = self.modules.get(cm)
            if not facts or cls not in facts.bases:
                continue
            for base in facts.bases[cls]:
                fid = self._class_method(base.split(".")[-1], method, cm, _seen | {key})
                if fid:
                    return fid
        return None

    def _module_func(self, module: str, func: str) -> str | None:
        return self.by_qual.get((module, func))

    def _import_target(self, module: str, name: str) -> str | None:
        mod = self.modules.get(module)
        return mod.imports.get(name) if mod else None

    # ------------------------------------------------------------------ main
    def resolve(self, caller: FunctionDef, expr: str) -> str | None:
        parts = expr.replace("()", "").split(".")
        mod = caller.module
        facts = self.modules.get(mod)

        # 1. bare name
        if len(parts) == 1:
            name = parts[0]
            if caller.cls and (fid := self.by_qual.get((mod, f"{caller.cls}.{caller.name}.{name}"))):
                return fid                                     # nested def inside method
            if fid := self.by_qual.get((mod, f"{caller.name}.{name}")):
                return fid                                     # nested def inside function
            if fid := self._module_func(mod, name):
                return fid
            if target := self._import_target(mod, name):
                tmod, _, tname = target.rpartition(".")
                if fid := self.by_qual.get((tmod, tname)):
                    return fid
                tfacts = self.modules.get(tmod)                # `import x from` / `export { a as b }`
                if tfacts and tname in tfacts.aliases and (fid := self.by_qual.get((tmod, tfacts.aliases[tname]))):
                    return fid
                # `from pkg import module` then `module(...)`? unlikely; fallthrough
            return None

        head, *rest = parts
        method = rest[-1]

        # 2. self.method
        if head == "self" and len(rest) == 1 and caller.cls:
            return self._class_method(caller.cls, method, mod)

        # 3. self.attr.method
        if head == "self" and len(rest) == 2 and caller.cls and facts:
            attr = rest[0]
            hinted = facts.instance_hints.get(f"{caller.cls}.{attr}")
            if hinted:
                return self._class_method(hinted.split(".")[-1], method, mod)
            return None

        if len(rest) == 1:
            # 4. local variable / parameter with type hint
            if head in caller.local_hints:
                cls = caller.local_hints[head].split(".")[-1]
                return self._class_method(cls, method, mod)
            # 6. Class.method / Class().method
            if head[:1].isupper():
                return self._class_method(head, method, mod)
            # 5. module.func via import
            if target := self._import_target(mod, head):
                if fid := self.by_qual.get((target, method)):
                    return fid
                # 5b. an instance exported by another module: `import { orderService } ...; orderService.place()`
                tmod, _, tname = target.rpartition(".")
                tfacts = self.modules.get(tmod)
                if tfacts:
                    tname = tfacts.aliases.get(tname, tname)
                    if tname in tfacts.instance_hints:
                        return self._class_method(tfacts.instance_hints[tname].split(".")[-1], method, tmod)
                # `import a.b` then `a.b.c.f()` handled by len(rest)>1 branch
            # 7. module-level instance
            if facts and head in facts.instance_hints:
                cls = facts.instance_hints[head].split(".")[-1]
                return self._class_method(cls, method, mod)
            return None

        # deeper dotted paths: try as module path  a.b.c.func  or  a.b.Class.method
        target = self._import_target(mod, head)
        if target:
            dotted = ".".join([target] + rest[:-1])
            if fid := self.by_qual.get((dotted, method)):
                return fid
            dmod, _, cls = dotted.rpartition(".")
            if fid := self.by_qual.get((dmod, f"{cls}.{method}")):
                return fid
        return None

    def receiver_of(self, caller: FunctionDef, expr: str, resolved: str) -> str | None:
        """Which class the call was made ON (for inherited methods)."""
        target = self.functions[resolved]
        if not target.cls:
            return None
        parts = expr.replace("()", "").split(".")
        cls = None
        if parts[0] == "self" and len(parts) == 2 and caller.cls:
            cls, mod = caller.cls, caller.module
        elif parts[0] == "self" and len(parts) == 3 and caller.cls:
            facts = self.modules.get(caller.module)
            cls = facts.instance_hints.get(f"{caller.cls}.{parts[1]}") if facts else None
        elif len(parts) == 2 and parts[0] in caller.local_hints:
            cls = caller.local_hints[parts[0]]
        elif len(parts) == 2:
            facts = self.modules.get(caller.module)
            cls = facts.instance_hints.get(parts[0]) if facts else (parts[0] if parts[0][:1].isupper() else None)
            if cls is None and parts[0][:1].isupper():
                cls = parts[0]
        if not cls:
            return None
        cls = cls.split(".")[-1]
        if cls == target.cls:
            return None
        mods = self.class_modules.get(cls)
        return f"{mods[0]}:{cls}" if mods else None

    def ext_kind_of(self, caller: FunctionDef, expr: str) -> str:
        head = expr.replace("()", "").split(".")[0]
        facts = self.modules.get(caller.module)
        if facts and head in facts.imports:
            return "lib"
        if head[:1].isupper():                 # Class(...) or Module.X: looks like a type
            return "lib"
        hint = facts.instance_hints.get(head, "") if facts else ""
        if hint and hint.split(".")[0] in facts.imports:   # module-level client of a library: `pool = new Pool()`
            return "lib"
        if facts and head in facts.globals:
            return "global"
        return "local"

    def resolve_all(self) -> dict:
        resolved = unresolved = 0
        for fn in self.functions.values():
            for call in fn.calls:
                call.resolved = self.resolve(fn, call.expr)
                if call.resolved:
                    call.receiver = self.receiver_of(fn, call.expr, call.resolved)
                    resolved += 1
                else:
                    call.ext_kind = self.ext_kind_of(fn, call.expr)
                    unresolved += 1
        return {"resolved_calls": resolved, "external_calls": unresolved}
