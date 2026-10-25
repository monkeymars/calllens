"""Which code editor opens source links, and how.

A browser cannot see the user's default editor, but the `seqgram` process can: it runs in the
user's terminal. detect() looks, in order, at
  1. $SEQGRAM_EDITOR (explicit), then $VISUAL / $EDITOR when they name a GUI editor we know
     (`code --wait` counts, `vim` is skipped: a server cannot open a terminal editor),
  2. the editor whose integrated terminal we run in (TERM_PROGRAM, ZED_TERM, JetBrains'
     TERMINAL_EMULATOR, macOS __CFBundleIdentifier, VS Code-fork paths in VSCODE_* vars),
  3. the first editor CLI found on PATH,
  4. nothing: "system", i.e. the OS default app for the file type (no line number).
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Callable, Mapping

# key -> (display name, argv that opens <file> at <line>)
EDITORS: dict[str, tuple[str, Callable[[str, int], list[str]]]] = {
    "vscode": ("VS Code", lambda f, n: ["code", "-g", f"{f}:{n}"]),
    "cursor": ("Cursor", lambda f, n: ["cursor", "-g", f"{f}:{n}"]),
    "windsurf": ("Windsurf", lambda f, n: ["windsurf", "-g", f"{f}:{n}"]),
    "antigravity": ("Antigravity", lambda f, n: ["antigravity", "-g", f"{f}:{n}"]),
    "zed": ("Zed", lambda f, n: ["zed", f"{f}:{n}"]),
    "pycharm": ("PyCharm", lambda f, n: ["pycharm", "--line", str(n), f]),
    "idea": ("IntelliJ IDEA", lambda f, n: ["idea", "--line", str(n), f]),
    "system": ("default app", lambda f, n: ["open", f] if sys.platform == "darwin" else ["xdg-open", f]),
}

# CLI names people put in $VISUAL / $EDITOR
_CLI_KEY = {"code": "vscode", "code-insiders": "vscode", "cursor": "cursor", "windsurf": "windsurf",
            "antigravity": "antigravity", "zed": "zed", "zeditor": "zed", "pycharm": "pycharm",
            "charm": "pycharm", "idea": "idea"}
# substrings of the host app's identity (bundle id, install path) -> key; forks before "vscode"
_HOST_HINTS = (("cursor", "cursor"), ("todesktop", "cursor"), ("windsurf", "windsurf"), ("exafunction", "windsurf"),
               ("antigravity", "antigravity"), ("zed", "zed"), ("pycharm", "pycharm"), ("intellij", "idea"),
               ("vscode", "vscode"), ("visual studio code", "vscode"))


def detect(env: Mapping[str, str] = os.environ) -> tuple[str, str]:
    """-> (editor key, why it was chosen)."""
    for var in ("SEQGRAM_EDITOR", "VISUAL", "EDITOR"):
        value = env.get(var, "").strip()
        if not value:
            continue
        name = Path(value.split()[0]).name.lower()
        if var == "SEQGRAM_EDITOR" and name in EDITORS:
            return name, f"${var}"
        if name in _CLI_KEY:
            return _CLI_KEY[name], f"${var}={value}"

    term = env.get("TERM_PROGRAM", "").lower()
    if env.get("ZED_TERM") or term == "zed":
        return "zed", "running in Zed's terminal"
    host = " ".join(env.get(k, "") for k in ("__CFBundleIdentifier", "VSCODE_GIT_ASKPASS_NODE", "VSCODE_IPC_HOOK_CLI",
                                              "GIT_ASKPASS", "TERMINAL_EMULATOR")).lower()
    if term == "vscode" or "jetbrains" in host or env.get("__CFBundleIdentifier"):
        for hint, key in _HOST_HINTS:
            if hint in host:
                return key, f"running in {EDITORS[key][0]}'s terminal"
        if term == "vscode":                       # VS Code itself, or a fork we do not know
            return "vscode", "running in VS Code's terminal"
        if "jetbrains" in host:
            return "pycharm", "running in a JetBrains terminal"

    for key, (_, argv) in EDITORS.items():
        if key != "system" and shutil.which(argv("", 0)[0]):
            return key, f"`{argv('', 0)[0]}` found on PATH"
    return "system", "no editor detected"
