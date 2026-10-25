# Running plans (Phase L)

## Before a measurement run

1. `m0 doctor` (no FAIL), and ask the user about machine hygiene (power plan,
   background apps) if the numbers will be used.
2. `m0 env up --engine trino`, and `m0 data status --sf 1` (or the plan's SF). For a
   plan with `df`, build it once with `m0 env up --engine df` (the runner starts and
   stops engines itself, but doesn't build the image).
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
| `m0 results list` | All runs: state, cells recorded/planned, size, `KEEP` if `results/keep.txt` names it |
| `m0 results prune` | Show which runs not kept would be deleted; `m0 results prune --yes` deletes them |

Exit codes: exit code 0 all cells ok; exit code 3 some cells not ok (data, not an error; the smoke plan
always gives 3 because of its deliberate `err_mem` cell); exit code 2 stack, engine
adapter, data or a query file missing; exit code 4 the run ID exists without `--resume`, the plan
changed since the run started (its resolved settings, compared by `plan_hash` in
`run.json`; comments don't count), or the run is in progress; exit code 1 harness error or
Ctrl-C (resume it).

Output: one line per non-`ok` cell as it happens (`(expected)` when the plan's
`expect` matches), `INFO` lines for automatic restarts, then outcome counts per engine
and the run directory.

## What a run does

- Cells are expanded from the plan (`docs/framework-design.md`, "Run plans"):
  shuffled query order per round, iteration 0 then the hot iterations of each query,
  ABBA engine blocks.
- **Cold:** before a cold cell the engine container is recreated and the page cache
  dropped by a privileged `busybox` helper (pinned in `versions.lock.yaml`), about
  15 s. `per_query` does this before every query, `per_block` once per block.
  Each restart and cache drop is in the next cell's `events` in `cells.jsonl`.
- **Per cell:** wall time = execute + fetch all rows (harness clock); cgroup CPU and
  memory for the engine and `objstore`; final engine stats (Trino `/v1/query/{id}`,
  dfrunner `/queries/{id}`); a health check (`/v1/info` or `/health`, plus `SELECT 1`);
  the outcome from `m0/classify_rules.yaml`.
- **SQL:** canonical Trino text from `queries/<suite>/<id>.sql`; `df` uses
  `queries/overrides/datafusion/<suite>/<id>.sql` where the manifest gives a
  `datafusion_override` reason. With `diag` in the plan, `diag/<cell_id>/query.sql` is
  what actually ran.
- **Engines switch per block:** the runner stops the other engine and recreates the
  block's engine (`df` or `trino`), so only one engine ever runs.
- **Timeout** (`timeout_s`, 600 s by default; a plan entry can set its own
  `timeout_s`): the query is cancelled (Trino `DELETE /v1/query/{id}`, dfrunner
  `DELETE /queries/{id}`) and the cell is `timeout`. If dfrunner is still busy after
  the cancel (pool memory still reserved), the engine is restarted before the next cell
  (`INFO restarted df after timeout (engine still busy after cancel)`).
- **After `killed` or `unresponsive`** the engine is restarted before the next cell
  (`INFO restarted …` on stdout; a `restart` event with reason `after killed` on the
  next cell).
- Results are appended per cell, so Ctrl-C or a crash loses at most the running cell.

## Where a cell's data is

`results/<run-id>/` (fields and encodings: `docs/framework-design.md`, "Results"):

| File | Content |
|---|---|
| `run.json` | Run ID, plan file, `plan_hash` and the resolved plan, git SHA and dirty flag, `versions.lock.yaml` and image digests, per engine its version, key settings and config-file hashes, container limits, host, WSL config, start/end, one entry per `--resume` |
| `cells.jsonl` | One line per finished cell: identity, `cold`, `events` (restart with reason and seconds, cache drop), wall/CPU/memory, `planning_ms`, `scan_ms`/`exchange_ms`/`compute_ms`, outcome, `error_line`, `engine_query_id`, `rows`, `answer` |
| `answers.jsonl` | Result rows (canonical JSON, capped) of the first completed run per engine × query, for the step 4 differ |
| `diag/<cell_id>/` | Only for cells the plan's `diag` selects (`diag: true`, or a list like `[tpch/q01]`): `query.sql`; `stats.json` (Trino: trimmed query info with `operatorTime` and `queryStats`; df: dfrunner stats with `pool`, `error`, `metrics.operators` and `metrics.plan`); `memory.csv` (`t_ms,engine_bytes,objstore_bytes` at 20 Hz) |

Planned cells aren't stored; they come from the plan in `run.json`. Without `diag`,
engine stats of a recent query are still available live:
`curl -s 127.0.0.1:9000/queries/<engine_query_id>` (df, last 512 queries) or
`curl -s -H 'X-Trino-User: m0' 127.0.0.1:8080/v1/query/<engine_query_id>` (Trino, while
it hasn't restarted).

Read `cells.jsonl` with a query, not by printing it:

```bash
uv run python -c "import json; [print(r['cell_id'], r['outcome'], r['wall_ms'], r['peak_mem_bytes']) for r in map(json.loads, open('results/smoke-1/cells.jsonl')) if r['iteration'] == 0]"
```

**Retention:** scratch runs pile up. Add the run IDs (or globs) worth keeping to
`results/keep.txt`, then `m0 results prune` (dry run) and `m0 results prune --yes`.
`results/datagen/` and `results/verify/` are never touched.

## Reading the numbers

- Iteration 0 is the first run of a query in its block. It's `cold = true` only if a
  restart and cache drop came directly before it (always with `per_query`).
- `peak_mem_bytes` is the container's peak: exact when `peak_mem_source` is
  `memory.peak`, a 20 Hz lower bound when `sampled`. For Trino it mostly shows JVM
  heap growth (it climbs towards ~7.5 GiB over a block); `engine_peak_mem_bytes` is
  the query's own reservation.
- `cpu_s` (cgroup) includes JIT, GC and background threads, so it's above
  `engine_cpu_s`, most of all on cold runs.
- **df:** `engine_peak_mem_bytes` is the DataFusion pool high-water mark (reset per
  query); `peak_mem_bytes` (container) minus it is memory the pool doesn't track.
  `engine_cpu_s` is dfrunner's process CPU (getrusage) during the query. dfrunner has no
  JVM, so its container memory falls back after each query, unlike Trino's.
- `ok` currently means "completed"; result comparison arrives with step 4.
- `scan_ms`, `exchange_ms`, `compute_ms` are operator time summed over threads, not
  wall time. df `scan_ms` is null: the Iceberg scan has no timer.

## Reference timings (2026-10-24, SF1, Trino 8 GiB)

- `phase-l-smoke.yaml`: 33 cells in under a minute.
- `phase-l-cold-check.yaml` (`per_query`, 2 queries): ~40 s; cold 2.0–2.5 s vs hot
  0.3–0.4 s.
