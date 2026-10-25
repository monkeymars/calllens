# Token benchmark: Claude Code with and without vibesequence

**Question:** does an AI agent spend fewer tokens on the same task when vibesequence is installed, without
getting worse answers?

**Short answer (on the bundled examples):** yes for questions about *how code flows* (−23% input tokens,
−18% cost, half the turns). No for small edit tasks (+19% input tokens). All 42 runs passed in both conditions.

## Results

Median of 3 runs per task and condition. **A** = plain project, **B** = after `vibesequence build . && vibesequence install`.
Input tokens = everything the model read across all turns (uncached + cache write + cache read), all models.

| task | kind | pass A | pass B | input tokens A | input tokens B | B/A | output A | output B | cost A | cost B | tool calls A | tool calls B |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `baseline` | baseline | 3/3 | 3/3 | 19,081 | 19,801 | 1.04 | 12 | 12 | $0.025 | $0.028 | 0 | 0 |
| `py-flow` | understand | 3/3 | 3/3 | 85,490 | 108,863 | 1.27 | 2,315 | 1,815 | $0.085 | $0.083 | 13 | 8 |
| `py-impact` | understand | 3/3 | 3/3 | 82,343 | 61,490 | 0.75 | 915 | 524 | $0.061 | $0.051 | 6 | 2 |
| `py-edit` | edit | 3/3 | 3/3 | 80,690 | 85,024 | 1.05 | 674 | 656 | $0.055 | $0.061 | 4 | 4 |
| `ts-cancel` | understand | 3/3 | 3/3 | 101,191 | 82,838 | 0.82 | 1,212 | 1,194 | $0.066 | $0.063 | 5 | 4 |
| `ts-impact` | understand | 3/3 | 3/3 | 85,213 | 40,204 | 0.47 | 1,331 | 502 | $0.071 | $0.042 | 7 | 1 |
| `ts-edit` | edit | 3/3 | 3/3 | 81,368 | 108,043 | 1.33 | 1,197 | 1,266 | $0.063 | $0.075 | 5 | 6 |

Totals by kind (all runs):

| kind | input tokens A → B | cost A → B | median turns A → B | file reads A → B |
|---|---|---|---|---|
| understand (12 runs each) | 1,090,029 → 836,911 (**−23%**) | $0.86 → $0.71 (**−18%**) | 8 → 4 | 56 → 23 |
| edit (6 runs each) | 485,908 → 578,769 (+19%) | $0.35 → $0.41 (+15%) | 5.5 → 6 | 11 → 12 |

Raw per-run data: [results.csv](results.csv).

## What we learned

- **Impact questions win big.** "Which routes break if I change `OrderRepo.updateStatus`?" took one
  `vibesequence callers` call in B instead of 6–7 greps and reads (`ts-impact`: −53% input, −41% cost).
- **Turns drive token use, not file size.** Each turn re-reads Claude Code's ~19K-token overhead plus the
  conversation so far, so saving a few turns matters more than saving a few file reads.
- **Edits don't benefit on tiny projects.** The agent still has to read the files it edits, and on these
  examples one grep already finds them. Running `show` first is an extra turn.
- **Exhaustive questions still need the source.** For `py-flow` ("list every DB write, HTTP call and event")
  the agent used `show` and then read the files anyway to confirm details, which cost more.
- **Fixed cost of installing:** the `CLAUDE.md` section adds ~720 tokens to every turn (`baseline`: +4%).

## Method

- **Projects:** `examples/shop_api` (FastAPI) and `examples/next_shop` (Next.js + Express). Each run copies
  the project into a fresh temp git repo.
- **Agent:** `claude -p` (Claude Code 2.1.286), `claude-sonnet-5-5`, effort `high`, `--setting-sources project,local`
  (no user-level plugins/hooks), the same tool allowlist in both conditions, `--no-session-persistence`.
- **Tasks:** 4 understanding questions, 2 edits, 1 baseline ("Reply with just: ok"). Defined in
  [`bench/bench.py`](../../bench/bench.py).
- **Grading:** answers must contain fixed key points (regex). Edits are checked in the edited source:
  the right calls in the right order, and Python edits must compile.
- **Cost:** `total_cost_usd` from Claude Code at API list prices. Runs used a Claude subscription, so this is
  an estimate, not a bill. The whole benchmark was ≈ $2.50 at list prices.

## Caveats

- **The examples are tiny** (~4K tokens of source in total), so reading everything is cheap. On real
  repos, where finding the right files takes many greps and reads, the savings should be larger. That is
  not measured yet.
- **n = 3 per cell.** One extra turn moves a cell by ~20K input tokens, so treat single-task ratios as rough.
- Results hold only for this model, effort and Claude Code version.

## Reproduce

```bash
python bench/bench.py run --runs 3 --jobs 3   # resumable; raw transcripts in bench/runs/ (gitignored)
python bench/bench.py regrade                 # re-apply graders after changing them
python bench/bench.py report                  # writes docs/benchmark/results.csv, prints the table
```
