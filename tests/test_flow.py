"""Smoke check: python tests/test_flow.py  (also runs under pytest)."""
import contextlib
import io
import sys
import tempfile
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vibesequence import builder, cli  # noqa: E402
from vibesequence.flow import expand, project  # noqa: E402
from vibesequence.render.mermaid import render  # noqa: E402

APP = textwrap.dedent('''
    def save(x): pass
    def notify(x): pass
    def audit(x): pass

    def handle(user, items):
        if not user:
            notify(user)
        if user.admin:
            audit(user)
        for item in items:
            save(item)

    def main():
        handle(None, [])
''')


def _run(*argv) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cli.main(list(argv))
    return buf.getvalue()


def test_flow():
    with tempfile.TemporaryDirectory() as d:
        root, out = Path(d, "src"), Path(d, "out")
        root.mkdir()
        (root / "app.py").write_text(APP)
        graph = builder.build(root, out)
        again = builder.build(root, out)                   # live mode relies on: same code -> same version
        assert not again.stats["changed"] and again.stats["version"] == graph.stats["version"]

        mm = render(project(graph, expand(graph, "app:handle", 3), "function"), "function")
        assert "opt if not user" in mm, mm
        assert "opt if user.admin" in mm, mm              # two separate ifs stay two fragments
        assert "loop for item in items" in mm, mm

        text = _run("show", "app:handle", "--out", str(out), "--format", "text", "--level", "function")
        assert "save  app.py:2" in text, text             # definition location for the agent

        callers = _run("callers", "app:save", "--out", str(out))
        assert "app:handle  app.py:12" in callers, callers
        assert "main()" in callers, callers                # flow that reaches it

        (root / "app.py").write_text(APP.replace("        save(item)", "        save(item)\n        notify(item)"))
        hook = _run("build", str(root), "--out", str(out), "--hook")
        assert builder.load(out).stats["version"] != graph.stats["version"]
        assert "+" in hook and "notify" in hook, hook
        assert _run("build", str(root), "--out", str(out), "--hook") == ""   # no change -> silent


def test_serve_open_guards():
    """POST /__open must refuse other origins and files outside the project (never launches an editor)."""
    import json
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer
    from vibesequence import editor, serve

    editor.EDITORS["missing"] = ("Missing", lambda f, n: ["vibesequence-no-such-editor", f])
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "app.py").write_text("x = 1\n")
        srv = ThreadingHTTPServer(("127.0.0.1", 0), serve._handler(root, root.resolve(), lambda: None, "missing"))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"

        def post(body, origin=base):
            req = urllib.request.Request(base + "/__open", json.dumps(body).encode(), {"Origin": origin})
            try:
                return urllib.request.urlopen(req).status
            except urllib.error.HTTPError as e:
                return e.code

        assert post({"file": "app.py", "line": 1}, "https://evil.example") == 403
        assert post({"file": "../../etc/passwd", "line": 1}) == 403
        assert post({"file": "app.py", "line": "x"}) == 400
        assert post({"file": "app.py", "line": 1}) == 404                          # CLI not on PATH
        assert post({"file": "app.py", "line": 1, "editor": "rm -rf /"}) == 400    # viewer pick: table keys only
        assert post({"file": "app.py", "line": 1, "editor": "missing"}) == 404     # a valid pick is used
        srv.shutdown()


def test_detect_editor():
    from vibesequence.editor import detect
    assert detect({"TERM_PROGRAM": "zed", "ZED_TERM": "true"})[0] == "zed"
    assert detect({"TERM_PROGRAM": "vscode", "__CFBundleIdentifier": "com.microsoft.VSCode"})[0] == "vscode"
    assert detect({"TERM_PROGRAM": "vscode", "__CFBundleIdentifier": "com.todesktop.230313mzl4w4u92"})[0] == "cursor"
    assert detect({"VISUAL": "code --wait"})[0] == "vscode"
    assert detect({"EDITOR": "vim", "TERM_PROGRAM": "vscode"})[0] == "vscode"           # terminal editors skipped
    assert detect({"TERMINAL_EMULATOR": "JetBrains-JediTerm", "__CFBundleIdentifier": "com.jetbrains.intellij"})[0] == "idea"
    assert detect({"VIBESEQUENCE_EDITOR": "pycharm", "TERM_PROGRAM": "zed"})[0] == "pycharm"  # explicit wins


def test_install_keeps_user_content():
    """install edits the user's CLAUDE.md and Claude settings: replace only our section, add the hook once."""
    import json
    with tempfile.TemporaryDirectory() as d:
        md, settings = Path(d, "CLAUDE.md"), Path(d, ".claude", "settings.local.json")
        md.write_text("# Proj\n\nKeep tests green.\n\n## vibesequence (old)\nold text\n\n## Deploy\nmake deploy\n")
        settings.parent.mkdir()
        settings.write_text(json.dumps({"permissions": {"allow": ["Bash(make:*)"]}}))
        for _ in range(2):
            with contextlib.redirect_stdout(io.StringIO()):
                cli._install(md)
                cli._install_hook(settings)
        text, conf = md.read_text(), json.loads(settings.read_text())
        assert "Keep tests green." in text and "## Deploy" in text and "old text" not in text
        assert text.count("## vibesequence") == 1
        assert conf["permissions"] == {"allow": ["Bash(make:*)"]}
        assert len(conf["hooks"]["PostToolUse"]) == 1


def test_import_root_and_ranking():
    """Built one folder too high, imports still resolve; roots that drive more code rank first."""
    with tempfile.TemporaryDirectory() as d:
        pkg = Path(d, "services", "app")
        pkg.mkdir(parents=True)
        (pkg / "lib.py").write_text("def f(): g()\ndef g(): h()\ndef h(): pass\n")
        (pkg / "main.py").write_text(textwrap.dedent('''
            from app.lib import f, g, h

            def run():
                f()
                g()
                h()

            def _cb():
                h()
        '''))
        graph = builder.build(Path(d), Path(d, "out"))
        assert graph.stats["import_roots"] == ["services"], graph.stats
        assert "app.main:run" in graph.functions
        assert graph.functions["app.main:run"].calls[0].resolved == "app.lib:f"
        roots = [e.id for e in graph.entries if e.kind == "root"]
        assert roots.index("app.main:run") < roots.index("app.main:_cb"), roots


def test_typescript_next_and_express():
    """examples/next_shop: Next.js route files + Express routes resolve through classes, inheritance,
    path aliases and an imported instance. Skipped without the optional tree-sitter dependency."""
    try:
        import tree_sitter_typescript  # noqa: F401
    except ImportError:
        print("skip test_typescript_next_and_express: pip install 'vibesequence[ts]'")
        return
    example = Path(__file__).resolve().parents[1] / "examples" / "next_shop"
    with tempfile.TemporaryDirectory() as d:
        graph = builder.build(example, Path(d))
        labels = {e.label for e in graph.entries if e.kind == "route"}
        assert {"POST /api/orders", "GET /api/orders/[id]", "GET /api/me", "GET /orders/:id", "POST /orders/:id/cancel"} <= labels, labels
        text = _run("show", "POST /api/orders", "--out", d, "--format", "text", "--level", "function", "--depth", "6")
        for step in ("auth.requireUser", "OrderService.place", "BaseRepo.get", "for each in items",
                     "PricingEngine.priceLine", "OrderRepo.markFailed", "fetch", "pool.query", "orders/route.POST"):
            assert step in text, (step, text)


def test_go_shop():
    """examples/go_shop: net/http + cobra entry points; calls resolve across packages (go.mod imports),
    sibling files of one package, embedded structs, NewX constructors and declared result types.
    Skipped without the optional tree-sitter-go dependency."""
    try:
        import tree_sitter_go  # noqa: F401
    except ImportError:
        print("skip test_go_shop: pip install 'vibesequence[go]'")
        return
    example = Path(__file__).resolve().parents[1] / "examples" / "go_shop"
    with tempfile.TemporaryDirectory() as d:
        graph = builder.build(example, Path(d))
        labels = {e.label for e in graph.entries}
        assert {"POST /orders", "GET /orders/{id}", "POST /orders/{id}/cancel", "GET /healthz",
                "cli restock", "cli migrate", "main()"} <= labels, labels
        text = _run("show", "POST /orders", "--out", d, "--format", "text", "--level", "function", "--depth", "6")
        for step in ("OrderHandler.Create", "auth.RequireUser", "OrderService.Place", "service.validate",
                     "for _, it := range items", "ProductRepo.Get", "BaseRepo.Find", "Conn.QueryRow",
                     "sql.QueryRowContext", "Pricing.Line", "Gateway.Charge", "client.Post", "on error",
                     "OrderRepo.MarkFailed", "goroutine", "events.Publish"):
            assert step in text, (step, text)
        assert "fmt." not in text and "json." not in text, text        # plumbing packages are filtered
        cancel = _run("show", "POST /orders/{id}/cancel", "--out", d, "--format", "text", "--level", "function")
        assert "Gateway.Refund" in cancel, cancel                       # field hint from a sibling file
        health = _run("show", "GET /healthz", "--out", d, "--format", "text", "--level", "function")
        assert "Conn.Ping" in health, health                            # conn := db.Open(): result type


if __name__ == "__main__":
    test_go_shop()
    test_typescript_next_and_express()
    test_import_root_and_ranking()
    test_flow()
    test_serve_open_guards()
    test_detect_editor()
    test_install_keeps_user_content()
    print("ok")
