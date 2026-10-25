# vibesequence

Turn your codebase into **zoomable sequence diagrams**, and use them as a map while you vibe code with an AI assistant. A call graph tells you *what is connected to what*. vibesequence tells you
*when a user does X, who calls whom, and in what order*.

![vibesequence viewer: GET /api/me as a sequence diagram](docs/preview.png)

## Why

When an AI assistant edits your code, it is hard to see what actually changed in the behavior.
vibesequence reads your code (no running it), finds the entry points (HTTP routes, CLI commands, handlers),
and draws each one as a sequence diagram:

- **Zoom** between package, module, class and function level. Click a blue call to open it on its own.
- **Play** a flow arrow by arrow, with the `file:line` of each step.
- **Live**: while the AI edits, changed steps turn green and you can play only the changes.
- **AI friendly**: the same flow is available as compact text, so your assistant reads one diagram instead of eight files.

Supports Python (stdlib only) and TypeScript / JavaScript (Next.js, Express).

## Quick start

```bash
pip install -e .            # Python projects
pip install -e ".[ts]"      # + TypeScript / JavaScript

vibesequence build .        # parse the repo into vibesequence-out/
vibesequence serve          # open the viewer at http://127.0.0.1:8765
```

Try it on the bundled examples: `vibesequence build examples`.

## Commands

| command | what it does |
|---|---|
| `vibesequence build .` | parse the repo into `vibesequence-out/` (incremental) |
| `vibesequence serve` | live viewer; source links open your editor |
| `vibesequence list` | list all flows |
| `vibesequence show "POST /orders"` | one flow as Mermaid (`--format text` for the AI-friendly tree) |
| `vibesequence callers OrderRepo.create` | who calls this, and which flows are affected |
| `vibesequence diff` | which flows changed in the last build |
| `vibesequence install` | add instructions to `CLAUDE.md` + a Claude Code hook that shows changed flows after each edit |

## Viewer shortcuts

`Space` play · `←` `→` step · `[` `]` level · `Z` zen · `D` dark mode · `\` sidebar · `⌘`+scroll zoom · drag to pan

## Roadmap

- [ ] **Go**: tree-sitter extractor, entry points for `net/http`, Gin, Echo, Chi and Cobra commands
- [ ] **Java**: Spring controllers
- [ ] **Smarter resolution**: return-type inference, dependency injection, interface → implementations
- [ ] **Diff against a git ref**: review a PR as sequence changes, not line changes
- [ ] **MCP server**: let the assistant call `list_flows` / `show_flow` instead of reading files
- [ ] **Runtime traces** (optional): mark the paths that actually ran

## Contributing

Open for contributions! Pick a roadmap item, fix a bug, or improve the docs. Issues and pull requests are welcome.

- **New language**: add one extractor class in `vibesequence/extractors/` that outputs `ModuleFacts`
  (see `extractors/base.py`). The resolver, viewer and CLI work without changes.
- **Run the tests** before opening a PR: `python tests/test_flow.py`
- Not sure where to start? Open an issue and say what you want to build.

## Learn more

How the pipeline works (extract → resolve → expand → project → render), the live viewer, editor links
and scaling notes: [docs/how-it-works.md](docs/how-it-works.md) (Indonesian).
