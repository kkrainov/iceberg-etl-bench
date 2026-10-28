# Framework design

## Principles

1. **In git from the first commit.** Harness code, queries, configs, run plans,
   pinned versions and docs all live in this repo. Raw results do not (they are
   published separately as release artifacts); compact summaries can be committed.
2. **Strict where results are produced.** Everything that turns into a published
   number is done by code, the same way every time:
   - deploying the environment, generating data, expanding run plans;
   - timing and metric collection;
   - the recorded outcome class of every cell;
   - result diffs, statistics, charts and the report.

   The agent does not hand-compute or hand-edit anything that ends up in results.
3. **Free when investigating.** When something fails or looks odd, the agent works like
   an engineer: it reads logs, inspects containers, queries engine endpoints, reads
   engine source and writes throwaway scripts. The harness offers helpers for the
   common cases, but they're shortcuts, not the only allowed path. Failure modes can't
   all be predicted, so the design doesn't pretend they can.
4. **Cheap by default.** Summaries are compact, and logs and artifacts sit in predictable
   places, so a free-form investigation can still go straight to the right file and
   grep it rather than reading everything.
5. **Python by default. Rust only where the engine requires it.** That's one small
   binary, `dfrunner`, because DataFusion and datafusion-distributed are Rust
   libraries. Trino needs no custom code: official image, config templates, HTTP client.
6. **Same measurement path for every engine.** Both engines run as long-lived servers.
   The same Python client measures wall time, and the same cgroup sampler collects
   CPU and memory.

## Components

```text
                     ┌──────────────── m0 (Python CLI) ────────────────┐
 plans/*.yaml ──────▶│ plan expander → scheduler → engine adapters     │──▶ results/<run-id>/
 queries/**          │ metrics sampler · classifier · differ · stats   │      run.json, cells.jsonl,
 configs/**          │ summarize · compare · report                    │      answers.jsonl, diag/
                     └──────────┬───────────────────────┬──────────────┘
                                │ HTTP (Trino REST)     │ HTTP + JSON
                      ┌─────────▼────────┐    ┌─────────▼───────────────────┐
                      │ Trino 484        │    │ dfrunner (Rust)             │
                      │ official image   │    │ single | coordinator+worker │
                      └─────────┬────────┘    └─────────┬───────────────────┘
                                └──── Iceberg catalog (REST locally / Glue on AWS)
                                      + S3-compatible storage ────┘
```

| Component | Language | Responsibility |
|---|---|---|
| `m0` CLI (`m0/`) | Python 3.12, managed with `uv` | Single entry point for humans and the agent (see the next section) |
| Plan expander | Python | Turns `plans/*.yaml` into a deterministic run matrix: engine × suite × query × iteration × memory level × concurrency, with an ABBA engine order and a seeded shuffle |
| Engine adapters | Python | `trino`: the `trino` client package over HTTP, query stats from `/v1/query/{id}`. `df` / `dfd`: HTTP to `dfrunner`, results returned as JSON. One interface (`m0/engines/base.py`): `start`, `restart`, `health`, `execute`, `cancel`, `stats`, `metrics`, `pool_limit_bytes`, `config_files`, `describe`, `stop`. |
| `dfrunner` | Rust | Adapted from datafusion-distributed's `benchmarks/remote-worker`. Modes: `single` (plain DataFusion, step 3b) and `node` (worker plus HTTP coordinator endpoint, step 5). Adds: Iceberg REST catalog registration via iceberg-rust (Glue in Phase C), memory-pool limit and high-water mark, disabled disk manager, operator metrics, structured errors. Under 1,000 lines. API: [below](#dfrunner-api). |
| Environment | Bash + Docker Compose (local), Bash + AWS CLI (AWS) | `infra/local/compose.yaml`, `infra/aws/*.sh`, wrapped by `m0 env …` |
| Data generator | SQL + Python | `datagen/*.sql`, run through Trino: CTAS from the `tpch`/`tpcds` connectors, then the `etl.*` derived tables |
| Metrics sampler | Python | Reads cgroup v2 files of the engine and `objstore` containers without root: `cpu.stat` deltas, `memory.current` sampled at 20 Hz, lifetime `memory.peak`, `memory.events` `oom_kill`. See [Peak memory](#peak-memory). |
| Classifier | Python | Applies the outcome rules from methodology section 7. Error-pattern table in `m0/classify_rules.yaml`, unit-tested. |
| Differ | Python | `m0/differ.py`: normalises each engine's answer from `answers.jsonl` and compares it with Trino's (methodology section 8). Computed on demand by `summarize` and `compare`; nothing is stored. |
| Stats and report | Python (+ matplotlib for the report) | `m0/stats.py` (standard library): medians, CV, totals over common queries, geomeans, bootstrap CIs. Charts and the Markdown report from `docs/report-template.md` come with step 6. |
| Skill | Markdown | `.claude/skills/m0-bench/SKILL.md` plus runbooks; explains the harness and how to analyse results |

## The `m0` CLI: an interface designed for the agent

Rules every command follows:
- **Short stdout.** A summary of at most ~40 lines by default. Details go to files
  under `results/<run-id>/`, and their paths are printed.
- **`--json`** gives the same summary in machine-readable form.
- **Exit codes** mean something: `0` ok, `1` harness error, `2` prerequisite
  missing, `3` run completed with failed queries (expected in this benchmark),
  `4` guard refused (budget, TTL, or a destructive action without `--yes`).
- **Idempotent and resumable.** `m0 run` skips cells already recorded for the run
  ID. `--resume` continues after an interruption.
- **No interactive prompts,** except an explicit `--yes` gate on billable actions.

| Command | Does |
|---|---|
| `m0 doctor` | Checks tools, versions, Docker, cgroup v2, WSL settings, disk, AWS credentials (Phase C) |
| `m0 env up/down/status [--target local\|aws]` | Starts and stops the stack. `down` on AWS also verifies that nothing tagged is left running. |
| `m0 data gen --suite tpch\|tpcds\|etl --sf N` | Generates and validates the tables (row counts per table against expected values) |
| `m0 data verify --engine E [--suite S] [--sf N]` | Fingerprints every table through engine E (row count; per column non-null count, min, max, sum or sum of lengths) into `results/verify/<E>/`, and compares with Trino's fingerprints: can E read everything Trino wrote, with the same values? |
| `m0 run <plan.yaml> [--engine E] [--query Q] [--resume] [--run-id ID]` | Executes the plan's cells (or those matching `--engine`/`--query`, both repeatable) into `results/<run-id>/`. Prints only non-`ok` cells and restarts as they happen, then outcome counts per engine. An existing run ID needs `--resume` (exit code 4 otherwise, also if the plan file changed); `--resume` without `--run-id` continues the latest run of that plan. |
| `m0 status [<run-id>\|latest]` | Progress, outcome counts, and the last failure in one line |
| `m0 results list\|prune [--keep ID] [--yes]` | Lists runs under `results/`; `prune` deletes runs not named in `results/keep.txt` or `--keep` (dry run without `--yes`). See [Results](#results). |
| `m0 summarize [<run-id>\|latest] [--with RUN]…` | `--with` adds the engines of another run (e.g. the `trino` and `df` baseline of an earlier run on the same machine and data); an engine may come from one run only. At most 40 lines: outcome counts per engine (a completed cell whose answer differs from Trino's shows as `ok_mismatch`), answer diffs per engine with the normalisations used, suite totals over common queries with ratio and geomean [95% CI], top 10 slowest relative queries, non-`ok` cells grouped by engine, outcome and error signature, every answer mismatch. Sections are capped and say what `--json` has (everything). Exit code 3 if any cell is not `ok`. See [Analysis](#analysis). |
| `m0 compare <run-a> <run-b> [--engines A:B]…` | Before/after comparison per engine, e.g. a new upstream version; `--engines df:df-jo` compares engine `df` of run A with `df-jo` of run B (A and B may be the same run): totals and geomean of B/A over queries both runs completed, queries that moved beyond noise, outcome changes, changed images and config files |
| `m0 explain <run-id> <engine> <query> [--top N] [--timeout S]` | Re-runs one query on the live engine with the run's scale factor and SQL: Trino `EXPLAIN ANALYZE` plus `/v1/query/{id}` operator summaries; `df` runs the query and reads dfrunner's plan with metrics. Saves `diag/explain.<engine>.<suite>.<id>/{query.sql, plan.txt, operators.json, stats.json}`, prints the top operators by time. Exit code 2 if the engine isn't up. |
| `m0 logs <run-id> <engine> <query> [--grep P] [--tail N]` | `docker logs` of the engine container for each round's window of that query's cells (`started_at` to the end of the last cell, plus a few seconds). Says when the container was recreated since and the lines are gone (exit code 2 if all are). |
| `m0 triage <run-id>` | Best-effort grouping of non-`ok` cells and harness errors by signature (outcome, engine, normalised error). Prints each group with paths to its logs, memory timeline and plan. Errors no rule recognises are grouped as `unrecognised` for the agent to look at. |
| `m0 repro <cell-id> [--times N] [--diag]` | Re-runs one cell in isolation, optionally with extra diagnostics: `EXPLAIN ANALYZE`, verbose engine logging, 50 Hz memory sampling. Reports whether the outcome is deterministic or flaky. |
| `m0 bisect-mem <cell-id>` | Binary-searches the memory budget to find where a query flips from `ok` to `err_mem` or `killed`. Also reports peak RSS against the pool limit at each step. |
| `m0 diff <run-id> <query> [--rows N]` | For `ok_mismatch`: which normalisation rules applied, and the first N differing rows |
| `m0 sql <engine> "<query>" [--rows N]` | Ad-hoc probe against a running engine with bounded output, e.g. checking types or row counts during an investigation. Never recorded as a measurement. |
| `m0 report <run-id>…` | Builds the Markdown report and charts |
| `m0 aws cost\|ttl\|teardown` | Phase C cost guardrails (see the infrastructure doc) |

## Claude Code skill

The skill is part of the framework, not separate documentation. It is built in the same
change as the commands it describes, and a harness feature is not done until its
skill entry exists and an agent has used it successfully on the smoke plan.

### Purpose

The skill tells an agent:
- what this benchmark is and what each question means;
- which `m0` commands exist and in what order to use them;
- how to read their output;
- how to **investigate** when something goes wrong, whether the harness, the
  environment or a query fails, down to a root cause and a recorded finding;
- when to stop and ask the user.

It holds no measurement logic. Numbers, recorded outcomes and diffs come from the CLI.
For investigation it gives orientation and hints (where things are, what usually goes
wrong, known upstream gaps) and leaves the method to the agent.

### Files

```text
.claude/skills/m0-bench/
├── SKILL.md                 # always loaded; kept under ~150 lines
└── runbooks/                # loaded only when the task needs them
    ├── preflight.md         # m0 doctor, prerequisites, AWS account bootstrap
    ├── environment.md       # m0 env up/down/status, local and AWS
    ├── data.md              # m0 data gen, validation, regeneration
    ├── run.md               # plans, single cells, resume, B2/B3 specifics
    ├── analyse.md           # reading summaries, noise rules, comparing runs
    ├── investigate.md       # failure decision tree, per-outcome playbooks, findings
    ├── report.md            # m0 report, publishing checklist
    ├── aws-cost.md          # guardrails, TTL, teardown, start-of-session check
    └── troubleshooting.md   # symptom → diagnostic command → fix
```

**`SKILL.md`** has `name` / `description` frontmatter and contains:
- A one-paragraph description of the benchmark and its two phases.
- **Hard rules:**
  - Never pass `--yes` to a billable command without asking the user first.
  - Start every session with `m0 aws status` once Phase C exists.
  - Never edit results or baseline configs by hand (see the investigation guardrails).
  - Treat exit code `3` (some queries failed) as data, not an error.
- The command table, and the standard loop: `doctor → env up → data gen → run → summarize → (investigate) → report → env down`.
- Pointers to the runbooks.

**`troubleshooting.md`** covers, for each case, the symptom, the `m0` command that confirms
it, and the fix:
- Spot interruption.
- Engine process killed for running out of memory, told apart from a clean memory error.
- Trino failing to start: JVM heap, config, catalog.
- Catalog or S3 permission errors (REST locally, Glue on AWS).
- Arrow Flight connection errors between `dfrunner` nodes.
- WSL resource limits and noisy-neighbour effects on the host.

### Investigating failures

There are two kinds of failure, and they are handled differently:
- **Harness or environment failures** make results invalid. Examples: a container
  crashed outside a query, data validation failed, the disk is full, configuration
  drifted, the sampler lost data. They must be fixed, and the affected cells re-run.
- **Query outcomes** (`err_mem`, `killed`, `timeout`, `unresponsive`, `ok_mismatch`,
  `err_unsupported`, `err_other`) **are results.** They are investigated to explain
  them, never "fixed" by tuning one engine until the query passes.

**Investigation is open-ended.** The agent may use any read-only means:
- logs under `results/<run-id>/logs/` and `docker logs`;
- `docker stats` and `docker inspect`, cgroup files, `dmesg` for OOM-killer messages;
- Trino's REST API and system tables; `dfrunner` metrics endpoints;
- `m0 sql` probes;
- engine and upstream source code (cloned into scratch);
- small throwaway scripts in a scratch directory.

It can also re-run cells with `m0 repro` and change things in **variant** runs (see the
guardrails).

**Suggested approach (`investigate.md`), not a required procedure:**
1. Rule out the environment first (`m0 doctor`, `m0 env status`). Most surprising
   failures are environmental.
2. Use `m0 triage` to see what failed together. Look at one representative per group.
3. Check whether it repeats (`m0 repro --times 3`) before digging deep.
4. Dig with whatever fits. `investigate.md` lists hints per outcome class:

| Outcome | Where to look first |
|---|---|
| `err_mem` / `killed` | `m0 explain` for the memory-holding operator; memory timeline; `dmesg`; `m0 bisect-mem` for the threshold. Check RSS against the pool limit (accounting gap?) and whether the operator can't spill (hash join, window). |
| `timeout` | Plans across engines, `df` vs `dfd`, join order, skew in the A5 breakdown |
| `unresponsive` | Node logs, Flight errors, restart history, host memory |
| `ok_mismatch` | `m0 diff`; harness normalisation first (floats, CHAR, ties, NULLs); then `df` vs `dfd` and `m0 sql` probes |
| `err_unsupported` | The missing feature; whether a mechanical rewrite is allowed; existing upstream issues |
| `err_other` / `unrecognised` | Logs and anything else. This is where most open-ended work happens. |

5. Write down what was learned in `findings/<id>.md`, following the short template in
   `findings/TEMPLATE.md`:
   - symptom and evidence;
   - root cause, marked *confirmed* or *suspected*;
   - upstream link if any;
   - action taken.

**Learning loop.** When an investigation finds a new failure pattern, the agent turns
it into code: a classifier rule with a unit test, a `doctor` check or a
`troubleshooting.md` entry. The next occurrence is then recognised automatically.
Strict parts grow from real failures instead of being designed up front.

**Guardrails.** These stay strict because they protect the results and your wallet:
- **Variants, not edits.** Any engine configuration change becomes a named variant
  with its own run ID. Baselines are never changed during an investigation.
- **Recorded outcomes come from code.** The agent can conclude that an `err_other` was
  really a memory error, but it fixes the classifier rule and re-classifies. It never
  edits results by hand.
- **Harness fixes.** Every affected cell is re-run.
- **Read-only probing.** No writes to the benchmark tables, and no changes to running
  engines outside a variant.
- **Upstream issues and posts.** These are drafted only, never filed or posted without
  the user's approval.
- **Ask the user first** when:
  - a baseline configuration would change;
  - a fairness question comes up;
  - a suspected engine bug is ready to draft upstream;
  - a repro would cost money (Phase C).
- **Bounded effort.** If a group is still unexplained after a reasonable dig, record it
  as *suspected* with the evidence so far, and move on.

### Keeping token use low

- Only `SKILL.md` is always loaded. Runbooks load on demand.
- **Start from summaries.** Use `m0 summarize`, `m0 compare` and `m0 triage` before opening
  files.
- **A file map in `SKILL.md`** shows where every log, plan and metric lives for a given
  cell, so the agent can grep the right file instead of listing directories.
- **Query results, don't print them.** Read `cells.jsonl` and `answers.jsonl` with a short Python or DuckDB query,
  and read logs with `grep`, `tail` or `m0 logs --grep`, rather than whole files.
- **Noise rules** in `analyse.md`, so the agent doesn't chase variance:
  - Trust suite totals over single S3-backed queries, which can vary by ±50%.
  - Ignore differences inside the CV flag.
  - Re-run a cell before calling a result a finding.

### Tests

- `tests/test_skill.py` checks that every `m0` command, flag and exit code mentioned in the
  skill exists in the CLI. This stops the skill drifting from the code.
- The smoke plan is the acceptance test. It includes deliberate failures:
  - an out-of-memory cell;
  - a result mismatch, produced by a corrupted reference;
  - an unsupported-syntax query;
  - an injected environment fault (a stopped catalog container).

  An agent using the skill must take a clean checkout to a `m0 summarize` of the
  smoke run, and must find the real cause of each deliberate failure. It may get there
  however it likes.

## Build order

Each step ships harness code, tests and the matching skill sections together. Scope,
acceptance criteria and risks per step: [implementation-plan.md](implementation-plan.md).

| Step | Harness | Skill |
|---|---|---|
| 1 ✅ | Repo skeleton, `m0 doctor`, local `env up/down/status`, versions lockfile | `SKILL.md` core, `preflight.md`, `environment.md` |
| 2 ✅ | Data generation and validation (TPC-H, TPC-DS, `etl.*`) | `data.md` |
| 3 ✅ | Trino adapter, `dfrunner` (`single` mode), cgroup sampler, classifier, smoke plan | `run.md`, first `troubleshooting.md` entries |
| 4 ✅ | Differ, stats, `summarize`, `compare`, `explain`, `logs` | `analyse.md` |
| 4b | `triage`, `repro`, `bisect-mem`, `diff`, `sql`; `findings/TEMPLATE.md`; deliberate-failure cells in the smoke plan | `investigate.md`, `troubleshooting.md` |
| 5 | `dfrunner` `node` mode (`dfd`), A3 setup, B2 memory levels, B3 concurrency | `run.md` and `troubleshooting.md` updates |
| 6 | `report` | `report.md` |
| 7 (Phase C) | `infra/aws`, `m0 aws …` guardrails | `aws-cost.md`, AWS troubleshooting |

## Repository layout

```text
m0-bench/
├── README.md  LICENSE  NOTICE
├── pyproject.toml  uv.lock          # Python deps: trino, pyyaml (matplotlib with the report)
├── versions.lock.yaml               # Trino image digest, DataFusion/datafusion-distributed/iceberg-rust revs, Lakekeeper, SeaweedFS, JDK
├── m0/                              # Python package: cli, plan, queries, engines/, metrics, classify (+ classify_rules.yaml), runner, store, differ, stats, analysis, explain, logs, report
├── dfrunner/                        # Rust crate: Cargo.toml, Cargo.lock, src/, patches/ (build variants)
├── queries/
│   ├── manifest.yaml                # id, suite, class, ordered, tie_ambiguous, rewrite reasons
│   ├── tpch/  tpcds/  etl/          # canonical SQL (Trino dialect)
│   └── overrides/datafusion/        # only where a rewrite is needed, each with a reason
├── datagen/                         # CTAS and derived-table SQL, expected row counts
├── configs/{trino,datafusion}/      # templated per profile: local-sf10, aws-sf100, …
├── plans/                           # phase-l-smoke.yaml, phase-l.yaml, phase-c.yaml, …
├── infra/{local,aws}/
├── findings/                        # confirmed findings (fixed format), committed
├── tests/                           # unit tests: classifier, differ, plan expander, stats, skill references
├── docs/
├── .claude/skills/m0-bench/
└── results/                         # git-ignored
```

## dfrunner API

`dfrunner/` (Rust, ~700 lines) serves one long-lived `SessionContext` over HTTP on
`127.0.0.1:9000`. Configuration: `configs/datafusion/local/dfrunner.properties`
(`dfrunner.*`, `catalog.*`, `s3.*`, and any `datafusion.*`/`iceberg.*` session option,
each with its reason), optionally a second file whose keys win (`DFRUNNER_CONFIG_EXTRA`,
used by the `-cs` variants), S3 credentials from `AWS_*` variables. `dfrunner
healthcheck` is the container health check.

| Endpoint | Does |
|---|---|
| `GET /health` | 200 once the catalog is connected (startup waits up to 60 s for it) |
| `GET /status` | Versions compiled in (DataFusion, datafusion-distributed, iceberg-rust, datafusion-iceberg; from `Cargo.lock`) and the build `variant` with its detail, settings (file, applied session options, `target_partitions`, pool kind and limit, disk manager, catalog, the physical optimizer rule names in order, the join-related optimizer options), pool `limit`/`reserved`/`peak`, namespaces (re-listed), running query IDs |
| `POST /query` `{"sql", "query_id"}` | Plans and executes read-only SQL (DDL, DML and `SET` rejected), collects every batch, returns 200 with `{"columns": [{name, type}], "rows": [[…]]}` (canonical JSON values, see [Results](#results)) plus `x-dfrunner-{query-id,elapsed-ms,pool-peak-bytes,rows}` headers. On failure: 422 `{"query_id", "error": {variant, contexts, root_message, message}}`, where `variant` is the root `DataFusionError` variant (`ResourcesExhausted`, `NotImplemented`, `Plan`, `SQL`, `Execution`, …, or `Cancelled`) and `contexts` the context labels on the way (e.g. `IcebergError(FeatureUnsupported)`). 409 if the ID is already running. |
| `GET /queries/{id}` | Final stats of one of the last 512 queries: `elapsed_ms`, `planning_ms`, `execution_ms`, `process_cpu_s` (getrusage delta), `rows`, `pool {limit, at_start, peak, at_end}`, `error`, and `metrics {operators: [{depth, op, metrics}], plan}` (physical plan with metrics). 202 while running. |
| `DELETE /queries/{id}` | Cancel: aborts the query's task. A client that disconnects also aborts it. |

- **Memory:** the pool is DataFusion's own `TrackConsumersPool<GreedyMemoryPool>` (what
  `with_memory_limit` builds), wrapped by `PeakPool`, which only records `reserved()`
  after each successful grow. `POST /query` resets the high-water mark, so with one
  query at a time `pool.peak` is that query's; the adapter records it as
  `engine_peak_mem_bytes`. Container memory (`peak_mem_bytes`) shows what the pool
  doesn't track.
- **Results are buffered** on the server until the query ends, so an error is always a
  clean 422 rather than a broken stream. Benchmark results are small (aggregates,
  `LIMIT 100`); the harness clock still covers transfer and decoding.
- **Tables:** catalog `iceberg` = the REST catalog; schemas = namespaces (any name
  resolves, so namespaces created later work); tables are datafusion-distributed's
  `IcebergCatalogTableProvider`, cached per table for the schema, with metadata
  reloaded on every scan.
- **JSON results:** dfrunner encodes values with the same canonical rules the Trino
  adapter applies to the trino client's Python values (`dfrunner/src/query.rs`
  `to_json`, `m0/engines/base.py` `canonical`; table in [Results](#results)), so the
  two engines' answers compare as JSON. Column types are each engine's own names
  (Arrow type names here).

### Engine variants (step 4c)

Single-node DataFusion variants for suite A6 (methodology section 2). Each is a compose
service with `df`'s limits, port and base config, and its own engine name in the
harness (`m0/engines/df.py` `VARIANTS`; the DataFusion dialect, overrides and error rules
apply to all). Only one runs at a time.

| Engine | Image (build variant) | Extra config |
|---|---|---|
| `df` | `m0-dfrunner:local` (`df`) | none |
| `df-cs` | `m0-dfrunner:local` (`df`) | `column-stats.properties`: `iceberg.column_stats_enabled=true` |
| `df-jo` | `m0-dfrunner-jo:local` (`jo`) | none |
| `df-jo-cs` | `m0-dfrunner-jo:local` (`jo`) | `column-stats.properties` |
| `df-sail` | `m0-dfrunner-sail:local` (`sail`) | none |
| `df-sail-cs` | `m0-dfrunner-sail:local` (`sail`) | `column-stats.properties` |

How each build variant is made (`dfrunner/Dockerfile`, `ARG DFRUNNER_VARIANT`, which runs
`dfrunner/patches/prepare.sh`; one cargo target cache per variant):
- `df`: plain dfrunner, DataFusion 55.0.0 from crates.io.
- `jo`: apache/datafusion#24456 (cost-based join order enumeration) backported onto
  55.0.0. `prepare.sh` downloads the crates.io 55.0.0 sources of datafusion-common,
  datafusion-physical-optimizer and datafusion-physical-plan, checks them against the
  checksums in `Cargo.lock`, applies `patches/datafusion-55.0.0-pr24456.patch` (its
  header lists what was taken and every adaptation), appends
  `patches/jo.cargo-patch.toml` (`[patch.crates-io]` to those paths) and builds with
  `patches/jo.Cargo.lock` (the `df` lockfile with those three crates as path
  dependencies). The PR's `datafusion.optimizer.join_enumeration` defaults to true and is
  left so. `versions.lock.yaml` pins the PR head, its merge base and the patch hash.
- `sail`: cargo feature `sail-join-reorder` compiles `src/sail_join_reorder/` (Sail's
  `JoinReorder` rule, lakehq/sail pinned in `versions.lock.yaml`), which `main.rs` inserts
  just before `JoinSelection` in DataFusion's default physical rule list, with default
  `JoinReorderOptions`. Sail's other physical rules are not added.

`m0 env up --engine <variant>` builds the image through the service that builds it
(`df`, `df-jo`, `df-sail`) and starts the variant. `run.json` records the variant through
`/status` (`versions.variant`, `versions.variant_detail`, the rule list and the session
options) and hashes its config and build files. datafusion-distributed's own physical
rules are not used in single mode, and its Iceberg provider registers no optimizer rules,
so every variant reads the same tables through the same provider.

## Run plans

`plans/*.yaml`, expanded by `m0/plan.py` into an ordered cell list. The same plan
always gives the same list.

```yaml
name: phase-l-smoke      # default: file name
phase: L
sf: 1                    # ${tpch}/${tpcds}/${etl} in queries -> iceberg.<suite>_sf1
seed: 20261024           # query-order shuffle
engines: [trino]
rounds: 1                # default 2 with several engines (ABBA), else 1
mem_levels: [100]        # % of the baseline memory budget (B2, step 5)
iterations: 3            # hot runs after each query's first run (iteration 0)
cold: per_block          # per_query | per_block
timeout_s: 600
result_rows_cap: 100000  # result rows kept per answer (answers.jsonl)
diag: false              # true, or queries [tpch/q01]: write diag/<cell_id>/ for those cells
queries:
  - {suite: tpch, ids: [q01, q03]}
  - {suite: etl, ids: [X1_mem], iterations: 0, expect: err_mem, timeout_s: 120}  # per-entry overrides
```

- **Order:** rounds → engine blocks (engines reversed on odd rounds, so two engines
  over two rounds run A B B A) → memory levels → queries → iteration 0, then the hot
  iterations of that query. Query order is shuffled per round with
  `Random("<seed>/<round>")`; every engine sees the same order within a round.
- **`cell_id`:** `<engine>.m<mem_level>.r<round>.<suite>.<query>.i<iteration>`, e.g.
  `trino.m100.r0.tpch.q01.i0`. It doesn't depend on the order, the seed or the filters.
- **Cold policy:** `per_query` restarts the engine and drops the page cache before
  every query's iteration 0 (methodology section 5). `per_block` does it once per
  engine block, for fast smoke runs. The first cell a run executes in a block is
  always cold, also when `--query` or `--resume` skipped the planned one. A restart
  after `killed`/`unresponsive` drops no cache and doesn't make the next cell cold.
- **Cold-run mechanics:** the engine container is recreated with its current limits
  (`env_local.restart_engine`, no catalog smoke write), the adapter waits until a
  `SELECT 1` works, then `docker run --rm --privileged <cache_drop image> sh -c 'sync;
  echo 3 > /proc/sys/vm/drop_caches'` drops the WSL VM's page cache (image pinned in
  `versions.lock.yaml`; no sudo).

## Peak memory

cgroup v2 `memory.peak` can only be reset by writing to it. The file is root-owned,
and since kernel 6.12 a reset only applies to the file descriptor that wrote it. The
harness runs unprivileged, so it uses:

- **Lifetime `memory.peak` (exact)** when the container's lifetime peak rose during
  the cell: the new high was reached inside the cell (`peak_mem_source = memory.peak`).
  Always true for the first heavy query after a restart.
- **Otherwise the maximum of `memory.current` sampled at 20 Hz**
  (`peak_mem_source = sampled`). It can miss spikes shorter than 50 ms, so it's a
  lower bound. The full timeline is written to `diag/<cell_id>/memory.csv` when the plan
  asks for diagnostics (`diag`).
- **Engine-reported peak** alongside (`engine_peak_mem_bytes`: Trino
  `peakTotalMemoryReservation`; DataFusion the pool high-water mark from dfrunner).

A privileged sidecar holding the `memory.peak` file descriptor open would give exact
per-query peaks. It was not built, because the lifetime-peak rule already covers the
cases that matter (a query that drives the container to a new high, e.g. before an OOM
kill), and it would add a long-running privileged container.

Caveats:
- For a JVM, container memory mostly tracks heap growth and garbage, not the query.
  With `-XX:InitialRAMPercentage=80`, Trino's container memory rises across a block
  towards ~7.5 GiB and stays there. Use `engine_peak_mem_bytes` for the query, and
  container memory for "how close to the container limit".
- `memory.current` includes page cache charged to the container. For `objstore` that's
  a large share (reclaimable, so not an OOM risk by itself).

## Results

Every run writes plain JSON to `results/<run-id>/` (`m0/store.py`):

```text
results/<run-id>/
├── run.json       what and how: run ID, plan (file, hash, resolved settings), git SHA
│                  and dirty flag, versions.lock.yaml (content and hash), image IDs and
│                  digests, per engine its reported version and key settings plus hashes
│                  of its config files, container limits, host (CPU, RAM, kernel, Docker,
│                  cgroup), WSL config, start and end time, one entry per --resume
├── cells.jsonl    one line per finished cell, appended and fsynced as it finishes
├── answers.jsonl  result rows of the first completed run per engine × query, capped
└── diag/<cell_id>/  query.sql, stats.json (engine stats with plan), memory.csv (20 Hz
                   timeline); only for cells the plan's `diag` selects
```

- **Planned cells** aren't stored: `m0.plan.expand(run.json["plan"])` gives them again
  (the expander is deterministic). `--resume` refuses a run whose `plan_hash` (of the
  resolved plan) differs from the plan file's.
- **`cells.jsonl`** is the only measurements file. A crash loses at most the running
  cell, and a torn last line is ignored. Read it with the standard library or DuckDB
  (`SELECT … FROM read_json_auto('results/<run-id>/cells.jsonl')`).
- **Retention:** `m0 results list` lists runs; `m0 results prune` deletes every run
  not named in `results/keep.txt` (run IDs or globs, one per line) or `--keep`, as a
  dry run unless `--yes`. Runs in progress are never deleted. `results/datagen/` and
  `results/verify/` hold other artifacts and are never listed or pruned.

### cells.jsonl

One JSON object per cell, every field present (null when unknown), in this order
(`m0/store.py` `FIELDS`):

| Group | Fields |
|---|---|
| Identity | `cell_id, seq, block, round, engine, mem_level, suite, query_id, iteration` |
| Cold protocol | `cold` (iteration 0 right after a restart + cache drop), `events`: what happened before the cell, e.g. `[{"event": "restart", "reason": "cold run", "s": 13.7}, {"event": "cache_drop", "s": 0.9}]`. Restart reasons: `cold run`, `new block`, `after killed`, `after unresponsive`, `after timeout (engine still busy after cancel)`. |
| Time | `started_at, wall_ms` (harness: execute + fetch all rows), `cpu_s` (cgroup `usage_usec` delta), `engine_elapsed_ms, engine_cpu_s`, `planning_ms`, and the time breakdown `scan_ms, exchange_ms, compute_ms` |
| Memory | `peak_mem_bytes, peak_mem_source` (see [Peak memory](#peak-memory)), `engine_peak_mem_bytes, mem_limit_bytes, pool_limit_bytes, objstore_peak_mem_bytes, oom_kills` |
| Outcome | `outcome, expected_outcome` (plan `expect`), `error_class` (rule id or signal), `error_line, unrecognised, engine_query_id` |
| Result | `rows` (row count), `answer`: `full`/`truncated` on the cell whose rows went to `answers.jsonl`, else null |

Time breakdown: operator time summed over threads (so it can exceed `wall_ms`), from
the engine's final stats.

| Field | Trino (`/v1/query/{id}` `operatorSummaries`) | DataFusion (dfrunner operator metrics) |
|---|---|---|
| `planning_ms` | `planningTime` | `planning_ms` |
| `scan_ms` | wall time (addInput + getOutput + finish) of `TableScanOperator`, `ScanFilterAndProjectOperator`; includes waiting for S3 reads | scan timer of a scan operator, if it has one. The Iceberg `DataSourceExec` has none, so null for now. |
| `exchange_ms` | wall time of exchange, merge and output operators (`ExchangeOperator`, `LocalExchange*`, `MergeOperator`, `LocalMergeSourceOperator`, `PartitionedOutputOperator`, `TaskOutputOperator`) | `elapsed_compute + repartition_time + send_time` of `RepartitionExec`, `CoalescePartitionsExec`, `SortPreservingMergeExec` (and shuffle operators in step 5) |
| `compute_ms` | wall time of every other operator | `elapsed_compute` of every other operator |

The two engines measure differently (Trino operator wall time includes blocking reads
inside an operator, DataFusion `elapsed_compute` excludes async waits), so compare each
bucket within an engine, and across engines only as a rough share.

`outcome` in `cells.jsonl` is never `ok_mismatch`: the runner records `ok` for a completed
cell, and `m0 summarize` derives `ok_mismatch` from the answer diff (see
[Analysis](#analysis)).

### answers.jsonl

One line per engine × query: the first cell of that query that completed (usually
iteration 0 of round 0):
`{"cell_id", "engine", "suite", "query_id", "columns": [{"name", "type"}], "row_count",
"truncated", "rows": [[…], …]}`. At most `result_rows_cap` rows and 64 MiB are kept.
`type` is the engine's own type name (`decimal(38, 2)` on Trino, `Decimal128(38, 2)` on
DataFusion).

Values use one canonical JSON encoding per type in both engines, so answers compare as
JSON (`m0/engines/base.py` `canonical`, dfrunner `to_json`):

| Type | Encoding |
|---|---|
| NULL | `null` |
| boolean, integers | JSON boolean, number |
| real, double | JSON number (REAL by its shortest decimal form); NaN and infinities as `"NaN"`, `"Infinity"`, `"-Infinity"` |
| decimal | string with the type's scale: `"1.50"` (the differ normalises scales; methodology section 8) |
| varchar, char | string (char padding as the engine returns it) |
| date | `"YYYY-MM-DD"` |
| timestamp | `"YYYY-MM-DDTHH:MM:SS.ffffff"`; with time zone converted to UTC and suffixed `Z` |
| varbinary | lowercase hex string |
| array, row | JSON array |
| anything else | the engine client's string form |

## Analysis

`m0 summarize` and `m0 compare` read `cells.jsonl` and `answers.jsonl` only
(`m0/analysis.py`), so they work on any run, also one copied from another machine.

- **Answer diffs** (`m0/differ.py`, methodology section 8): each non-Trino answer is
  compared with Trino's answer of the same query. Columns by position; decimals at the
  lower of the two result scales, rounded half up, one unit in the last place allowed;
  floats with a tolerance; CHAR padding stripped; NULL equals NULL. Rows as a multiset,
  then, for ordered queries, the sequence of `sort_keys` values (manifest), where
  NULL placement is free. `tie_ambiguous` queries are compared on their sort-key columns
  only. Each diff is `match` (with the normalisations it needed), `mismatch` (with the
  first difference), `unverified` (a truncated, unordered answer) or `no_reference`.
- **Effective outcome:** every `ok` cell of an engine x query whose diff is `mismatch`
  counts as `ok_mismatch` in the summary and in the stats. The answer comes from the
  first completed cell; the other iterations are assumed to return the same rows.
- **Stats** (`m0/stats.py`, methodology section 9): per engine x query the median, min
  and max of the `ok` hot runs (iteration 0 when a plan has no hot runs), the cold
  time (median of iteration 0), and the CV of the hot runs. A query is completed when
  all its cells are `ok`/`ok_mismatch`. Suite totals sum medians over the queries
  every engine completed. Ratios are Trino / engine. The 95% confidence intervals
  come from 1,000 bootstrap resamples of each query's runs (seed fixed in
  `m0/stats.py`, so the same cells give the same interval).


## Upstream code reuse

All sources below are Apache-2.0. Code is copied and adapted (no build-time
dependency on the source repos except the engine libraries themselves). Each adapted
file keeps its licence header plus a `Derived from <repo>@<sha>:<path>` line, and
`NOTICE` lists every source.

| Source | What we take | Into |
|---|---|---|
| datafusion-contrib/datafusion-distributed `benchmarks/remote-worker` | HTTP query endpoint + worker server skeleton, worker resolution | `dfrunner/` |
| datafusion-distributed `iceberg/` | Catalog-backed table provider usage (as a library dependency) | `dfrunner/` |
| datafusion-distributed `benchmarks/src/{run,stats,compare}.rs` | Iteration and comparison logic, as a reference for the Python port | `m0/stats.py` |
| apache/datafusion `benchmarks/queries` | TPC-H/TPC-DS query text known to plan in DataFusion | `queries/` (Trino variants are canonical) |
| apache/datafusion#24456 (unmerged PR, pinned head) | Cost-based join order enumeration, backported to 55.0.0 as a patch applied at build time | `dfrunner/patches/` (variant `jo`) |
| lakehq/sail `crates/sail-physical-optimizer/src/join_reorder/` | `JoinReorder` physical optimizer rule | `dfrunner/src/sail_join_reorder/` (variant `sail`) |
| trinodb/trino `tpcds`/`tpch` connectors and benchmark queries | Data generation via CTAS; Trino query text | `datagen/`, `queries/` |
| ClickHouse/ClickBench | Run-script structure only. Its Trino timing method (CLI JVM startup counted in each query) is a known flaw we avoid. | — |
| gabotechs/datafusion-distributed-dev-tools | Reference for remote cluster layout and the Trino config they used (we document how ours differs) | `docs/` |

Upstream repos are inspected in a scratch clone. Their build scripts are never run
from inside the clones.

## Testing the harness itself

- Unit tests for the classifier (real error strings from both engines), the differ
  (floats, CHAR padding, ties, NULLs), the plan expander (determinism, ABBA order)
  and stats.
- `plans/phase-l-smoke.yaml`: SF1, a handful of queries per suite, every engine,
  and one deliberate out-of-memory cell. It must finish in under 10 minutes and is
  run after every harness change.
