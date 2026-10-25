# iceberg-etl-bench

An open, reproducible comparison of **Trino's default in-memory execution**
(`retry-policy=NONE`) against **Apache DataFusion + datafusion-distributed** on
Iceberg tables, for ETL-shaped workloads.

It is milestone M0 of [Rino](#about-rino), a planned open-source, Rust-native,
drop-in replacement for Trino. M0 answers one question before any engine design
starts: *does the DataFusion foundation have enough headroom over Trino to justify
the project, and where does it break?*

> **Status: Phase L (local, exploratory).** No pass/fail thresholds yet. They will
> be pre-registered and published before Phase C (AWS) runs.

## What is measured

| Question | How |
|---|---|
| **Headroom**: is the foundation faster and cheaper than Trino? | TPC-H and TPC-DS (every query; failures included) plus ETL-shaped operator queries |
| **Distribution tax**: does DataFusion's single-node advantage survive distribution? | Same hardware as 1 large node vs N small nodes, plus a scaling curve |
| **Memory behaviour**: what happens under memory pressure? | Out-of-memory matrix per operator class and concurrent clients, outcomes classified |
| **Where the time goes** | Per-query breakdown (scan / exchange / compute) and an optimizer variant |

Out of scope: Trino fault-tolerant execution (FTE), spill-to-disk strategies, writes,
row-level DML, and SQL dialect compatibility.

## Phases

| Phase | Where | Cost | Output |
|---|---|---|---|
| **L** (now) | One workstation, Linux (WSL2), Docker | $0 | Working harness, rough ratios, pain-point list, private go/stop decision |
| **C** | AWS `eu-north-1`, Graviton Spot, no Kubernetes | ~$100–200 (estimate) | Pre-registered, published results |

## Documents

- [docs/methodology.md](docs/methodology.md): questions, systems, data, suites, run protocol, metrics, correctness rules.
- [docs/framework-design.md](docs/framework-design.md): harness architecture, the `m0` CLI, and which upstream code is adapted.
- [docs/infrastructure.md](docs/infrastructure.md): local setup (Phase L) and AWS setup with cost guardrails (Phase C).
- [docs/report-template.md](docs/report-template.md): structure of the generated report and its presentation rules.
- [docs/implementation-plan.md](docs/implementation-plan.md): build steps with scope, acceptance criteria and status.
- `docs/announcement-plan.md`: *to be written*.
- `.claude/skills/m0-bench/`: how a Claude Code agent drives the harness. Built together
  with the harness; see [framework-design.md](docs/framework-design.md#claude-code-skill).

## Quick start

Requires Docker (cgroup v2, BuildKit) and [uv](https://docs.astral.sh/uv/). The
DataFusion runner (`dfrunner/`, Rust) is built inside Docker from pinned images, so no
host Rust toolchain is needed to run the benchmark.

```bash
uv run m0 doctor                       # check prerequisites and machine settings
uv run m0 env up --engine trino        # local stack: object store, Iceberg REST catalog, Trino
uv run m0 env up --engine df           # or plain DataFusion (builds dfrunner, ~7 min the first time)
uv run m0 env status
uv run m0 env down                     # stop; data is kept
```

Then `uv run m0 data gen --suite tpch --sf 1` (and `tpcds`, `etl`) and
`uv run m0 run plans/phase-l-smoke.yaml` (Trino and DataFusion in ABBA order).
A run writes plain JSON to `results/<run-id>/`: `run.json` (plan and environment),
`cells.jsonl` (one line per measured cell) and `answers.jsonl` (result rows for the
correctness check). `uv run m0 status` shows its progress, `uv run m0 results list`
lists runs, and `uv run m0 results prune --yes` deletes runs not named in
`results/keep.txt`.
`uv run m0 data verify --engine df` checks that DataFusion reads every table with the
same values as Trino.

`uv run m0 summarize` reports a finished run in at most 40 lines: outcomes, each
engine's answers compared with Trino's, suite totals and ratios with bootstrap
confidence intervals, the slowest queries and every failure. `m0 compare <run-a>
<run-b>` compares two runs, `m0 explain <run-id> <engine> <query>` saves one query's
plan with metrics, and `m0 logs <run-id> <engine> <query>` shows the engine's log lines
for that query. `plans/phase-l-a1a2-sf1.yaml` runs all 22 TPC-H and 99 TPC-DS queries.
Still planned: `m0 triage`, `repro`, `diff`, `sql`, `report` and the rest of the CLI in
[framework-design.md](docs/framework-design.md#the-m0-cli-an-interface-designed-for-the-agent).

## About Rino

Rino ("Trino without the T; R for Rust") is a working name for an open-source engine
built on Apache DataFusion. This benchmark repository is deliberately neutral: it
measures existing upstream projects (Trino, DataFusion, datafusion-distributed), not
Rino.

## Licence and attribution

Apache-2.0. Parts of the harness are adapted from Apache-2.0 projects; see `NOTICE`
and [docs/framework-design.md](docs/framework-design.md#upstream-code-reuse).

Benchmarks derived from TPC-H and TPC-DS are not official TPC results and are not
comparable with published TPC results.
