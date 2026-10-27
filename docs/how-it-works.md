# How vibesequence works

## In one minute

vibesequence **reads** your code without running it, then draws *who calls whom, in order* as a
sequence diagram. It takes six steps:

1. **Read each file.** For every function, note every call it makes, **in order**, and whether the call
   sits inside an `if`, a loop or a `try/except`.
2. **Connect calls to the real functions.** The text `self.orders.create` is traced to the function it
   actually reaches, `OrderRepo.create`. Calls that can't be traced (into a library like `requests`) are
   kept as "external".
3. **Find the starting points.** Every route (`POST /orders`), CLI command or handler is one "flow". This
   is where the **User** actor starts.
4. **Walk from the start.** From `POST /orders`, follow the calls one by one, down to a chosen depth.
   The result is a tree of steps in order.
5. **Zoom.** The core idea: the same tree can be viewed from different distances.
   - far (package): `api → services → repos → infra`
   - closer (class): `OrderService → OrderRepo → PaymentGateway`
   - closest (function): every method is its own lane

   Calls that stay inside one box are hidden; only calls between boxes become arrows.
6. **Draw.** For **you**, a diagram you can zoom, click and play step by step. For the **AI**, a compact
   text version with the `file:line` of every step, so it doesn't have to read many files.

**Analogy:** it's like drawing a parcel's delivery route from the address labels at each post office,
without actually sending anything. You can view the map at city scale (package) or street scale (function).

**Limit:** because it only reads the code, both branches of an `if` are drawn. It's a map of every
possible path, not a recording of one run.

## The engine in detail

```
source files ──1. extract──▶ facts per file ──2. resolve──▶ ordered call graph (graph.json)
                                                                │
                                                       3. detect entry points ("user flows")
                                                                │  per flow, on demand:
                                     4. expand ──▶ call tree ──5. project (zoom) ──▶ messages ──6. render ──▶ Mermaid / text
```

Steps 1–3 run on `vibesequence build` and are cached per file. Steps 4–6 run each time you open a flow
or change the zoom, so they are cheap. All examples below are real output from `examples/shop_api`.

### 1. Extract: calls in order, with their context

`extractors/python_ast.py` (Python's `ast`, no dependencies) and `extractors/typescript_ts.py`
(tree-sitter, `pip install "vibesequence[ts]"`) record, for every function, each call as a `CallSite`
(`model.py`):

- **`expr`**: the call as written, e.g. `self.payments.charge`;
- **`order`**: its position in the body. Evaluation order is respected: `a.b().c(d())` is recorded as
  `a.b`, `d`, `a.b().c`;
- **`context`**: the control flow around it, e.g. `["loop:for item in items"]`, `["except:Exception"]`.
  The condition text is kept, so the diagram can say `alt if not user`, not just `alt`.

They also record cheap type hints (`self.orders = OrderRepo()`, `svc: OrderService`), base classes and
imports. `OrderService.place` after extraction and resolution:

```
 2  self.products.get              [loop:for item in items]  -> BaseRepo.get  (receiver ProductRepo)
 6  self.orders.create             []                        -> OrderRepo.create
 7  self.payments.charge           [try]                     -> PaymentGateway.charge
 8  self.orders.mark_failed        [except:Exception]        -> OrderRepo.mark_failed
 9  self.products.decrement_stock  [loop:for item in items]  -> ProductRepo.decrement_stock
```

Everything is per file, so results are cached by content hash in `cache.json`. A rebuild only re-parses
changed files (Flask, 24 files and 350 functions: 0.07 s cold, 0.016 s warm).

**TypeScript / JavaScript** maps onto the same model, so everything after this step is shared:

- A module is its file path: `lib/repos/orderRepo.ts` becomes `lib.repos.orderRepo`.
- Imports resolve like `tsc`: relative paths, `paths` aliases from the nearest `tsconfig.json`/`jsconfig.json`
  (`@/lib/...`), and `index` files. Packages such as `next/server` become library participants.
- `this.repo.save()` resolves through field types: constructor parameter properties, typed fields,
  `this.x = new X()`, and exported instances (`export const orderService = new OrderService(...)`).
- Callbacks passed to `map`/`forEach`/`filter` are drawn as a `loop`, and `.catch(cb)` as an error branch.
- Generic framework file names keep their folder: `orders/route`, `[id]/route`.

### 2. Resolve: from `self.orders.create` to a definition

`resolver.py` tries a chain of cheap rules and stops at the first match:

1. plain name `validate()` → same module → imports → nested def;
2. `self.method()` → own class → base classes (recursively);
3. `self.attr.method()` → type hint `self.attr = OrderRepo()` → `OrderRepo.method`;
4. `local.method()` → parameter annotation or local assignment;
5. `module.func()` → import map;
6. `Class.method()` / `Class().method()`;
7. module-level object `repo.save()` → module hint.

There are two details:

- **Inherited methods keep the real receiver.** `self.products.get` resolves to `BaseRepo.get` with
  receiver `ProductRepo`, so the diagram says `ProductRepo.get`.
- **Unresolved calls are kept and classified:**
  - `lib`: an imported name, e.g. `requests.post`;
  - `global`: a module-level object, e.g. `_conn.execute`;
  - `local`: a local variable, e.g. `resp.json()`.

  The viewer's **Externals** filter uses this classification. By default it shows `lib` only, because
  the boundaries to databases, HTTP and queues matter most in a sequence diagram.

**Import root.** If you build one folder too high (a monorepo, or `vibesequence build .` above the project),
`import app.repos.base` no longer matches the module `services.app.repos.base`. vibesequence detects the
prefix (three or more imports matching one folder) and strips it. It warns instead of guessing when the
match is ambiguous.

The output is `graph.json`: a call graph that keeps order and context.

### 3. Entry points: where "User" starts

`entrypoints.py` assigns kinds in priority order:

1. route decorators: `@router.post("/orders")` → `POST /orders`;
2. Next.js `app/**/route.ts` exports, `pages/api/**`, and Express `router.get('/x', …)`;
3. CLI commands (click, typer);
4. handlers (`@task`, `on_*`, `handle_*`);
5. `main`;
6. functions nothing calls (`root`), ranked by how much of your code they reach, with penalties for
   private and nested functions.

`list`, `FLOWS.md` and the sidebar show the top 20 roots. The rest are behind "Show N more".

### 4. Expand: call graph → call tree

`flow.py` (`expand`) walks from the entry point depth-first, in source order:

- a call already on the current path is marked `↺` (cycle);
- anything deeper than `--depth` is marked `…`, the "click to zoom in" signal;
- inside an inherited method, `self` keeps its dynamic class, so `self.x()` is drawn on the subclass.

Trees are built lazily per flow and never stored, so cost is bounded by `--depth`.

### 5. Project: the zoom

Every function has a path `package > module > class > function`. A zoom level picks which rung is the
**participant** (`flow.py`, `participant_of`):

| level | participant for `OrderRepo.create` |
|---|---|
| function | `app.repos.order_repo:OrderRepo.create` |
| class | `OrderRepo` |
| module | `order_repo` |
| package | `app.repos` |

Projection then works like this:

- A call that crosses participants becomes an **arrow**.
- A call that stays inside one participant is **flattened**. Its children move up and inherit its
  control-flow context, so a `for` loop inside a hidden helper still shows as a `loop` at module level.
- Identical consecutive leaf calls collapse: `log.info ×3`.

One tree, many views. At package level, `POST /orders` reads:

```
app.api ->> app.services: OrderService.place
    loop for item in items
        app.services ->> app.repos: ProductRepo.get
    ...
    opt on error Exception
        app.services ->> app.repos: OrderRepo.mark_failed
```

Clicking a blue arrow in the viewer expands again with that call as the new root. The breadcrumb is the
stack of those roots.

### 6. Render: Mermaid for people, text for the AI

`render/mermaid.py` writes a Mermaid `sequenceDiagram`. Consecutive messages with the same context are
wrapped in fragments:

- `if` with an `else` → `alt / else`;
- `if` alone → `opt if …`;
- a loop → `loop for item in items`;
- `try` / `except` → `opt try` / `opt on error PaymentError`.

Diagrams stop at 150 messages with a "zoom in for more" note.

`render/text.py` prints the same flow as an indented tree with the `file:line` of every step. It is
about half the size of the Mermaid version, and the assistant knows which lines to open without grepping:

```
OrderService.place  app/services/order_service.py:15
  for item in items
    ProductRepo.get …  app/repos/base.py:7
  try
    PaymentGateway.charge …  app/infra/payment.py:8
  on error Exception
    OrderRepo.mark_failed …  app/repos/order_repo.py:17
```

Mermaid was chosen because a browser can render it **and** an LLM can read it directly. The expand,
project and render steps exist twice: in Python (`flow.py`, `render/mermaid.py`) for the CLI, and in
JavaScript inside `render/viewer.html` for the viewer. Both are tested to produce identical text, so
what you see is what the AI reads.

## Around the engine

### The viewer

`render/html.py` embeds `graph.json` into one self-contained `index.html`. Zooming and level changes run
in the browser with no server. The only external file is Mermaid, loaded from cdnjs with an integrity hash.

**Play** walks a flow one arrow at a time:

- the current arrow is highlighted and the next ones are dimmed;
- the two participants involved are highlighted;
- the caption shows `A → B: method`, its `file:line`, and the fragment it sits in.

Keys: `Space` play/pause, `←` `→` step, `Esc` reset. Speed is 0.5×, 1× or 2×.

### Live mode: see what changed

`vibesequence serve` serves the viewer on `127.0.0.1:8765`. Every 1.5 s the page asks the server to
rebuild incrementally, and when a flow changes, the diagram re-renders without a reload.

Changes are measured against a **baseline**, the graph when `serve` started:

- new steps are drawn green, and removed steps are listed in a banner;
- changed flows get a green dot in the sidebar;
- **Play changes** plays only the new steps, and **Mark as seen** moves the baseline to now.

The diff is a longest-common-subsequence over the sequence of arrows. Order counts and line numbers don't,
so moving code or adding comments is not a flow change.

### Opening source in your editor

The **Source** link and the `file:line` in the Play caption open your editor at that line. A browser can't
know your editor, so `vibesequence build` / `serve` detects it from the terminal they run in
(`editor.py`), in this order:

1. `$VIBESEQUENCE_EDITOR`, then `$VISUAL` / `$EDITOR` when they name a known GUI editor;
2. the editor whose terminal you are in;
3. the first editor CLI on `PATH`;
4. the OS default app.

On `file://` pages the link uses the editor's URL scheme (`vscode://file/…`). Embedded browsers such as
VS Code's Simple Browser block those, so under `serve` the page asks the server to run the editor's CLI
instead (`code -g file:line`).

The server only:
- listens on 127.0.0.1;
- accepts requests whose Host is `127.0.0.1` or `localhost`;
- refuses cross-site POSTs;
- opens source files inside the project.

### The AI assistant loop

`vibesequence install` does two things:

- It adds a section to `CLAUDE.md` that tells the assistant to read `FLOWS.md` and `vibesequence show`
  before reading source files.
- It adds a Claude Code `PostToolUse` hook in `.claude/settings.local.json`.

After every edit, the hook runs `vibesequence build . --hook`, which reports flows that changed, at
function level and ignoring line numbers:

```
vibesequence: flow changed
## POST /orders
⋮
+      if order.total > 1000
+        OrderRepo.mark_failed
       events.publish
```

`vibesequence diff` shows the same report at any time.

## Limits

- **It's static.** Both branches of every `if` are shown. It maps possible paths, not one execution.
- **Resolution is heuristic, with no type checker:**
  - dependency injection, interface → implementation dispatch, and functions passed as values often stay
    unresolved and show up as grey external calls;
  - NestJS decorators, React component trees, and Express handlers imported from another file are not
    detected as entry points yet.
- See the [roadmap](../README.md#roadmap) for what's planned.

## Code map

```
vibesequence/
  model.py                    dataclasses: CallSite, FunctionDef, ModuleFacts, EntryPoint, Graph
  extractors/base.py          Extractor protocol + registry by file extension
  extractors/python_ast.py    Python extractor (ast): evaluation order, context, type hints
  extractors/typescript_ts.py TS/JS extractor (tree-sitter, optional): tsconfig imports, Next.js/Express routes
  resolver.py                 name resolution, inheritance, receivers, external classification
  entrypoints.py              route / cli / handler / main / root detection and ranking
  builder.py                  walk the repo, hash cache, import roots, write graph.json
  flow.py                     expand (call tree) + project (zoom) + collapse
  render/mermaid.py           Mermaid sequenceDiagram + alt/loop/opt fragments
  render/text.py              text format for the AI: tree + file:line
  render/html.py              embed graph.json into the viewer
  render/viewer.html          the viewer (JS port of flow.py + mermaid.py)
  serve.py                    vibesequence serve: viewer over http + open files via the editor CLI
  editor.py                   detect the user's editor and how to open it
  cli.py                      build / list / show / callers / diff / serve / install
```

Adding a language means writing one extractor that outputs `ModuleFacts`. The resolver, renderers and
viewer don't change.
