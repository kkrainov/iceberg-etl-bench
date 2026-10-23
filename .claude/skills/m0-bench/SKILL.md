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
| `m0 env status` | Health of every service, plus whether the warehouse exists |
| `m0 env down` | Stop the stack. Data is kept. |
| `m0 env down --purge --yes` | Stop the stack and delete all data (ask first) |

More commands (`data`, `run`, `summarize`, `triage`, …) arrive with later build steps.
If `m0 --help` doesn't list a command, it doesn't exist yet.

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
| Container logs | `docker compose -p m0 logs <service> --tail 200` (services: `objstore`, `catalog`, `catalog-db`, `catalog-migrate`, `trino`) |

## When something goes wrong

Investigate freely with read-only means: logs, `docker compose -p m0 ps`,
`docker stats`, cgroup files, `dmesg`, engine HTTP endpoints, and reading code. Rule
out the environment before suspecting an engine. Grep logs; don't read them whole.

- Runbooks: [preflight](runbooks/preflight.md), [environment](runbooks/environment.md)
- When you understand a new failure pattern, encode it: add a `doctor` check or a
  runbook entry, with a test where possible.
