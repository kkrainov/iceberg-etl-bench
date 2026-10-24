# Running plans (Phase L)

## Before a measurement run

1. `m0 doctor` (no FAIL), and ask the user about machine hygiene (power plan,
   background apps) if the numbers will be used.
2. `m0 env up --engine trino`, and `m0 data status --sf 1` (or the plan's SF).
3. Nothing else heavy on the machine: the engine gets 8 GiB of ~11.7 GiB.

## Commands

| Command | Use |
|---|---|
| `m0 run plans/phase-l-smoke.yaml` | Run every cell of a plan into `results/<plan>-<timestamp>/` |
| `m0 run plans/phase-l-smoke.yaml --run-id smoke-1` | Name the run |
| `m0 run plans/phase-l-smoke.yaml --query tpch/q03 --engine trino` | Only matching cells (`--query` takes `suite/id` or a bare id; both flags repeat) |
| `m0 run plans/phase-l-smoke.yaml --resume` | Continue the latest run of this plan; recorded cells are skipped |
| `m0 run plans/phase-l-smoke.yaml --resume --run-id smoke-1` | Continue a named run |
| `m0 status` | Progress and last failure of the latest run (`m0 status smoke-1` for another) |

Exit codes: exit code 0 all cells ok; exit code 3 some cells not ok (data, not an error; the smoke plan
always gives 3 because of its deliberate `err_mem` cell); exit code 2 stack, engine
adapter, data or a query file missing; exit code 4 the run ID exists without `--resume`, the plan
file changed since the run started, or the run is in progress; exit code 1 harness error or
Ctrl-C (resume it).

Output: one line per non-`ok` cell as it happens (`(expected)` when the plan's
`expect` matches), `INFO` lines for automatic restarts, then outcome counts per engine
and the `runs.parquet` path.

## What a run does

- Cells are expanded from the plan (`docs/framework-design.md`, "Run plans"):
  shuffled query order per round, iteration 0 then the hot iterations of each query,
  ABBA engine blocks.
- **Cold:** before a cold cell the engine container is recreated and the page cache
  dropped by a privileged `busybox` helper (pinned in `versions.lock.yaml`), about
  15 s. `per_query` does this before every query, `per_block` once per block.
  Each restart and cache drop is a line in `run.log`.
- **Per cell:** wall time = execute + fetch all rows (harness clock); cgroup CPU and
  memory for the engine and `objstore`; Trino's final query info from
  `/v1/query/{id}`; a health check (`/v1/info` + `SELECT 1`); the outcome from
  `m0/classify_rules.yaml`.
- **Timeout** (`timeout_s`, 600 s by default): the query is cancelled
  (`DELETE /v1/query/{id}`) and the cell is `timeout`.
- **After `killed` or `unresponsive`** the engine is restarted before the next cell
  (logged, `INFO restarted …` on stdout).
- Results are appended per cell, so Ctrl-C or a crash loses at most the running cell.

## Where a cell's data is

`results/<run-id>/`:

| File | Content |
|---|---|
| `runs.parquet` | One row per cell (schema: `docs/framework-design.md`, "Results schema") |
| `cells.jsonl` | The same rows, appended as cells finish |
| `run.log` | Start, restarts (with reason and seconds), cache drops, non-`ok` cells |
| `env.json` | git SHA, image digests, host, WSL config, config hashes, plan, times |
| `cells/<cell_id>/query.sql` | The exact SQL that ran |
| `cells/<cell_id>/engine_stats.json` | Trino query info, trimmed: state, errorCode, failureInfo (no stacks), queryStats |
| `cells/<cell_id>/memory.csv` | `t_ms,engine_bytes,objstore_bytes` at 20 Hz |
| `rows/<cell_id>.parquet` | Result rows of iteration-0 cells (for the step 4 differ) |

Read `runs.parquet` with a query, not by printing it:

```bash
uv run python -c "import pyarrow.parquet as pq; t = pq.read_table('results/smoke-1/runs.parquet', columns=['cell_id','outcome','wall_ms','peak_mem_bytes']); print(t.slice(0, 10))"
```

## Reading the numbers

- Iteration 0 is the first run of a query in its block. It's `cold = true` only if a
  restart and cache drop came directly before it (always with `per_query`).
- `peak_mem_bytes` is the container's peak: exact when `peak_mem_source` is
  `memory.peak`, a 20 Hz lower bound when `sampled`. For Trino it mostly shows JVM
  heap growth (it climbs towards ~7.5 GiB over a block); `engine_peak_mem_bytes` is
  the query's own reservation.
- `cpu_s` (cgroup) includes JIT, GC and background threads, so it's above
  `engine_cpu_s`, most of all on cold runs.
- `ok` currently means "completed"; result comparison arrives with step 4
  (`diff_status = pending`).

## Reference timings (2026-10-24, SF1, Trino 8 GiB)

- `phase-l-smoke.yaml`: 33 cells in under a minute.
- `phase-l-cold-check.yaml` (`per_query`, 2 queries): ~40 s; cold 2.0–2.5 s vs hot
  0.3–0.4 s.
