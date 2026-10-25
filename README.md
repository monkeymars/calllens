# calllens

Ubah codebase menjadi **user sequence diagram yang bisa di-zoom**, lalu pakai
diagram itu sebagai peta saat vibe coding bersama AI assistant.

Graphify menjawab *"apa berhubungan dengan apa"* (graph). calllens menjawab
*"kalau user melakukan X, siapa memanggil siapa, dalam urutan apa"* (sequence).

```
pip install -e .                   # Python project (stdlib saja)
pip install -e ".[ts]"             # + TypeScript / JavaScript (tree-sitter)
calllens build .                    # parse repo  ->  calllens-out/
open calllens-out/index.html        # viewer interaktif: zoom in/out per flow
calllens list                       # daftar flow (entry point)
calllens show "POST /orders" --level class --depth 4     # Mermaid untuk satu flow
calllens show "POST /orders" --format text               # tree ringkas + file:line (paling hemat untuk AI)
calllens callers OrderRepo.create   # siapa memanggil fungsi ini + flow mana yang terdampak
calllens diff                       # flow apa yang berubah di build terakhir
calllens serve                      # viewer di http://127.0.0.1:8765, link Source membuka file di editor
calllens install                    # instruksi di CLAUDE.md + hook Claude Code (flow yang berubah tampil tiap edit)
```

Output `calllens-out/`:

| file | untuk siapa | isi |
|---|---|---|
| `index.html` | manusia | viewer: pilih flow, pilih level (package / module / class / function), klik pesan untuk zoom in, breadcrumb untuk zoom out |
| `FLOWS.md` | AI assistant | indeks: satu baris per flow (label, id, `file:line`); diagram diambil per flow lewat `show` |
| `graph.prev.json` | `calllens diff` | graph sebelum perubahan terakhir |
| `graph.json` | tooling | call graph terurut: fungsi, call site, konteks control-flow |
| `cache.json` | calllens | hash per file untuk build inkremental |

Bahasa yang didukung:

| bahasa | extractor | dependency | entry point yang dikenali |
|---|---|---|---|
| Python | `extractors/python_ast.py` (stdlib `ast`) | tidak ada | route FastAPI/Flask, CLI click/typer, task, `main` |
| TypeScript / JavaScript | `extractors/typescript_ts.py` (tree-sitter) | `pip install calllens[ts]` | Next.js `app/**/route.ts` (`export async function POST`, `export const GET = …`), `pages/api/**`, Express `router.get('/x', …)` |

Bahasa lain ditambahkan lewat satu kelas extractor yang mengeluarkan `ModuleFacts` (lihat *Scaling*).

### TypeScript / JavaScript

Extractor TS memetakan kode ke model yang sama dengan Python, jadi viewer, Play, live, diff,
`callers` dan hook bekerja tanpa perubahan:

* modul = path file (`lib/repos/orderRepo.ts` → `lib.repos.orderRepo`); import diselesaikan seperti
  `tsc`: relatif `./x`, alias `paths` dari `tsconfig.json`/`jsconfig.json` terdekat (`@/lib/...`),
  file `index`; package (`next/server`, `pg`) jadi participant library;
* `this.repo.save()` diselesaikan lewat tipe field: parameter property constructor
  (`private repo: OrderRepo`), field bertipe, `this.x = new X()`; juga instance yang di-export modul
  lain (`export const orderService = new OrderService(...)`) dan default export;
* callback `map/forEach/filter/...` digambar sebagai `loop` di fungsi pemanggilnya, `.catch(cb)`
  sebagai cabang error; global yang noise (`console`, `JSON`, `Promise.all`) dibuang, `fetch`
  dipertahankan sebagai boundary HTTP; klien library di level modul (`const pool = new Pool()`)
  tampil sebagai library;
* file framework yang namanya generik tampil bersama foldernya: `orders/route`, `[id]/route`.

Contoh: `examples/next_shop` (Next.js App Router + satu router Express, domain sama dengan
`examples/shop_api`).

Batasan prototipe: NestJS decorator, React component tree, dan handler Express yang di-import
dari file lain belum dikenali sebagai entry point; resolusi tipe tetap heuristik (tanpa `tsc`).

---

## Cara kerja (pipeline)

```
 source files
      │  1. extract   (per file, di-cache per hash)
      ▼
 ModuleFacts  = fungsi + CALL SITE TERURUT + konteks (if/loop/try) + hint tipe + import
      │  2. resolve    (heuristik: nama -> definisi, termasuk inheritance)
      ▼
 Graph        = call graph dengan URUTAN  (ini bedanya dengan graph biasa)
      │  3. detect entries   (route, cli, handler, main, root tanpa caller)
      ▼
 Entry points = daftar "user flow"
      │  4. expand    (DFS terurut dari entry, batas depth, cycle guard)
      ▼
 Call TREE    = satu pohon per flow; waktu turun ke bawah, nesting = activation
      │  5. project   (ZOOM: pilih rung hierarki sebagai participant, calls internal diratakan)
      ▼
 Message tree = pesan antar participant pada level tertentu
      │  6. render    (Mermaid sequenceDiagram; fragment alt/loop/opt dari konteks)
      ▼
 Mermaid text ->  viewer (manusia)  /  FLOWS.md & `show` (AI)
```

### 1. Extract: kenapa urutan dan konteks yang direkam

Sequence diagram butuh tiga hal yang tidak ada di call graph biasa:

* **urutan** pemanggilan di dalam satu body (`calls[i].order`),
* **konteks control-flow** setiap call (`["loop", "try"]`) supaya bisa dirender sebagai
  `loop` / `alt` / `opt`,
* **urutan evaluasi** yang benar: Python mengevaluasi receiver dan argumen dulu,
  jadi `a.b().c(d())` direkam sebagai `a.b`, `d`, `a.b().c`.

Extractor juga mencatat *petunjuk tipe* murah: `self.repo = OrderRepo()`,
`x: Foo`, `def f(svc: OrderService)`, plus daftar base class dan import map.
Semua ini per file, jadi bisa di-cache dan diparalelkan.

### 2. Resolve: dari teks `self.repo.save` ke definisi

Rantai lookup murah, berhenti di yang pertama cocok:

1. nama polos `validate()` → modul yang sama → import → nested def
2. `self.method()` → kelas sendiri → base class (inheritance, rekursif)
3. `self.attr.method()` → hint `Class.attr = OrderRepo` → `OrderRepo.method`
4. `local.method()` → anotasi parameter / assignment lokal
5. `module.func()` → import map
6. `Class.method()` / `Class().method()`
7. objek level modul `repo.save()` → hint modul

Yang tidak terpecahkan **tetap dipertahankan** sebagai participant eksternal
dan diklasifikasikan: `lib` (nama ter-import: `requests.post`), `global`
(objek modul: `_conn.execute`), `local` (variabel lokal: `resp.json()`).
Viewer memakai klasifikasi ini sebagai filter noise: default hanya `lib`,
karena batas dengan library (DB, HTTP, queue) justru yang paling penting di
sequence diagram.

Untuk inherited method, resolver menyimpan **receiver** (`self.orders.get` →
definisi `BaseRepo.get`, receiver `OrderRepo`). Saat `expand`, kelas dinamis
`self` ikut diwariskan ke body method yang dipanggil, sehingga
`Flask.handle_exception` → `App._find_error_handler` → `self._get_exc_class…`
tetap digambar sebagai `Flask`, bukan `Scaffold`.

### 3. Entry points: aktor "User" mulai dari mana

Prioritas: decorator route (`@router.post("/orders")` → label `POST /orders`),
command CLI (`click`/`typer`), handler (`@task`, `on_*`, `handle_*`), `main`,
lalu fungsi ber-in-degree 0 yang punya call (bukan test, bukan `@property`).

Untuk kode tanpa route/CLI (library, worker, kode non-web) semua flow berupa `root`; calllens
mengurutkannya dengan skor = jumlah fungsi milikmu yang dijangkau, dikurangi untuk fungsi private
(`_x`), nested, dan modul private. `list`, `FLOWS.md` dan sidebar menampilkan 20 teratas (sidebar:
"Show N more"; `list --all` dan pencarian tetap mencakup semuanya).

**Import root otomatis.** Kalau build dijalankan satu folder terlalu tinggi (monorepo, atau
`calllens build .` di atas project), import `app.repos.base` tidak cocok dengan modul
`services.app.repos.base`. calllens mendeteksi prefix seperti itu (>= 3 import cocok ke satu folder)
dan menghapusnya dari nama modul, seolah dijalankan di dalam folder itu (`import root: services/`
tampil di output build). Kalau cocok ke beberapa folder sekaligus atau bentrok dengan modul lain,
calllens tidak menebak dan memberi peringatan untuk build satu project saja.

### 4. Expand: call graph → call tree

DFS terurut dari entry point. Node yang sudah ada di jalur saat ini ditandai
`↺` (rekursi/cycle), node di luar `--depth` ditandai `…`. Tanda `…` itu
sinyal "klik untuk zoom in". Pohon dibangun **lazy** per flow, tidak
disimpan; dengan `max_depth` kompleksitasnya terikat.

### 5. Project: inti dari zoom

Setiap fungsi punya path hierarki `package > module > class > function`.
Zoom level = memilih satu rung sebagai *participant*:

```
participant(f, "class")   = f.module : f.cls          (atau modul kalau fungsi bebas)
participant(f, "module")  = f.module
participant(f, "package") = f.module tanpa segmen terakhir   ("app.services")
```

Lalu pohon diproyeksikan: pesan dipertahankan hanya jika
`participant(caller) != participant(callee)`. Call yang **internal** (dalam
participant yang sama) **diratakan**: anak-anaknya naik menggantikannya dan
*mewarisi konteks control-flow-nya*, sehingga sebuah `for` di dalam method
yang diratakan tetap muncul sebagai `loop` di level modul. Pesan leaf yang
identik dan berurutan di-run-length-collapse (`log.info ×3`).

Hasilnya: **satu pohon, banyak proyeksi**. Level `package` memperlihatkan
`api → services → repos → infra`; level `function` memperlihatkan setiap method.
Zoom *in* ke satu pesan = `expand` ulang dengan root = callee pesan itu
(breadcrumb di viewer adalah tumpukan root ini).

### 6. Render: Mermaid untuk manusia, text untuk AI

Mermaid dipilih karena bisa dirender di browser **dan** dibaca langsung oleh LLM.
Satu flow "POST /orders" di level class ≈ 40 baris ≈ 400 token, versus ribuan
token kalau assistant membaca 8 file sumber. Sibling berurutan dengan tag
konteks sama dibungkus fragment: `if`+`else` → `alt/else`, `if` saja → `opt`,
`loop`, `try`/`except` → `opt try` / `opt on error`.

Logika expand/project/render ada dua: Python (`calllens/flow.py`,
`render/mermaid.py`) untuk CLI, dan port JavaScript di `render/viewer.html`
untuk viewer. Keduanya diuji menghasilkan teks identik (180 kombinasi
parameter pada dua repo), supaya yang dilihat manusia = yang dibaca AI.

---

Fragment membawa **kondisinya**, bukan hanya jenisnya: `alt if not user`,
`loop for item in items`, `opt on error PaymentError`. Extractor menyimpan tag
konteks sebagai `kind:teks` (`if:not user`); dua `if` terpisah yang berurutan
tetap jadi dua fragment.

`show --format text` mencetak flow yang sama sebagai tree berindentasi dengan
`file:line` definisi setiap langkah, tanpa alias participant dan
`activate/deactivate`. Di `POST /orders` (contoh) ukurannya ±2× lebih kecil dari
Mermaid (1.4 KB vs 2.9 KB level class), dan assistant langsung tahu baris mana
yang harus dibuka, tanpa grep:

```
orders.create_order  app/api/orders.py:10
  auth.require_user  app/services/auth.py:16
  try
    OrderService.place  app/services/order_service.py:15
      for item in items
        ProductRepo.get  app/repos/base.py:7
          db.query_one  app/infra/db.py:7
```

`list`, `show`, dan `callers` menjalankan build inkremental dulu (±20 ms hangat),
jadi assistant tidak pernah membaca graph yang basi.

## Viewer live: lihat apa yang diubah AI

Dibuka lewat `calllens serve`, viewer mengikuti kode sendiri: setiap 1,5 detik (selama tab terlihat)
ia meminta server membangun ulang secara inkremental; kalau ada flow yang berubah, diagram
dirender ulang tanpa reload (indikator **Live** berkedip "Updated").

Perubahan diukur terhadap *baseline* = graph saat `serve` dimulai, bukan edit terakhir, karena
agent biasanya mengedit beberapa kali dalam satu giliran:

* langkah baru digambar **hijau**; langkah yang hilang disebut di banner (`+2 steps · −1 removed: …`);
* flow yang berubah diberi titik hijau di sidebar;
* **▶ Play changes** memutar hanya langkah yang berubah; **Mark as seen** menjadikan kondisi
  sekarang baseline baru.

Perbandingan memakai LCS atas urutan panah (urutan ikut dihitung, nomor baris tidak), jadi memindah
kode atau menambah komentar tidak dianggap perubahan flow.

## Memutar flow (Play)

Tombol **Play** di kiri bawah canvas menjalankan flow panah demi panah: panah aktif ditebalkan
dan beranimasi dari pengirim ke penerima, langkah berikutnya diredupkan, participant yang terlibat
disorot (juga di header sticky), dan canvas ikut scroll. Caption menampilkan `A → B: method`,
`file:line` (klik untuk buka di editor) dan fragment tempat langkah itu berada (`in for item in items`).
Kontrol: `Space` play/pause, `←` `→` per langkah, `Esc` reset, kecepatan 0.5× / 1× / 2×.
Ini urutan statis hasil analisis, bukan rekaman eksekusi: kedua cabang `if` tetap diputar.

## Membuka source di editor

Properti **Source** (dan `file:line` di caption Play) membuka file pada baris fungsinya di **editor
default kamu**. Browser tidak bisa tahu editor itu, jadi `calllens build` / `calllens serve` yang
mendeteksinya dari terminal tempat ia dijalankan (`calllens/editor.py`), berurutan:

1. `$CALLLENS_EDITOR` (`vscode|cursor|windsurf|antigravity|zed|pycharm|idea|system`), lalu `$VISUAL` /
   `$EDITOR` bila berisi editor GUI yang dikenal (`code --wait` ya, `vim` dilewati);
2. editor yang terminalnya sedang dipakai (`TERM_PROGRAM`, `ZED_TERM`, JetBrains `TERMINAL_EMULATOR`,
   macOS `__CFBundleIdentifier`): jalankan calllens dari terminal VS Code dan link membuka VS Code;
3. CLI editor pertama yang ada di `PATH`;
4. aplikasi default OS untuk file itu (`open` / `xdg-open`, tanpa nomor baris).

* Dibuka sebagai file (`open calllens-out/index.html`): link memakai URL scheme editor hasil deteksi
  saat build (`vscode://file/…:line`, `zed://file/…:line`, `pycharm://open?file=…&line=…`).
* **Browser bawaan VS Code** (dan browser tertanam lain) memblokir URL scheme seperti itu. Pakai
  `calllens serve` lalu buka `http://127.0.0.1:8765` (VS Code: *Simple Browser: Show*). Klik Source
  meminta server membuka file lewat CLI editor (`code -g file:line`, `zed file:line`, `pycharm --line`).
  CLI itu harus ada di `PATH` (VS Code: *Shell Command: Install 'code' command in PATH*).
  Server hanya listen di 127.0.0.1, menolak request dari origin lain, dan hanya membuka file di dalam project.

## Loop vibe coding: lihat step yang berubah setelah tiap edit

`calllens build . --hook` membangun ulang, membandingkan setiap flow (level
function, tanpa nomor baris) dengan graph sebelumnya, lalu mencetak JSON hook
Claude Code: `systemMessage` untuk **user**, `additionalContext` untuk **Claude**.
Kalau tidak ada flow yang berubah, tidak mencetak apa pun.

`calllens install` memasangnya otomatis di `.claude/settings.local.json` (personal, tidak di-commit,
karena path perintahnya per mesin; aman dijalankan ulang, `--no-hook` untuk melewati). Isinya setara:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [{ "type": "command", "command": "cd \"$CLAUDE_PROJECT_DIR\" && calllens build . --hook" }]
      }
    ]
  }
}
```

Setelah agent mengedit, user langsung melihat misalnya:

```
calllens: flow changed
## POST /orders
⋮
+      if order.total > 1000
+        OrderRepo.mark_failed
       events.publish
```

`calllens diff [flow]` menampilkan hal yang sama kapan saja (perubahan terakhir).
Snippet dari `calllens install` juga meminta assistant menulis sequence diagram
*rencana* di plan sebelum coding, supaya user menyetujui langkah-langkahnya dulu.

## Scaling

Yang sudah ada:

* **Inkremental**: extract per file, cache by content hash. Flask (24 file,
  350 fungsi): 0.07 s dingin, 0.016 s hangat.
* **Lazy**: `graph.json` berukuran O(fungsi + call), bukan O(entry × depth);
  pohon flow dibangun saat diminta, di CLI maupun di browser.
* **Pluggable**: `extractors/base.py` mendefinisikan protocol; `get_extractors()`
  memetakan ekstensi → extractor. Tambah TypeScript/Go = satu kelas berbasis
  tree-sitter yang mengeluarkan `ModuleFacts`, tanpa menyentuh resolver/renderer.
* **Terbatas**: `--depth`, batas 150 pesan per diagram dengan catatan
  "zoom in", filter eksternal.

Langkah berikutnya kalau repo membesar:

1. **tree-sitter** untuk bahasa berikutnya (Go, Java) memakai kontrak `ModuleFacts` yang sama (TS/JS sudah).
2. **Resolusi lebih kuat**: inferensi return type sederhana (`ctx = self.request_context()` →
   `RequestContext`), dependency injection (konstruktor FastAPI/Nest), protokol/interface
   → implementasi (tampilkan sebagai `alt` antar implementasi).
3. **Sharding output**: `graph.json` per package + indeks, viewer memuat shard saat
   flow dibuka; atau MCP server kecil (`calllens serve`) yang mengekspos
   `list_flows`, `show_flow(root, level, depth)` sehingga assistant memanggil
   tool, bukan membaca file.
4. **Diff flow vs git ref**: `calllens diff` sekarang membandingkan dengan build sebelumnya; versi `<git-ref>` —
   review PR dengan melihat perubahan sequence, bukan perubahan baris.
5. **Runtime trace sebagai lapisan opsional**: tangkap trace nyata (OpenTelemetry /
   `sys.setprofile`) untuk menandai jalur yang benar-benar dieksekusi dan
   memperbaiki call yang tak terpecahkan secara statis.
6. **Nama pesan semantik**: untuk level package/module, minta LLM memberi label
   ringkas satu kali per fungsi (di-cache), sehingga `OrderRepo.create` terbaca
   "simpan order + line item".

## Struktur kode

```
calllens/
  model.py              dataclass: CallSite, FunctionDef, ModuleFacts, EntryPoint, Graph
  extractors/base.py    protocol Extractor + registry per ekstensi
  extractors/python_ast.py   extractor Python (ast), urutan evaluasi, konteks, hint tipe
  extractors/typescript_ts.py   extractor TS/JS (tree-sitter, opsional): import tsconfig, route Next.js/Express
  resolver.py           rantai resolusi nama + inheritance + receiver + klasifikasi eksternal
  entrypoints.py        deteksi route / cli / handler / main / root
  builder.py            jalan di repo, cache hash, tulis graph.json
  flow.py               expand (call tree) + project (zoom) + collapse
  render/mermaid.py     Mermaid sequenceDiagram + fragment alt/loop/opt
  render/viewer.html    viewer (port JS dari flow.py + mermaid.py)
  render/html.py        menyuntik graph.json ke viewer
  render/text.py        format text untuk AI: tree + file:line
  serve.py              calllens serve: viewer via http + buka file di editor lewat CLI-nya
  editor.py             deteksi editor default user + cara membukanya (URL scheme / CLI)
  cli.py                build / list / show / callers / diff / serve / install
tests/test_flow.py      smoke test (python tests/test_flow.py)
examples/shop_api/      contoh FastAPI kecil untuk mencoba
examples/next_shop/     contoh Next.js (App Router) + Express, domain yang sama
```
