---
name: m0-bench
description: Drive the iceberg-etl-bench harness (`m0` CLI) that compares Trino (retry-policy=NONE) with DataFusion + datafusion-distributed on Iceberg. Use it to check prerequisites, start or stop the local stack, run benchmark suites, analyse results and investigate failures in this repository.
---

# m0-bench

This repo measures whether DataFusion + datafusion-distributed has enough headroom
over Trino's in-memory execution to justify Rino, a Rust drop-in replacement for
Trino. It also measures where the foundation breaks under memory pressure.

- **Phase L** (now): one workstation, WSL2 + Docker. Exploratory, $0.
- **Phase C** (later): AWS `eu-north-1` Spot. Pre-registered and published.

Method: `docs/methodology.md`. Harness design: `docs/framework-design.md`.

## Hard rules

1. **Results come from code.** Never hand-edit results, recorded outcomes or baseline
   configs. Experiments are named variants with their own runs.
2. **Billable actions** (Phase C): never pass `--yes` to a billable command without
   asking the user first.
3. **Destructive local actions:** `m0 env down --purge --yes` deletes generated data.
   Ask the user before purging.
4. **One engine at a time.** `m0 env up --engine <e>` stops the other engines. Don't
   start engines with plain `docker compose`.
5. **Upstream and public actions:** draft issues and posts only, never file or post them.

## Commands available now

Run them with `uv run m0 …` from the repo root. Each prints at most a short summary;
add `--json` for machine-readable output.

| Command | Use |
|---|---|
| `m0 doctor` | Prerequisites and machine settings. Run it first in every session. |
| `m0 env up` | Start infra (object store, Iceberg REST catalog). Idempotent. |
| `m0 env up --engine trino` | Also start Trino (recreated, so it's cold) and run a write/read smoke test |
| `m0 env up --engine df` | Stop Trino, build the `dfrunner` image (incremental; first build ~10 min), start plain DataFusion cold and run a read smoke test |
| `m0 env up --engine df-jo` | The same for a step 4c variant (`df-cs`, `df-jo`, `df-jo-cs`, `df-sail`, `df-sail-cs`): builds the variant's image (`df-jo` and `df-sail` are separate builds, ~5 min each) and starts it on port 9000 |
| `m0 env status` | Health of every service, plus whether the warehouse exists |
| `m0 env down` | Stop the stack. Data is kept. |
| `m0 env down --purge --yes` | Stop the stack and delete all data (ask first) |
| `m0 data gen --suite tpch --sf 10` | Generate TPC-H/TPC-DS/`etl` tables into Iceberg through Trino (`--suite tpch\|tpcds\|etl`; `etl` needs `tpcds` at the same SF). Skips tables that validate; `--force` rebuilds. |
| `m0 data status` | Validate every generated table against `datagen/expected.yaml`; `--suite`/`--sf` narrow it and show per-table rows, size, files, snapshot |
| `m0 data verify --engine df` | Fingerprint every table through an engine and compare with Trino's (`m0 data verify --engine trino` first, with Trino up). Lists every table or column that differs or can't be read. Exit code 3 = differences (findings, not errors). |
| `m0 run plans/phase-l-smoke.yaml` | Execute a plan (the smoke plan runs `trino` and `df` in ABBA order and switches engines itself); prints non-ok cells and restarts as they happen, then outcome counts. `--query`, `--engine` filter; `--run-id` names the run; `--resume` continues it. Exit code 3 = some cells not ok (expected for the smoke plan). |
| `m0 status` | Progress and last failure of the latest run (or `m0 status <run-id>`) |
| `m0 summarize` | A finished run in at most 40 lines: outcomes, answer diffs against Trino, suite totals, ratios with 95% CIs, slowest queries, every failure group, every mismatch. `m0 summarize <run-id> --json` for everything. `m0 summarize <run-id> --with <other-run>` adds the other run's engines (e.g. the `trino`/`df` baseline for an A6 run). Exit code 3 = some cells not ok. |
| `m0 compare <run-a> <run-b>` | Run B against run A per engine (B/A times, moved queries, outcome changes, changed images and configs). `m0 compare <run-a> <run-b> --engines df:df-jo` pairs engine `df` of A with `df-jo` of B. |
| `m0 explain <run-id> <engine> <query>` | Re-run one query on the live engine; saves the plan with metrics under the run's `diag/`, prints the top operators. Needs the engine up. |
| `m0 logs <run-id> <engine> <query>` | Engine log lines during that query's cells (`--grep`, `--tail`); says when the container was recreated and the lines are gone |
| `m0 results list` | Every run under `results/` with state, cells, size, and whether `results/keep.txt` keeps it |
| `m0 results prune` | Dry run: which runs not kept would be deleted. `--yes` deletes them, `--keep <id>` spares one more. Ask the user before `--yes` unless they asked for the cleanup. |

Engines now: `trino` (Trino 484) and `df` (DataFusion 55 in `dfrunner single`), plus the
step 4c variants of `df` for suite A6: `df-cs` (Iceberg column statistics on), `df-jo`
and `df-jo-cs` (apache/datafusion#24456 join-order enumeration backported to 55.0.0),
`df-sail` and `df-sail-cs` (Sail's `JoinReorder` rule). How they are built:
`docs/framework-design.md` ("Engine variants"). `dfd` arrives with step 5.

Standard loop: `doctor → env up --engine trino → data status → run → status →
summarize → (explain / logs / compare) → results prune`. Analysis: runbook
[analyse](runbooks/analyse.md). More commands (`triage`, `repro`, `diff`, …) arrive
with later build steps. If `m0 --help` doesn't list a command, it doesn't exist yet.

Plans: `plans/phase-l-smoke.yaml` (minutes, after every harness change),
`plans/phase-l-a1a2-sf1.yaml` (all 22 TPC-H + 99 TPC-DS queries, ~25 min),
`plans/phase-l-a1a2-sf10.yaml` (the Phase L main run, hours).

**Exit codes:**
- exit code 0: ok
- exit code 1: harness error, a bug or an unexpected failure; investigate
- exit code 2: prerequisite missing or stack not running
- exit code 3: run finished, but some queries failed (that's data, not an error)
- exit code 4: guard refused (budget, TTL, or a destructive action without `--yes`)

## Where things are

| What | Path |
|---|---|
| Pinned versions (images, engines, tools) | `versions.lock.yaml` |
| Local stack definition | `infra/local/compose.yaml` |
| Trino config (local) | `configs/trino/local/` |
| DataFusion config (local): pool, spilling, partitions, catalog | `configs/datafusion/local/dfrunner.properties` |
| `dfrunner` (Rust): source, lockfile, Dockerfile | `dfrunner/` (`src/main.rs` HTTP API, `catalog.rs`, `pool.rs`, `query.rs`) |
| DataFusion SQL overrides (only with a reason in the manifest) | `queries/overrides/datafusion/<suite>/<id>.sql` |
| Table fingerprints per engine; df-vs-trino report | `results/verify/<engine>/<namespace>.json`; `results/verify/df/vs-trino.json` |
| Data generation SQL, expected row counts, etl parameters | `datagen/` (`expected.yaml`, `etl/*.sql`, `etl/params.yaml`) |
| What `data gen` built (snapshot IDs, timings) | `results/datagen/<suite>_sf<N>.json` |
| Run plans; queries (manifest + Trino SQL) | `plans/*.yaml`; `queries/manifest.yaml`, `queries/<suite>/<id>.sql` |
| Outcome rules (engine error → class) | `m0/classify_rules.yaml`, tested with real errors in `tests/fixtures/{trino,df}_errors.yaml` |
| One line per cell: outcome, error line, wall, CPU, memory, time breakdown, restart and cache-drop `events` | `results/<run-id>/cells.jsonl` (fields: `docs/framework-design.md`, "Results") |
| What and how a run ran: plan (resolved), git, versions, image digests, engine settings and config hashes, host, start/end | `results/<run-id>/run.json` |
| Result rows of the first completed run per engine × query (capped, canonical JSON) | `results/<run-id>/answers.jsonl` |
| One cell's SQL, engine stats (Trino query info / dfrunner stats with plan + metrics), memory timeline; only when the plan sets `diag` | `results/<run-id>/diag/<cell_id>/{query.sql, stats.json, memory.csv}` |
| `m0 explain` output: SQL, plan with metrics, operators, engine stats | `results/<run-id>/diag/explain.<engine>.<suite>.<id>/{query.sql, plan.txt, operators.json, stats.json}` |
| Answer comparison rules (differ), statistics | `m0/differ.py`, `m0/stats.py` (methodology sections 8 and 9) |
| Query order keys and tie flags used by the differ | `queries/manifest.yaml` (`ordered`, `sort_keys`, `tie_ambiguous`) |
| Runs that `m0 results prune` keeps (run IDs or globs) | `results/keep.txt` |
| Container logs | `docker compose -p m0 logs <service> --tail 200` (services: `objstore`, `catalog`, `catalog-db`, `catalog-migrate`, `trino`, `df`, and the variants `df-cs`, `df-jo`, `df-jo-cs`, `df-sail`, `df-sail-cs`) |
| dfrunner live state (versions, pool, running queries) | `curl -s 127.0.0.1:9000/status`; one query: `curl -s 127.0.0.1:9000/queries/<engine_query_id>` |

## When something goes wrong

Investigate freely with read-only means: logs, `docker compose -p m0 ps`,
`docker stats`, cgroup files, `dmesg`, engine HTTP endpoints, and reading code. Rule
out the environment before suspecting an engine. Grep logs; don't read them whole.

- Runbooks: [preflight](runbooks/preflight.md), [environment](runbooks/environment.md), [data](runbooks/data.md), [run](runbooks/run.md), [analyse](runbooks/analyse.md), [troubleshooting](runbooks/troubleshooting.md)
- `cell_id` is `<engine>.m<mem>.r<round>.<suite>.<query>.i<iteration>`; iteration 0 is
  the first (cold) run. Query `cells.jsonl` with a short Python one-liner (runbook
  [run](runbooks/run.md)); don't print it whole.
- When you understand a new failure pattern, encode it: add a `doctor` check or a
  runbook entry, with a test where possible.
