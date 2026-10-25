"""Token benchmark: the same tasks run by Claude Code with (B) and without (A) vibesequence.

    python bench/bench.py run [--runs 3] [--jobs 3] [--only TASK]   # resumable: finished runs are skipped
    python bench/bench.py report                                     # -> docs/benchmark/{results.csv,README.md}

Each run copies one example project into a fresh temp dir (git repo), sets up the condition
(B = `vibesequence build . && vibesequence install`), runs `claude -p` headless and grades the answer
or the edited code with a fixed check. User-level settings/plugins are excluded (--setting-sources).
"""
import argparse
import csv
import json
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "bench" / "runs"
DOCS = ROOT / "docs" / "benchmark"
WORK = Path(tempfile.gettempdir()) / "vsbench"
MODEL, EFFORT = "claude-sonnet-5-5", "high"
TOOLS = ["Read", "Grep", "Glob", "Edit", "Write", "MultiEdit",
         "Bash(python -m vibesequence:*)", "Bash(vibesequence:*)", "Bash(ls:*)", "Bash(cat:*)", "Bash(grep:*)",
         "Bash(rg:*)", "Bash(find:*)", "Bash(head:*)", "Bash(tail:*)", "Bash(sed -n:*)", "Bash(wc:*)",
         "Bash(git diff:*)", "Bash(git status:*)", "Bash(python -m py_compile:*)"]
ASK = " Answer concisely. Do not modify any files."
EDIT = " Make the minimal change. Do not run the app and do not add tests."


def keys(*patterns):                       # comprehension grader: every key point must be in the answer
    def check(answer, _wd):
        hits = [bool(re.search(p, answer, re.I | re.S)) for p in patterns]
        return all(hits), sum(hits) / len(hits)
    return check


def ordered(path, start, end, *needles):   # edit grader: needles appear in this order inside one function body
    def check(_answer, wd):
        src = (wd / path).read_text()
        if start not in src:
            return False, 0.0
        seg = src[src.index(start):]
        body = seg[:seg.find(end, 1)] if seg.find(end, 1) > 0 else seg
        pos, at = [], 0
        for n in needles:                  # each needle searched after the previous one
            m = re.compile(n).search(body, at)
            pos.append(m.start() if m else -1)
            at = m.end() if m else at
        ok = all(p >= 0 for p in pos)
        if ok and path.endswith(".py"):
            ok = subprocess.run([sys.executable, "-m", "py_compile", str(wd / path)], capture_output=True).returncode == 0
        return ok, sum(p >= 0 for p in pos) / len(pos)
    return check


def both(*checks):
    def check(answer, wd):
        res = [c(answer, wd) for c in checks]
        return all(r[0] for r in res), sum(r[1] for r in res) / len(res)
    return check


TASKS = [
    dict(id="baseline", project="shop_api", kind="baseline", prompt="Reply with just: ok", check=keys(r"\bok\b")),
    dict(id="py-flow", project="shop_api", kind="understand",
         prompt="When a client calls POST /orders, what does the backend do, step by step? List every database write, "
                "every external HTTP call and every event published, in order, and say what happens if the payment "
                "charge fails." + ASK,
         check=keys(r"order[ _]?lines?|line items?", r"pay\.example|requests\.post|charge",
                    r"mark_failed|status\W{0,5}\W?failed|['\"]failed['\"]|marked (as )?failed",
                    r"decrement|reduc\w* (the )?stock", r"order\.placed")),
    dict(id="py-impact", project="shop_api", kind="understand",
         prompt="If I change the behaviour of OrderRepo.update_status, which HTTP routes are affected? "
                "List each route (method + path) and say briefly why." + ASK,
         check=keys(r"/cancel", r"POST\W{1,3}/orders(?![/\w{])|create_order",
                    r"mark_failed|payment (charge )?fail")),
    dict(id="py-edit", project="shop_api", kind="edit",
         prompt='In the POST /orders flow, when the payment charge fails, publish an "order.failed" event with the '
                "order id right after the order is marked failed (before re-raising)." + EDIT,
         check=ordered("app/services/order_service.py", "def place", "\n    def ",
                       r"mark_failed\(", r"events\.publish\(\s*['\"]order\.failed", r"\braise\b")),
    dict(id="ts-cancel", project="next_shop", kind="understand",
         prompt="What happens when a client calls POST /api/orders/[id]/cancel in the Next.js app? List the steps in "
                "order (auth, reads, writes, events) and say whether the payment is refunded." + ASK,
         check=keys(r"requireUser|auth", r"userId|owner|belong", r"updateStatus|status\W{0,20}cancelled",
                    r"order\.cancelled",
                    r"(no|not|never|n't|without|missing|doesn.t)\b[^.\n]{0,80}refund|refund[^.\n]{0,80}\b(not|never|missing|no)\b")),
    dict(id="ts-impact", project="next_shop", kind="understand",
         prompt="If I change OrderRepo.updateStatus, which routes are affected (Next.js and Express)? "
                "List each route (method + path) and say briefly why." + ASK,
         check=keys(r"POST\W{1,3}/api/orders(?![/\w\[])|route\.ts:\s*POST|orders/route", r"/api/orders/\[id\]/cancel",
                    r"(?<!/api)/orders/:id/cancel|express", r"markFailed|payment (charge )?fail")),
    dict(id="ts-edit", project="next_shop", kind="edit",
         prompt="Cancelling an order (POST /api/orders/[id]/cancel) must refund the payment: add a refund method to "
                "PaymentGateway that POSTs to https://pay.example/refund, and call it from OrderService.cancel "
                "before the status is updated." + EDIT,
         check=both(ordered("lib/services/orderService.ts", "async cancel(", "\n  }\n",
                            r"payments\.refund\(", r"updateStatus\("),
                    ordered("lib/payment.ts", "class PaymentGateway", "\n}\n", r"\brefund\s*\(", r"pay\.example/refund"))),
]


def setup(task, cond, run):
    wd = WORK / task["id"] / cond / str(run)
    shutil.rmtree(wd, ignore_errors=True)
    shutil.copytree(ROOT / "examples" / task["project"], wd)
    sh = lambda *c: subprocess.run(c, cwd=wd, check=True, capture_output=True)
    sh("git", "init", "-q"); sh("git", "add", "-A"); sh("git", "commit", "-qm", "init")
    if cond == "B":
        sh("vibesequence", "build", "."); sh("vibesequence", "install")
    return wd


def run_one(task, cond, run):
    out = RUNS / f"{task['id']}.{cond}.{run}.json"
    if out.exists():
        return
    wd = setup(task, cond, run)
    t0 = time.time()
    p = subprocess.run(["claude", "-p", task["prompt"], "--output-format", "stream-json", "--verbose",
                        "--model", MODEL, "--effort", EFFORT, "--setting-sources", "project,local",
                        "--no-session-persistence", "--max-budget-usd", "1.5", "--permission-mode", "acceptEdits",
                        "--allowedTools", *TOOLS],
                       cwd=wd, capture_output=True, text=True, timeout=900)
    (RUNS / f"{task['id']}.{cond}.{run}.jsonl").write_text(p.stdout)
    events = [json.loads(l) for l in p.stdout.splitlines() if l.startswith("{")]
    result = next((e for e in events if e.get("type") == "result"), {})
    tools = [c for e in events if e.get("type") == "assistant"
             for c in e["message"].get("content", []) if c.get("type") == "tool_use"]
    answer = result.get("result") or ""
    passed, score = task["check"](answer, wd) if result else (False, 0.0)
    mu = result.get("modelUsage", {})
    tot = lambda k: sum(m.get(k, 0) for m in mu.values())
    out.write_text(json.dumps(dict(
        task=task["id"], project=task["project"], kind=task["kind"], cond=cond, run=run,
        passed=passed, score=round(score, 2),
        input_total=tot("inputTokens") + tot("cacheReadInputTokens") + tot("cacheCreationInputTokens"),
        input_uncached=tot("inputTokens"), cache_read=tot("cacheReadInputTokens"),
        cache_write=tot("cacheCreationInputTokens"), output=tot("outputTokens"),
        cost_usd=round(result.get("total_cost_usd", 0), 4), turns=result.get("num_turns", 0),
        tool_calls=len(tools), reads=sum(t["name"] == "Read" for t in tools),
        searches=sum(t["name"] in ("Grep", "Glob") for t in tools),
        vibesequence_calls=sum("vibesequence" in json.dumps(t.get("input", {})) for t in tools),
        seconds=round(time.time() - t0), error="" if result and not result.get("is_error") else
        (result.get("subtype") or p.stderr[-300:] or "no result"),
        answer=answer[:2000]), indent=1))
    print(f"{task['id']:10} {cond} #{run}  pass={passed}  in={tot('inputTokens') + tot('cacheReadInputTokens') + tot('cacheCreationInputTokens')}"
          f"  out={tot('outputTokens')}  ${result.get('total_cost_usd', 0):.3f}", flush=True)


def cmd_run(a):
    RUNS.mkdir(parents=True, exist_ok=True)
    jobs = [(t, c, r) for r in range(1, a.runs + 1) for t in TASKS if not a.only or t["id"] == a.only for c in "AB"]
    with ThreadPoolExecutor(a.jobs) as ex:     # ponytail: plain thread pool; subscription rate limits cap useful --jobs
        for f in [ex.submit(run_one, *j) for j in jobs]:
            try:
                f.result()
            except Exception as e:
                print("run failed:", e, flush=True)


def cmd_regrade(_a):                       # re-apply the (fixed) graders to saved answers / edited work dirs
    tasks = {t["id"]: t for t in TASKS}
    for p in sorted(RUNS.glob("*.json")):
        r = json.loads(p.read_text())
        events = [json.loads(l) for l in p.with_suffix(".jsonl").read_text().splitlines() if l.startswith("{")]
        answer = next((e.get("result") or "" for e in events if e.get("type") == "result"), "")
        passed, score = tasks[r["task"]]["check"](answer, WORK / r["task"] / r["cond"] / str(r["run"]))
        if (passed, round(score, 2)) != (r["passed"], r["score"]):
            print(f"{p.stem}: pass {r['passed']} -> {passed}")
        r.update(passed=passed, score=round(score, 2))
        p.write_text(json.dumps(r, indent=1))


def cmd_report(_a):
    rows = [json.loads(p.read_text()) for p in sorted(RUNS.glob("*.json"))]
    DOCS.mkdir(parents=True, exist_ok=True)
    cols = [k for k in rows[0] if k != "answer"]
    with open(DOCS / "results.csv", "w", newline="") as f:
        w = csv.DictWriter(f, cols, extrasaction="ignore"); w.writeheader(); w.writerows(rows)
    med = lambda rs, k: statistics.median(r[k] for r in rs) if rs else 0
    order = [t["id"] for t in TASKS]
    lines = ["| task | kind | pass A | pass B | input tokens A | input tokens B | B/A | output A | output B "
             "| cost A | cost B | tool calls A | tool calls B |", "|" + "---|" * 13]
    for tid in order:
        A = [r for r in rows if r["task"] == tid and r["cond"] == "A"]
        B = [r for r in rows if r["task"] == tid and r["cond"] == "B"]
        if not A or not B:
            continue
        ia, ib = med(A, "input_total"), med(B, "input_total")
        lines.append(f"| `{tid}` | {A[0]['kind']} | {sum(r['passed'] for r in A)}/{len(A)} | {sum(r['passed'] for r in B)}/{len(B)} "
                     f"| {ia:,.0f} | {ib:,.0f} | {ib / ia:.2f} | {med(A, 'output'):,.0f} | {med(B, 'output'):,.0f} "
                     f"| ${med(A, 'cost_usd'):.3f} | ${med(B, 'cost_usd'):.3f} | {med(A, 'tool_calls'):.0f} | {med(B, 'tool_calls'):.0f} |")
    print("\n".join(lines))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("--runs", type=int, default=3); r.add_argument("--jobs", type=int, default=3)
    r.add_argument("--only")
    sub.add_parser("report"); sub.add_parser("regrade")
    a = ap.parse_args()
    {"run": cmd_run, "report": cmd_report, "regrade": cmd_regrade}[a.cmd](a)
