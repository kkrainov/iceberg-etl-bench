# Implementation plan

How the harness gets built, step by step. Each step ships code, tests and the matching
skill sections together (see [framework-design.md](framework-design.md#claude-code-skill)).
A step is done only when its acceptance criteria pass on the reference machine and the
work is committed.

Sizes: **S** = one session, **M** = 2–3 sessions, **L** = 4+ sessions.

## How each step is executed

1. **Brief.** The lead agent writes a self-contained brief for the step: the goal,
   scope, acceptance criteria and risks from this plan, the relevant docs, the
   repository conventions, and decisions already made.
2. **Implement.** A fresh sub-agent implements the step from the brief. It doesn't
   commit or push. If it hits something that needs the user (installs, `sudo`, a
   fairness question), it stops and reports instead of working around it.
3. **Verify.** The lead agent checks the result independently:
   - reviews the diff;
   - re-runs the acceptance criteria and tests itself;
   - checks the docs and skill against the code.

   It fixes small issues directly and sends larger ones back to a sub-agent.
4. **Update this plan.** The lead agent marks the step done, records the acceptance
   evidence and any deviations or new findings, and adjusts later steps.
5. **Commit and push**, plan update included.

| Step | Goal | Size | Status |
|---|---|---|---|
| 1 | Skeleton, `doctor`, local stack | S | ✅ done |
| 2 | Data generation | M | ✅ done |
| 3a | Measurement core with Trino | M | ✅ done |
| 3b | `dfrunner` single mode (`df`) | L | ✅ done |
| 3c | Simplify result storage (JSON only, fewer artifacts) | S | next |
| 4 | Analysis: diff, stats, summaries, full query catalogue | M | |
| 4b | Investigation helpers and deliberate failures | S | |
| 5 | Environment interface, distributed `dfd`, memory levels, concurrency, B1 queries | L | |
| 6 | Report | M | |
| L | Phase L execution and Gate L | M | |
| 7 | Phase C (AWS) | L | after Gate L |

---

## Step 1: skeleton, `doctor`, local stack ✅

**Delivered:**
- `m0 doctor` and `m0 env up/down/status`.
- Local stack: SeaweedFS, Lakekeeper with Postgres, Trino 484.
- `versions.lock.yaml`.
- `SKILL.md` core plus the `preflight` and `environment` runbooks.
- Tests, including the skill-drift test.

**Acceptance (passed 2026-10-24):**
- `doctor` reports 0 failures on the reference machine.
- `env up --engine trino` passes the write/read smoke test through the catalog and S3.
- State survives `env down`; `--purge` without `--yes` exits with code 4.
- 11 tests pass.

## Step 2: data generation ✅

**Delivered:**
- **Commands:**
  - `m0 data gen --suite tpch|tpcds|etl --sf N [--force]`, which is idempotent and
    resumes after interruption;
  - `m0 data status`, which validates using Iceberg metadata only: row counts, no
    delete files, format v2, Parquet, zstd.
- **Generator inputs:**
  - `datagen/expected.yaml`: row counts from the specs, cross-checked against the
    connectors;
  - `datagen/etl/*.sql` and `params.yaml`: `store_sales_skewed` and `store_sales_dups`;
  - a per-namespace manifest in `results/datagen/`.
- **Skill and tests:** `runbooks/data.md`; 15 new tests (26 in total).

**Acceptance (verified independently by the lead, 2026-10-24):**
- All six namespaces (tpch, tpcds, etl × SF1, SF10) are present. `m0 data status`
  exits 0.
- Re-running `m0 data gen` builds 0 tables.
- **Spot checks against the specs:**
  - TPC-H SF10: `lineitem` 59,986,052 rows, `orders` 15,000,000.
  - TPC-DS: `store_sales` SF1 2,880,404 rows, `customer` SF10 500,000.
  - Money columns are `decimal(12,2)`.
  - Tables are zstd, format v2.
- **ETL tables:**
  - The top skewed key holds 13.96% of rows at SF10.
  - `store_sales_dups` has a unique (ticket, item, version) key over 28,799,585 rows.
  - Regenerating with `--force` gives identical rows (same checksum).
- **Time:** all of SF10 generated in **329 s**, against the 60-minute budget.

| Suite | SF1 | SF10 |
|---|---|---|
| tpch | 12 s, 209 MiB | 50 s, 2.1 GiB |
| tpcds | 46 s, 258 MiB | 169 s, 2.7 GiB |
| etl | 10 s, 210 MiB | ~100 s, 2.0 GiB |

**Findings** (recorded in methodology section 3):
- **CHAR columns:** TPC-DS `CHAR(n)` becomes Iceberg `string` with the padding
  dropped, so both engines read unpadded strings.
- **Spec-shaped TPC-H:** the `tpch` connector is configured for standard column names
  and DECIMAL money columns (its defaults are unprefixed names and DOUBLE).
- **Row-count quirk:** Trino's `tpcds` connector makes 75 `reason` rows at every SF
  (the spec says 35 at SF1 and 45 at SF10). It's harmless and noted in `expected.yaml`.

**Deviations, accepted by the lead:**
- **SeaweedFS memory.** It was OOM-killed at 768 MiB during SF10 writes. It now has a
  1 GiB limit, plus `GOMEMLIMIT` and upload caps. The stack totals ~9.5 GiB with
  Trino at 8 GiB.
- **Disk check placement.** The scale-factor-sized disk check lives in `m0 data gen`,
  not in `doctor`, because `doctor` has no `--sf`.
- **Extra and excluded tables.** `etl_sf1` is generated too, for smoke plans.
  `dbgen_version` is excluded.

## Step 3a: measurement core with Trino ✅

**Delivered:**
- **Commands:** `m0 run <plan> [--engine] [--query] [--resume] [--run-id]` and
  `m0 status`.
- **Plans:**
  - plan expander with ABBA rounds, a seeded shuffle, stable cell IDs, and a `cold`
    policy of `per_query` or `per_block`;
  - `plans/phase-l-smoke.yaml` and `plans/phase-l-cold-check.yaml`.
- **Engine side:**
  - engine adapter interface plus the Trino adapter, with stats and errors from
    `/v1/query/{id}` and cancel via `DELETE`;
  - Trino memory configuration per the 484 docs: `query.max-memory-per-node=4GB` (also
    DataFusion's pool size), 2 GB headroom, heap 6.4 GiB in an 8 GiB container.
- **Measurement:**
  - cold runs: engine restart plus a page-cache drop through a pinned privileged
    busybox helper (no sudo);
  - 20 Hz cgroup sampler, with exact lifetime `memory.peak` used whenever a cell raises
    it, and the object store's peak recorded too;
  - classifier (`m0/classify_rules.yaml`) tested against real Trino 484 errors;
  - automatic restart after `killed` or `unresponsive`, and a timeout with cancel.
- **Results and queries:**
  - result store with `runs.parquet` (47 columns), `env.json`, per-cell artifacts, and
    the cold result rows for the step 4 differ;
  - query manifest plus a smoke subset: TPC-H q1, q3, q6; TPC-DS q3, q7, q55; ETL W1,
    J3, and the deliberate `X1_mem`.
- **Skill and tests:** `runbooks/run.md` and `troubleshooting.md`; 73 tests.

**Acceptance (verified independently by the lead, 2026-10-24):**
- Smoke run `verify-3a`: 33 cells in 49 s. 32 are `ok`, and the deliberate `X1_mem`
  is `err_mem` (`EXCEEDED_LOCAL_MEMORY_LIMIT` at 4 GB). Exit code 3.
- No nulls in wall time, CPU, container/engine/objstore peaks or outcome.
- `--resume` recorded nothing new.
- The sub-agent also showed:
  - **killed recovery:** a `docker kill` mid-query became `killed`, followed by an
    automatic restart, and the remaining cells were ok;
  - **timeout:** the query was cancelled;
  - **cold check:** cold runs were 2.0–2.6 s vs 0.3–0.5 s hot.
- The objstore peaked at ~0.8 GiB, against a 1 GiB limit.

**Lead fixes and findings during verification:**
- **W1 didn't stress skew.** It partitioned by the row's primary key, so partitions
  had 1–3 rows. It now dedups per `skew_customer_sk` (keeping each customer's latest
  row), with a total `ORDER BY` for determinism. The top key's partition holds ~14% of
  rows.
- **Trino container peak isn't per-query.** The JVM keeps its grown heap, so after a
  heavy query every later cell shows ~7.4 GiB. Methodology section 6 now says
  per-query memory comparisons use engine-reported peaks.
- **JVM out-of-memory exits.** Trino's JVM exits by itself on a heap
  `OutOfMemoryError`. The user decided (2026-10-24) to count that as `killed`, with
  `error_class=memory_self_exit` to tell it apart from an OS kill (`oom_killer`).


**Goal.** `m0 run <plan>` executes a plan against Trino, measures every cell the
methodology requires and records the results. No analysis yet.

**Scope:**
- **Plan format** (`plans/*.yaml`) and the **expander**:
  - engines × suites × queries × iterations × memory levels;
  - ABBA engine order;
  - seeded shuffle;
  - deterministic `cell_id`.
- **Engine adapter interface:** `start`, `health`, `execute`, `cancel`, `stats`,
  `stop`. The Trino adapter uses the `trino` client plus `/v1/query/{id}` for CPU,
  peak memory and errors.
- **cgroup sampler:**
  - Finds each container's cgroup.
  - Takes `cpu.stat` deltas.
  - Resets `memory.peak` before each query (kernel ≥ 6.12).
  - Reads `memory.events` `oom_kill`.
  - 10 Hz `memory.current` timeline written to a file.
- **Classifier:** `m0/classify_rules.yaml`, with unit tests on real Trino error
  strings. Unknown errors become `err_other` flagged `unrecognised`.
- **Runner:**
  - Cold run after an engine restart and page-cache drop, then hot iterations.
  - Timeouts with cancel.
  - Restart after `killed` or `unresponsive`.
  - `--resume`.
  - Exit code 3 when some cells failed.
- **Result store:**
  - `results/<run-id>/runs.parquet`;
  - `env.json` (git SHA, lock hash, image digests, host, WSL config);
  - per-cell logs and plans.
- **Query files:**
  - `queries/manifest.yaml`;
  - smoke subset: 3 TPC-H, 3 TPC-DS, 2 placeholder ETL queries.
- `plans/phase-l-smoke.yaml`, at SF1.
- Skill: `runbooks/run.md`, plus the first `troubleshooting.md` entries.

**Acceptance:**
- The smoke plan runs on Trino. Every cell has wall time, CPU-seconds, peak memory
  and an outcome.
- A deliberate low-memory cell is classified `err_mem`, or `killed` if the container
  is OOM-killed, and the runner recovers by itself.
- Re-running with `--resume` does nothing.

**Risks:**
- **SeaweedFS memory on reads is untested.** Its read buffers are uncapped by default.
  Watch `objstore` memory during runs, because it shares the host with the engine
  under test.
- **Storage garbage.** Regenerated tables leave space until SeaweedFS vacuums it
  (11.8 GB physical vs 8.2 GB logical after step 2), and there may be orphan files
  from a scratch namespace. Add a cleanup or vacuum to `m0 data` if it grows.
- The page-cache drop needs root (`sudo sh -c 'echo 3 > /proc/sys/vm/drop_caches'`).
  We'll decide the mechanism with the user. Options: a sudoers entry limited to that
  one command, or a privileged helper container.
- Attributing `memory.peak` correctly to the Trino JVM.

## Step 3b: `dfrunner` single mode (`df`) ✅

**Delivered:**
- **`dfrunner` (Rust, ~900 lines):** adapted from datafusion-distributed's
  `remote-worker` and attributed in `NOTICE`.
  - HTTP API: `/health`, `/status`, `POST /query` (Arrow IPC, structured errors),
    `/queries/{id}` (stats, plan with metrics, pool peak), and cancel.
  - Memory pool `TrackConsumersPool<GreedyMemoryPool>` at 4 GiB, with a per-query
    high-water mark.
  - Disk manager disabled (no spilling); `target_partitions=12`.
  - Iceberg via datafusion-distributed-iceberg's catalog provider over the REST catalog.
- **Pinned versions:**
  - DataFusion 55.0.0 and arrow 59.3.0.
  - datafusion-distributed 4.0.0 **@main ee3eacd**. The `v4.0.0` tag has no Iceberg
    crate and no `remote-worker` (the lead checked: the crate was added on 2026-09-10,
    and no tag includes it yet).
  - iceberg-rust 0.10.1@5b82ab99 and datafusion-iceberg 0.12.0@20327db.
- **Build:** multi-stage Docker build from pinned `rust:1.95.0-trixie` and
  `debian:trixie-slim` (multi-arch digests). Thin LTO and mimalloc. The first build
  took 406 s at ~4.7 GiB peak; a no-change rebuild takes 2 s.
- **Python side:**
  - `m0/engines/df.py` and `m0 env up --engine df`, with engine switching in the runner;
  - `m0 data verify --engine` for engine-neutral table fingerprints;
  - DataFusion classifier rules, tested against 10 real errors;
  - a mechanical-override mechanism, enforced by the manifest.
- **Smoke plan** now runs `trino` and `df` in ABBA order. 125 tests.

**Acceptance (verified independently by the lead, 2026-10-25):**
- `env up --engine df` starts from a stopped stack in 26 s, with the image cached.
- **`data verify --engine df`:** 68/68 tables and 1,072/1,072 columns match Trino's
  fingerprints. The values are real, e.g. the `lineitem` decimal sum
  2,293,813,156,773.36 is identical. **iceberg-rust reads everything Trino writes.**
- **Smoke run `verify-3b`:** 132 cells in 7.0 min.
  - trino: 64 ok, 2 `err_mem` (X1_mem).
  - df: 64 ok, 2 `timeout` (X1_mem).
  - No metric nulls. `--resume` records nothing new.
- **Hot medians at SF1** (informational only): df was faster on all 8 queries, by
  1.2× (TPC-H q1) to 5.7× (J3). df pool peak ≤ 0.24 GiB, container ≤ 0.78 GiB.

**Decisions taken by the lead during verification:**
- Accepted the datafusion-distributed `main@ee3eacd` pin, since no tag contains the
  Iceberg crate. Re-pin to a release tag once one includes it.
- Accepted SeaweedFS read caps (`-s3.readerCacheSizeMB=256
  -volume.concurrentDownloadLimitMB=256`). `df` scans OOM-killed the object store at
  1 GiB. This is infrastructure that both engines use.
- Methodology section 2 now records the dfrunner build (thin LTO, mimalloc): the
  allocator is part of what's measured.

**Findings (to record in `findings/` in step 4b):**
- **F1, df CPU pathology.** X1_mem (`array_agg` fed by a cross join with UNNEST) runs
  on one partition at ~40k rows/s and times out at 120 s. The pool stays at ~195 MB,
  so it never reaches the memory limit. The same query with `count()` takes 4.2 s.
  Suspected cause: very large cross-join batches meeting array_agg's per-group path.
  Not confirmed.
- **F2, df cancel doesn't stop CPU-bound operators.** The query keeps running at
  100% CPU and holding pool memory after a cancel. The runner now restarts an engine
  that's still busy after a timeout. This matters for B3 (a runaway query slows its
  neighbours) and for Rino (query cancellation).
- **F3, memory outside the pool.** A full window sort of SF10 `lineitem` fails
  cleanly with `ResourcesExhausted` at a pool of 4.29 GB, but the container reached
  5.4 GiB: ~1.1 GiB of RSS was outside the pool.


**Goal.** Plain DataFusion runs the same plans as Trino, through the same runner and
sampler.

**Scope:**
- **Rust crate** `dfrunner/`, adapted from datafusion-distributed's
  `benchmarks/remote-worker` and attributed in `NOTICE`:
  - registers the Iceberg REST catalog, with tables from the same warehouse;
  - memory pool limit, with a high-water mark tracked;
  - `target_partitions` = CPU limit;
  - HTTP API: `execute` (Arrow IPC result plus metrics headers), `cancel`, `health`,
    `metrics`;
  - structured errors (`ResourcesExhausted` → `err_mem`).
- **Multi-arch build:** x86_64 now, arm64 for Phase C. The Docker image is built
  locally, and the compose service runs under profile `df`.
- `m0 env up --engine df`, and the `df` adapter.
- **Type check:** every table Trino wrote is read by `dfrunner` and compared on
  count plus checksum, which settles the iceberg-rust compatibility question.
- **Memory parity:** DataFusion's pool limit = Trino's `query.max-memory-per-node`,
  in the same container size.

**Acceptance:**
- The smoke plan runs on both `trino` and `df`.
- Every table generated in step 2 is readable by `dfrunner`, or each unreadable type
  is recorded as a finding.
- A deliberate low-memory cell on `df` is classified correctly as `err_mem` or
  `killed`.

**Carried over from 3a:**
- The adapter interface also needs `metrics()`, `pool_limit_bytes()` and
  `config_files()`.
- The pre-run namespace check is Trino-only.
- Result Parquet uses Trino type names. Map them to Arrow types for the cross-engine
  diff.

**Risks:**
- **Parquet compatibility.** iceberg-rust must read what Trino writes:
  `DELTA_LENGTH_BYTE_ARRAY` strings, INT32/INT64 decimals, and `timestamp(6)` as INT64
  microseconds. The type check in scope covers this.
- **Scan parallelism.** Trino writes at most 8 files per table, and SF10 `lineitem`
  has 8 files with ~16 row groups. That may limit DataFusion's parallelism on 12
  cores. Check that `df` splits by row group or byte range. If layout turns out to
  favour one engine, decide (with the user) on a neutral writer setting and
  regenerate.
- Version alignment. datafusion-distributed pins DataFusion 55 and specific
  iceberg-rust and datafusion-iceberg revisions. `dfrunner` must use exactly those.
- Iceberg REST catalog support in iceberg-rust against Lakekeeper (auth, warehouse
  parameter).
- Rust build time in WSL. Cache target directories.

## Step 3c: simplify result storage

**Goal.** A run records what was tested, how it was configured and what was measured,
in plain JSON, and nothing more. Requested by the user on 2026-10-25: the step 3a/3b
store (Parquet tables, per-cell directories, separate environment and plan snapshots)
is more than the benchmark needs.

**Today** (`results/<run-id>/`, from step 3a):
- `cells.jsonl` plus `runs.parquet` (47 columns, rebuilt from the JSON lines);
- `cells.json` (planned cells), `plan.yaml`, `env.json`, `run.log`;
- `cells/<cell_id>/` per cell: `query.sql`, `engine_stats.json`, `memory.csv`;
- `rows/<cell_id>.parquet` per first run.

**Target layout:**

```text
results/<run-id>/
├── run.json       what and how: plan name and resolved settings, git SHA (+ dirty),
│                  pinned versions (images, engines), key engine settings + config
│                  hashes, host summary, start/end
├── cells.jsonl    one line per cell, appended as it finishes (the only measurements
│                  file; makes --resume and crash safety work): test identity,
│                  measurements, outcome, error line, events (restart, cache drop,
│                  recovery)
├── answers.jsonl  result rows of the first run per engine × query, capped (for the
│                  cross-engine correctness diff)
└── diag/          only when asked for (`m0 repro --diag`, or a plan flag): engine
                   stats/plan and memory timeline for chosen cells
```

**Scope:**
- **Remove** `runs.parquet`, `cells.json`, `plan.yaml`, `env.json`, `run.log`, the
  per-cell `cells/` directories and `rows/*.parquet`. Fold what's still needed into
  the three files above.
  - Planned cells are re-derived from the plan, which is deterministic.
  - Restart and recovery events become fields or event lines in `cells.jsonl`.
- **Trim the cell record** to fields that analysis, the report or the agent actually
  use. Today there are 47 columns. Keep the time-breakdown numbers step 4 needs
  (scan / exchange / compute) as a few extracted fields, not raw engine stats.
- **dfrunner returns JSON results** instead of Arrow IPC (results are small). Drop the
  `pyarrow` dependency from the harness.
- **Results retention:** `m0 results list` and `m0 results prune` (keep named runs,
  delete scratch runs) or an equivalent simple convention. Delete the current scratch
  runs (`killtest`, `cold-check-1`, `smoke-3b`, `phase-l-smoke-*`). Re-run the smoke
  plan to create a fresh reference run.
- **Data fingerprints** (`results/verify/`) and the data-generation manifests
  (`results/datagen/`) are already JSON. Keep them as they are.
- Update `docs/framework-design.md` (results schema, layout), the skill's file map,
  `runbooks/run.md`, and the tests.

**Acceptance:**
- The two-engine smoke plan produces only `run.json`, `cells.jsonl` and
  `answers.jsonl`, plus `diag/` when asked.
- `--resume` still works.
- `m0 status` works.
- `pyarrow` is no longer a dependency.
- Total test count doesn't drop, apart from tests of removed code.

**Note for step 4:** analysis reads `cells.jsonl` with the standard library (or
DuckDB ad hoc). There's no dataframe dependency unless step 4 shows a real need.

## Step 4: analysis, plus the full query catalogue

**Carried over from 3b:**
- **Decimal types differ.** Trino `sum` returns decimal(38,s) and `avg` rounds to the
  input scale. DataFusion returns decimal(p+10,s) and `avg` at scale 6. The differ
  must compare at Trino's scale.
- **Stale schemas.** `df` caches table schemas per process, so restart it after data is
  regenerated.

**Goal.** Turn raw cells into answers.

**Scope:**
- **Differ:** the normalisation rules from methodology section 8, with
  `tie_ambiguous` handling and unit tests (floats, CHAR, NULLs, ties).
- **Stats:**
  - medians, min/max, coefficient of variation;
  - totals over common queries;
  - geomean of ratios;
  - bootstrap confidence intervals.
- **Commands:** `m0 summarize`, `m0 compare`, `m0 explain` (Trino `EXPLAIN ANALYZE`,
  DataFusion metrics), `m0 logs`.
- **Full TPC-H (22) and TPC-DS (99) query catalogue:**
  - Trino SQL is canonical.
  - DataFusion overrides only where needed, each with a reason.
  - Sources attributed.
- Skill: `runbooks/analyse.md`.

**Acceptance:**
- A1/A2 at SF1 on `trino` and `df`.
- `summarize` output stays under 40 lines.
- Every result is diffed. Each mismatch is classified or recorded as a finding.

## Step 4b: investigation helpers

**Carried over from 3b:**
- X1_mem doesn't exercise memory on `df` (finding F1). Add a deliberate memory cell
  that fails cleanly on both engines, e.g. the full window sort from F3, and check its
  behaviour on Trino.
- Record findings F1–F3 in `findings/`.

**Scope:**
- Commands: `m0 triage`, `m0 repro`, `m0 bisect-mem`, `m0 diff`, `m0 sql`.
- `findings/TEMPLATE.md`.
- **Deliberate failures in the smoke plan:**
  - out of memory;
  - corrupted reference result;
  - unsupported syntax;
  - stopped catalog container.
- Skill: `runbooks/investigate.md`, plus `troubleshooting.md` updates.

**Acceptance:** an agent using only the skill finds the real cause of each deliberate
failure.

## Step 5: distributed execution and pressure suites

**Goal.** Cover everything else Phase L needs, and put an environment interface
between the runner and where engines run, so the AWS phase is a second
implementation of that interface rather than a rewrite. Added at the user's request
on 2026-10-25.

**Scope:**
- **Environment interface** (`m0/environments/`), mirroring the engine adapters.
  Today the runner calls `env_local` directly, and `metrics.py` assumes engine
  containers are on the local machine.
  - **Operations:**
    - start, stop and restart an engine on its N nodes, cold;
    - drop page caches on every engine node;
    - per-node CPU and memory samples, plus OOM and exit signals, for the
      classifier;
    - catalog and storage settings for the engines;
    - where results are written.
  - **`local` implementation:** the existing Compose stack, with multi-node engines
    as several containers (`dfd` workers, Trino coordinator plus worker) and per-node
    cgroup sampling.
  - **`aws` stub:** defines the same operations and raises "not implemented" until
    step 7. Step 7 fills it in: per-node metrics are collected on each node and sent
    back to the harness on the coordinator, and results are synced to S3.
  - **The runner, `doctor` and `data` commands use only the interface.** Plans name
    their environment (`env: local`).
  - **Results:** `run.json` (step 3c) records the environment and node layout.
    `cells.jsonl` records per-node peaks and the maximum across nodes.
  - **Acceptance:** the smoke plan runs unchanged through the interface. Engine and
    runner tests use a fake environment (no Docker needed).
- **`dfrunner` node mode (`dfd`):**
  - N workers plus coordinator over Arrow Flight;
  - static worker resolver from compose service names;
  - task metrics exported for the A5 breakdown.
- **Multi-node Trino** for A3: a coordinator plus a worker, the same total resources
  as `dfd`.
- **Optimizer variant** `dfd-jo`: a build with apache/datafusion#24456 applied, with the
  commit pinned (A6).
- **B1 ETL query catalogue:** J1–J4, G1–G2, W1–W3, T1, S1–S2.
- **B2:** memory levels 100/50/25% applied consistently across container, pool and
  Trino limits.
- **B3:** concurrent clients, per-client outcomes, collateral-failure detection.
- Skill: `run.md` and `troubleshooting.md` updates (Flight errors, worker discovery).

**Acceptance:**
- Plans exist and pass at SF1 for A3, A5, A6, B1, B2 and B3.
- B2 shows clean `err_mem` and `killed` outcomes, and RSS-vs-pool data is collected.

**Carried over from 3a:** memory levels are expanded into blocks but not yet applied
to the container, the pool or Trino's limits. The cgroup lookup assumes the systemd
driver, with a cgroupfs fallback.

**Also from 3b:**
- `df`'s pool peak is process-wide, so per-query peaks don't hold under concurrency (B3).
- A cancel can't interrupt CPU loops (F2).
- iceberg-rust makes one scan task per file, and SF10 tables have ≤ 8 files for 12
  partitions. Check scan parallelism, and how `dfd` splits work units.

**Risks:**
- Two engine nodes plus infra must fit in ~11.7 GiB for A3 (2 × ~4 GiB). This may
  only allow a reduced A3 locally.
- The PR #24456 branch may not rebase cleanly onto the DataFusion version
  datafusion-distributed uses.

## Step 6: report

**Scope:**
- `m0 report` generates [report-template.md](report-template.md).
- Charts in `m0/report/` with a colour-blind-safe palette.
- An outcome matrix.
- The Phase L "Exploratory" banner throughout.
- Skill: `runbooks/report.md`.

**Acceptance:** a full report renders from a smoke run with no manual edits, and every
section either has content or says why it's empty.

## Phase L execution and Gate L

1. Run the full Phase L plan at SF10:
   - A1, A2, A5, A6, B1 and B2 in full;
   - A3 and B3 in reduced form.
2. Investigate failures and record findings.
3. Generate the exploratory report.
4. **Gate L (your decision, private):** continue to Phase C, change direction, or stop.
5. Draft upstream issues from confirmed findings, for your approval.

## Step 7: Phase C (AWS)

Starts only after Gate L.

**Scope:**
- The `aws` implementation of the environment interface from step 5.
- `infra/aws/*.sh` and `m0 aws status|cost|ttl|teardown`:
  - tag-based;
  - instance TTL;
  - budget check;
  - `--yes` gate.
- Glue catalog configs for both engines.
- arm64 images pushed to a registry, or built on the nodes.
- Spot price capture into `env.json`, for $/query.
- Phase C plans: the primary tier, the A4 scaling tier, and the SF1000 tier (conditional).
- Before running: pre-registered thresholds published, and maintainers invited to
  review the configs.
- Skill: `runbooks/aws-cost.md` and AWS troubleshooting.

**Acceptance:**
- A smoke run on the smallest tier completes and tears down with zero leftover tagged
  resources.
- The cost of that run matches the estimate within 2×.

## Not scheduled yet

- `docs/announcement-plan.md`: written closer to the first public post, before Phase C.
