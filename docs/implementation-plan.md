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
| 2 | Data generation | M | next |
| 3a | Measurement core with Trino | M | |
| 3b | `dfrunner` single mode (`df`) | L | |
| 4 | Analysis: diff, stats, summaries, full query catalogue | M | |
| 4b | Investigation helpers and deliberate failures | S | |
| 5 | Distributed `dfd`, memory levels, concurrency, B1 queries | L | |
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

## Step 2: data generation

**Goal.** TPC-H and TPC-DS at SF1 and SF10, plus the derived `etl.*` tables, all in
Iceberg. Regeneration is deterministic and the result is validated.

**Scope:**
- `m0 data gen --suite tpch|tpcds|etl --sf N` runs through Trino.
  - Uses `CREATE TABLE … WITH (format_version = 2, format = 'PARQUET') AS SELECT * FROM tpch.sfN.<table>`.
  - zstd compression. Trino's default target file size.
- Namespaces `tpch_sf1`, `tpch_sf10`, `tpcds_sf1`, `tpcds_sf10`, `etl_sf10`.
- `m0 data status`: per table, rows, bytes, file count and snapshot ID, compared with
  the expected values.
- `datagen/expected.yaml`: expected row counts per table and scale factor.
  - TPC-H/TPC-DS counts come from the specs.
  - `etl.*` counts come from the generator.
- `datagen/etl/*.sql`: deterministic derived tables:
  - **Zipf-skewed key tables** (for J3, W1). Skew comes from a hash of the row key, so
    the result is identical on every run.
  - **Duplicated rows** for dedup (W1).
  - **Fact pairs** for fact-to-fact joins (J1).
- Generation is idempotent and resumable. A table that exists and validates is skipped.
  `--force` regenerates it.
- Skill: `runbooks/data.md`.

**Answers two open questions:**
- How do TPC-DS `CHAR(n)` columns end up in Iceberg? Iceberg has no CHAR type: does
  Trino convert or reject them? The answer goes into the methodology's normalisation
  rules.
- Do Trino's TPC-DS types (decimals, dates) all map cleanly? The iceberg-rust side is
  checked in step 3b.

**Acceptance:**
- All tables are present, and their row counts match `expected.yaml`.
- Re-running is a no-op.
- SF10 generation time and size are recorded in the runbook. Budget: under 60 minutes
  for everything at SF10 on the reference machine.
- `doctor` gains a disk-space check sized for the requested scale factor.

**Risks:**
- Trino's `tpcds` connector is slow to generate at SF10. Fallback: generate Parquet
  with tpchgen/tpcdsgen (as datafusion-distributed does), then register it in Iceberg
  via Trino. This changes the source, not the tables.
- SeaweedFS throughput or stability under multi-GB writes.

## Step 3a: measurement core with Trino

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
- The page-cache drop needs root (`sudo sh -c 'echo 3 > /proc/sys/vm/drop_caches'`).
  We'll decide the mechanism with the user. Options: a sudoers entry limited to that
  one command, or a privileged helper container.
- Attributing `memory.peak` correctly to the Trino JVM.

## Step 3b: `dfrunner` single mode (`df`)

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

**Risks:**
- Version alignment. datafusion-distributed pins DataFusion 55 and specific
  iceberg-rust and datafusion-iceberg revisions. `dfrunner` must use exactly those.
- Iceberg REST catalog support in iceberg-rust against Lakekeeper (auth, warehouse
  parameter).
- Rust build time in WSL. Cache target directories.

## Step 4: analysis, plus the full query catalogue

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

**Goal.** Cover everything else Phase L needs.

**Scope:**
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
