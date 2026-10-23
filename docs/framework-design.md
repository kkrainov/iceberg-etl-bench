# Framework design

## Principles

1. **In git from the first commit.** Harness code, queries, configs, run plans,
   pinned versions and docs all live in this repo. Raw results do not (they are
   published separately as release artifacts); compact summaries can be committed.
2. **Deterministic work lives in scripts.** Anything that can be decided by rules is
   done by code:
   - deploying and tearing down the environment,
   - generating data,
   - expanding run plans,
   - timing, metric collection,
   - classifying outcomes,
   - diffing results,
   - statistics, charts and the report.

   The agent never parses logs or computes numbers by hand.
3. **The agent spends tokens on judgement only:**
   - choosing what to run,
   - reading a compact summary,
   - deciding what to investigate next,
   - explaining findings.

   The skill (`.claude/skills/m0-bench/`) tells the agent what the harness is, which
   commands exist, and how to read their output. It contains no logic.
4. **Python by default. Rust only where the engine requires it.** That's one small
   binary, `dfrunner`, because DataFusion and datafusion-distributed are Rust
   libraries. Trino needs no custom code: official image, config templates, HTTP client.
5. **Same measurement path for every engine.** Both engines run as long-lived servers.
   The same Python client measures wall time, and the same cgroup sampler collects
   CPU and memory.

## Components

```text
                     ┌──────────────── m0 (Python CLI) ────────────────┐
 plans/*.yaml ──────▶│ plan expander → scheduler → engine adapters     │──▶ results/<run-id>/
 queries/**          │ metrics sampler · classifier · differ · stats   │      runs.parquet, *.jsonl,
 configs/**          │ summarize · compare · report                    │      logs/, plans/, env.json
                     └──────────┬───────────────────────┬──────────────┘
                                │ HTTP (Trino REST)     │ HTTP + Arrow IPC
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
| Engine adapters | Python | `trino`: the `trino` client package over HTTP, query stats from `/v1/query/{id}`. `df` / `dfd`: HTTP to `dfrunner`, results returned as Arrow IPC. One interface: `start`, `health`, `execute`, `cancel`, `stats`, `stop`. |
| `dfrunner` | Rust | Adapted from datafusion-distributed's `benchmarks/remote-worker` (~400 lines). Modes: `single` (plain DataFusion) and `node` (worker plus HTTP coordinator endpoint). Adds: Iceberg catalog registration (REST or Glue via iceberg-rust), memory-pool limit and peak tracking, task-metrics export for A5, structured errors. Target size: under 1,000 lines. |
| Environment | Bash + Docker Compose (local), Bash + AWS CLI (AWS) | `infra/local/compose.yaml`, `infra/aws/*.sh`, wrapped by `m0 env …` |
| Data generator | SQL + Python | `datagen/*.sql`, run through Trino: CTAS from the `tpch`/`tpcds` connectors, then the `etl.*` derived tables |
| Metrics sampler | Python | Reads cgroup v2 files of each engine container: `cpu.stat`, `memory.peak` (reset per query), `memory.events`. Falls back to 10 Hz sampling. |
| Classifier | Python | Applies the outcome rules from methodology section 7. Error-pattern table in `m0/classify_rules.yaml`, unit-tested. |
| Differ | Python + pyarrow | Normalises results and compares them to the reference (methodology section 8). Stores fingerprints, and diffs only when they differ. |
| Stats and report | Python + matplotlib | Medians, geomeans, bootstrap CIs, charts, Markdown report from `docs/report-template.md` |
| Skill | Markdown | `.claude/skills/m0-bench/SKILL.md` plus runbooks; explains the harness and how to analyse results |

## The `m0` CLI: an interface designed for the agent

Rules every command follows:
- **Short stdout.** A summary of at most ~40 lines by default. Details go to files
  under `results/<run-id>/`, and their paths are printed.
- **`--json`** gives the same summary in machine-readable form.
- **Exit codes** mean something: `0` ok, `1` harness error, `2` prerequisite
  missing, `3` run completed with failed queries (expected in this benchmark),
  `4` budget or TTL guard refused.
- **Idempotent and resumable.** `m0 run` skips cells already recorded for the run
  ID. `--resume` continues after an interruption.
- **No interactive prompts,** except an explicit `--yes` gate on billable actions.

| Command | Does |
|---|---|
| `m0 doctor` | Checks tools, versions, Docker, cgroup v2, WSL settings, disk, AWS credentials (Phase C) |
| `m0 env up/down/status [--target local\|aws]` | Starts and stops the stack. `down` on AWS also verifies that nothing tagged is left running. |
| `m0 data gen --suite tpch\|tpcds\|etl --sf N` | Generates and validates the tables (row counts per table against expected values) |
| `m0 run <plan.yaml> [--engine E] [--query Q] [--resume]` | Executes the plan matrix or a single cell |
| `m0 status [<run-id>]` | Progress, and current or last failure in one line |
| `m0 summarize <run-id\|latest>` | Outcome counts per engine, suite totals and ratios, top 10 slowest relative queries, every non-`ok` cell with a one-line reason |
| `m0 compare <run-a> <run-b>` | Before/after comparison, e.g. a new upstream version or `dfd-jo` vs `dfd` |
| `m0 explain <run-id> <engine> <query>` | Saves plans and A5 breakdown; prints the top operators by time |
| `m0 logs <run-id> <engine> <query> [--grep P] [--tail N]` | Bounded log excerpts |
| `m0 triage <run-id>` | Groups non-`ok` cells and harness errors by signature (outcome, engine, normalised error, operator). Collects a diagnostic bundle per group: log excerpt, memory timeline, plan, node health, environment drift. Prints groups ranked by impact. |
| `m0 repro <cell-id> [--times N] [--diag]` | Re-runs one cell in isolation, optionally with extra diagnostics: `EXPLAIN ANALYZE`, verbose engine logging, 50 Hz memory sampling. Reports whether the outcome is deterministic or flaky. |
| `m0 bisect-mem <cell-id>` | Binary-searches the memory budget to find where a query flips from `ok` to `err_mem` or `killed`. Also reports peak RSS against the pool limit at each step. |
| `m0 diff <run-id> <query> [--rows N]` | For `ok_mismatch`: which normalisation rules applied, and the first N differing rows |
| `m0 sql <engine> "<query>" [--rows N]` | Ad-hoc probe against a running engine with bounded output, e.g. checking types or row counts during an investigation. Never recorded as a measurement. |
| `m0 finding new <group-id>` | Creates a findings record pre-filled with the triage evidence (see "Investigating failures") |
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

It contains **no logic**. Anything an agent would otherwise work out by hand (numbers,
classifications, diffs, log parsing) is a CLI command instead.

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
  - Never read raw result files; use `m0 summarize`, `m0 explain` and `m0 logs`.
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

**Decision tree (`investigate.md`)**

1. **Is the environment healthy?** Run `m0 doctor` and `m0 env status`, and check the
   drift flags in `m0 triage`. If not, mark the run's affected cells invalid, fix the
   environment, and re-run with `m0 run --resume`.
2. **Group failures.** `m0 triage` gives the groups. Work through them in impact order:
   one representative cell per group, not every cell.
3. **Is it deterministic?** Run `m0 repro --times 3`. A flaky outcome is reported as
   flaky, with its rate; the agent doesn't keep digging into noise.
4. **Follow the playbook for the outcome class:**

| Outcome | Playbook |
|---|---|
| `err_mem` / `killed` | `m0 explain` to find the operator holding memory, then `m0 repro --diag` for the memory timeline, then `m0 bisect-mem` for the failure threshold. Check whether RSS ran far above the pool limit (an accounting gap, which pushes failures to `killed`) and whether the operator is known not to spill (hash join, window). Link the matching upstream issue or epic. |
| `timeout` | Compare plans across engines and against `df` vs `dfd` (distribution problem or plan problem?). Check join order, and re-run under `dfd-jo` if relevant. Look for skew in the A5 breakdown. |
| `unresponsive` | Node logs, Flight connection errors, restart history. Decide: did the node die, or did a query starve the others? |
| `ok_mismatch` | `m0 diff`. Rule out harness normalisation first (floats, CHAR padding, ties, NULLs). Then cross-check `df` against `dfd`, and probe with `m0 sql`. Classify as harness bug, engine bug or ambiguous query. |
| `err_unsupported` | Name the missing feature. Is a mechanical rewrite allowed under the rewrite rules? If so, add an override with its reason. If not, record it. Search upstream for an existing issue. |
| `err_other` | `m0 logs --grep`. Classify as environment (go back to step 1), engine bug, or configuration. |

5. **Record a finding.** `m0 finding new` writes a fixed-format record:
   - symptom;
   - commands run and the key numbers;
   - root cause, marked *confirmed* or *suspected*;
   - upstream link;
   - action (fix the harness, add a rewrite, report as a result, draft an upstream issue).

   Confirmed findings move from `results/` into the committed `findings/` directory.
   They feed the report and the Phase L pain-point list.

**Guardrails for investigations**

- **Configuration changes.** Any engine configuration change made during an
  investigation becomes a named engine variant with its own run ID. It never
  overwrites the baseline configuration, and it is reported as a variant.
- **Harness fixes.** Re-run every affected cell and note the fix in the finding.
- **Upstream issues and posts.** These are drafted only. Nothing is filed upstream or
  posted without the user's approval.
- **Ask the user first** when:
  - the fix would change a baseline engine configuration;
  - a fairness question comes up;
  - a suspected engine bug is ready to draft upstream;
  - a repro would cost money (Phase C).
- **Bounded effort.** If one failure group remains unexplained after the playbook,
  record it as *suspected* with the evidence so far and move on. Don't spend tokens
  without limit on a single group.

### Keeping token use low

- Only `SKILL.md` is always loaded. Runbooks load on demand.
- **Investigation ladder.** Each step has bounded output, and the agent stops climbing
  once the question is answered:
  1. `m0 summarize`
  2. `m0 compare`
  3. `m0 explain <query>`
  4. `m0 logs --grep … --tail N`
- **Findings.** `m0 summarize --findings` emits a fixed-format stub per non-`ok` cell
  and per large regression. The agent fills in only the interpretation.
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

  An agent following only the skill must take a clean checkout to a `m0 summarize` of
  the smoke run, investigate each deliberate failure, and produce a correct finding
  for it.

## Build order

Each step ships harness code, tests and the matching skill sections together.

| Step | Harness | Skill |
|---|---|---|
| 1 | Repo skeleton, `m0 doctor`, local `env up/down`, versions lockfile | `SKILL.md` core, `preflight.md`, `environment.md` |
| 2 | Data generation and validation (TPC-H, TPC-DS, `etl.*`) | `data.md` |
| 3 | Trino adapter, `dfrunner` (`single` mode), cgroup sampler, classifier, smoke plan | `run.md`, first `troubleshooting.md` entries |
| 4 | Differ, stats, `summarize`, `compare`, `explain`, `logs` | `analyse.md` |
| 4b | `triage`, `repro`, `bisect-mem`, `diff`, `sql`, `finding`; deliberate-failure cells in the smoke plan | `investigate.md`, `troubleshooting.md` |
| 5 | `dfrunner` `node` mode (`dfd`), A3 setup, B2 memory levels, B3 concurrency | `run.md` and `troubleshooting.md` updates |
| 6 | `report` | `report.md` |
| 7 (Phase C) | `infra/aws`, `m0 aws …` guardrails | `aws-cost.md`, AWS troubleshooting |

## Repository layout

```text
m0-bench/
├── README.md  LICENSE  NOTICE
├── pyproject.toml  uv.lock          # Python deps: trino, pyarrow, pyyaml, httpx, matplotlib
├── versions.lock.yaml               # Trino image digest, DataFusion/datafusion-distributed/iceberg-rust revs, Lakekeeper, SeaweedFS, JDK
├── m0/                              # Python package: cli, plan, engines/, metrics/, classify, diff, stats, report
├── dfrunner/                        # Rust crate: Cargo.toml, Cargo.lock, src/
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

## Results schema

`results/<run-id>/runs.parquet` has one row per executed cell:

`run_id, cell_id, phase, engine, engine_variant, suite, query_id, iteration, cold,
mem_level, concurrency, client_id, started_at, wall_ms, engine_elapsed_ms, cpu_s,
peak_mem_bytes, mem_limit_bytes, pool_limit_bytes, outcome, error_class, error_line,
rows, result_hash, diff_status, nodes, instance_type, spot_usd_per_h`

`env.json` holds the environment manifest: git SHA, versions lockfile, config hashes,
host CPU/RAM/kernel, WSL config or EC2 instance metadata, start/end times.

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
