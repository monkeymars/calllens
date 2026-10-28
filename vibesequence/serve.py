"""`vibesequence serve`: the viewer over http://127.0.0.1 plus POST /__open, which opens a source file
in your editor through the editor's own CLI (`code -g file:line`, ...).

Why a server: editor URL schemes (vscode://file/...) are dropped by embedded browsers such as
VS Code's built-in browser (a webview), so a plain link cannot open the file there. A same-origin
HTTP call to this process works in any browser.

The editor is detected once at start-up (see editor.py). The viewer's "Open in" dropdown may name another
one, but only by key of that fixed table: the page picks an editor, never a command.

Safety: binds to 127.0.0.1 only; every request must carry Host 127.0.0.1:<port> or localhost:<port>
(blocks DNS rebinding: a website whose domain resolves to 127.0.0.1 sends its own Host); POSTs must be
same-origin (Origin must match Host, so other websites cannot trigger them); /__open only opens source
files inside the built project (never e.g. a .command file through the OS default app); the editor
comes from a fixed table and is started without a shell.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from . import editor as editors
from .extractors.base import get_extractors


def _handler(out: Path, root: Path, refresh: Callable[[], object], editor: str):
    sources = set(get_extractors())               # the viewer only links to files vibesequence parsed
    lock = threading.Lock()                        # page loads and polls can arrive together: one build at a time

    def fresh():
        with lock:
            return refresh()

    class Handler(SimpleHTTPRequestHandler):
        def _foreign_host(self):
            port = self.server.server_address[1]
            return self.headers.get("Host") not in (f"127.0.0.1:{port}", f"localhost:{port}")

        def do_GET(self):
            if self._foreign_host():
                return self._reply(403, "unexpected Host header")
            path = self.path.split("?")[0]
            if path == "/__version":               # live viewer polls this: rebuild, report the graph version
                graph = fresh()
                return self._reply(200, graph.stats["version"] if graph else "")
            if path in ("/", "/index.html"):
                fresh()                            # rebuild incrementally, so a reload shows current code
            super().do_GET()

        def do_POST(self):
            if self.path not in ("/__open", "/__baseline"):
                return self.send_error(404)
            if self._foreign_host():
                return self._reply(403, "unexpected Host header")
            if self.headers.get("Origin") != f"http://{self.headers.get('Host', '')}":
                return self._reply(403, "cross-origin request refused")
            if self.path == "/__baseline":         # "Mark as seen": changes are now measured from here
                with lock:
                    shutil.copyfile(out / "graph.json", out / "graph.base.json")
                return self._reply(200, "ok")
            try:
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                path, line = (root / req["file"]).resolve(), int(req["line"])
                # the viewer's "Open in" pick, else the detected editor; only keys of the fixed table
                name, argv_for = editors.EDITORS[req.get("editor") or editor]
                argv = argv_for(str(path), line)
            except (ValueError, KeyError, TypeError):
                return self._reply(400, "bad request")
            if not path.is_relative_to(root) or not path.is_file():
                return self._reply(403, "file is outside the project")
            if path.suffix not in sources:
                return self._reply(403, "not a source file")
            exe = shutil.which(argv[0])
            if not exe:
                return self._reply(404, f"`{argv[0]}` is not on PATH")
            subprocess.Popen([exe, *argv[1:]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._reply(200, f"opened in {name}")

        def _reply(self, code: int, msg: str):
            body = json.dumps({"ok": code == 200, "message": msg}).encode()
            self.send_response(code)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):              # quiet: one line per request is noise here
            pass

    return partial(Handler, directory=str(out))


def run(out: Path, port: int, refresh: Callable[[], object]) -> None:
    graph = refresh()
    root = Path(graph.stats["root"]).resolve()
    # baseline for "what changed while I was vibe coding": the graph when serve started
    shutil.copyfile(out / "graph.json", out / "graph.base.json")
    editor, why = editors.detect()
    handler = _handler(out, root, refresh, editor)
    for candidate in range(port, port + 20):       # port taken (another serve?): use the next free one
        try:
            server = ThreadingHTTPServer(("127.0.0.1", candidate), handler)
            break
        except OSError:
            continue
    else:
        raise SystemExit(f"vibesequence: ports {port}-{port + 19} are all in use; pass --port")
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"vibesequence: serving {out}/ at {url}  (Ctrl+C to stop)")
    print(f"  source links open in {editors.EDITORS[editor][0]} ({why}); change it in the viewer's \"Open in\" menu")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
