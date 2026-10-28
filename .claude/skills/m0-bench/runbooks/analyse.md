# Analysing a run

Start from `m0 summarize`. Open files only for the one query you are looking at, and
query them with a short script rather than printing them.

## Commands

| Command | Use |
|---|---|
| `m0 summarize` | The latest run in at most 40 lines (`m0 summarize <run-id>` for another). Exit code 3 if any cell is not `ok` or an answer differs (data, not an error). |
| `m0 summarize <run-id> --json` | Everything behind the summary: every diff, per-query stats (samples, median, min/max, cold, CV), suite totals with CIs, every failure group |
| `m0 compare <run-a> <run-b>` | B against A per engine: totals and geomean of B/A times over queries both completed, queries that moved beyond noise, outcome changes, changed images and config files. `latest` works for either. |
| `m0 explain <run-id> <engine> <query>` | Re-runs one query on the live engine with the run's scale factor and SQL; saves the plan with metrics; prints the top operators. Needs that engine up (exit code 2 otherwise). `--top N`, `--timeout S`. |
| `m0 logs <run-id> <engine> <query>` | The engine container's log lines during that query's cells. `--grep P` (regex, case-insensitive), `--tail N` (default 40). |

`<query>` is `suite/id` (`tpcds/q14a`) or an id only one suite has (`q14a`; `q01`
is ambiguous).

## Reading `m0 summarize`

1. **Header:** plan, scale factor, rounds x hot iterations, cells, total query time.
2. **Outcomes per engine** (cells). `ok_mismatch` is derived: a completed cell whose
   engine x query answer differs from Trino's. `cells.jsonl` still says `ok`; results
   are never rewritten.
3. **`<engine> vs trino answers`:** one diff per engine x query with an answer.
   `match`, `mismatch`, `unverified` (truncated unordered answer), `no_reference`
   (Trino has no answer). "matches using" counts the normalisations that were needed:

   | Rule | Meaning |
   |---|---|
   | `decimal_scale` | same value, other scale (`13.150000` vs `13.15`) |
   | `decimal_round` | the engine's longer decimal rounded half up to Trino's scale (DataFusion `avg` has scale 6) |
   | `decimal_lower_scale` | Trino's value rounded to the engine's lower scale (DataFusion decimal division keeps scale 8) |
   | `decimal_ulp` | one unit in the last place apart: Trino rounds a division half up, DataFusion truncates |
   | `float_tolerance` | floats within 1e-9 absolute or 1e-6 relative |
   | `char_strip` | CHAR trailing spaces |
   | `null_order` | rows only differ in where NULL sort keys go (Trino last, DataFusion first in DESC) |
   | `sort_keys_only` | a `tie_ambiguous` query, compared on its sort-key columns |
   | `truncated_prefix` | an ordered answer cut by `result_rows_cap`, compared on the kept prefix |

4. **Suite table:** totals of per-query hot medians over the queries every engine
   completed (`common`), `ratio total` = Trino total / engine total, geomean of
   per-query ratios with a bootstrap 95% CI, and how many queries each engine did not
   complete. Above 1 means the engine is faster.
5. **Slowest relative to trino:** smallest ratios first. `~` marks a CV above 10%
   on either side.
6. **not ok:** non-`ok` cells grouped by engine, outcome and error signature (numbers
   and quoted names masked), with the queries and the first error line. `(expected)`
   when the plan's `expect` matched.
7. **answer mismatches:** one line each with the first difference.

Lines are capped at 40; anything cut says `+N … in --json`.

## Typical follow-ups

- **One query's numbers:**
  `uv run m0 summarize <run-id> --json | python3 -c "import json,sys; s=json.load(sys.stdin); print([q for q in s['queries'] if q['query_id']=='q72'])"`
- **A mismatch:** read the two answers, not the whole file:
  `python3 -c "import json; [print(a['engine'], a['columns'], a['rows'][:5]) for a in map(json.loads, open('results/<run-id>/answers.jsonl')) if a['query_id']=='q61' and a['suite']=='tpcds']"`.
  Check the normalisation table above first (types, scales, NULLs, order), then the
  query's `sort_keys` / `tie_ambiguous` in `queries/manifest.yaml`.
- **A slow or failing query:** `m0 env up --engine df` (or `trino`), then
  `m0 explain <run-id> df tpcds/q72`. Read `diag/explain.<engine>.<suite>.<id>/plan.txt`
  with `grep`/`head`; `operators.json` has every operator.
- **What the engine logged:** `m0 logs <run-id> df tpcds/q72 --grep error`. dfrunner
  logs `Query <id> started/finished/failed` per query; grep the `engine_query_id`
  from `cells.jsonl`. Trino logs little per query; its query info is in
  `diag/` (with `diag` in the plan) or `m0 explain`.
- **Before/after an upgrade or config variant:** `m0 compare <old-run> <new-run>`.
- **Engine variants in a separate run (A6):** the variants run without `trino` and `df`;
  take those from the earlier run on the same machine and data:
  `m0 summarize <a6-run> --with <baseline-run>` (outcomes, geomean vs Trino, answer diffs
  vs Trino for every variant) and
  `m0 compare <baseline-run> <a6-run> --engines df:df-jo --engines df:df-jo-cs` (each
  variant against `df`: queries beyond noise, outcome changes). Different hot-run counts
  between the runs are fine; medians are over each run's own hot runs.

## Noise rules

- Trust suite totals and geomeans over single queries. At SF1 most queries take well
  under a second, so single-query ratios swing a lot.
- Ignore differences inside the CV flag (`~`) and inside the CI.
- `m0 compare` marks a query as moved only if |B/A - 1| > max(10%, 2 x CV).
- Re-run a cell (`m0 run <plan> --query <q> --engine <e> --run-id scratch-…`) before
  calling a result a finding.
- With `cold: per_block`, iteration 0 is a first run, not a cold one; compare cold
  times only from `per_query` plans.

## Things to know

- `m0 explain` is a diagnostic, never a measurement: it writes nothing to
  `cells.jsonl`. It warns when the engine's config files differ from the run's.
  Trino: `EXPLAIN ANALYZE` plus operator wall time per plan node. df: the query runs
  as in the benchmark; operators are ranked by `elapsed_compute` (the Iceberg scan has
  no timer, so scans look cheap).
- `m0 logs` only reaches the current engine container. The runner recreates the engine
  for every cold start, so older windows print `gone: container recreated at …` (exit
  code 2 when every window is gone). The last block of each engine in a run usually
  still has its logs.
- `no_reference` and Trino failures: the engine's own timing still counts, but the
  query is not in `common`, so it is outside the totals.
