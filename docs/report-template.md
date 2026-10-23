# Report template

`m0 report <run-id>…` fills this template. Two kinds of content:

- **Generated**, written `{{like_this}}`: tables, numbers and charts produced by code
  from `runs.parquet` and `env.json`. Never edited by hand. If a number looks wrong,
  fix the code and regenerate.
- **Written**, marked `<!-- WRITTEN: … -->`: short interpretation by a human or agent.
  Every claim must point to a generated table, chart or finding.

The same template serves both phases. In a Phase L report every page title, table
caption and chart carries **"Exploratory, single workstation"**. Phase C reports link
the pre-registered methodology version.

## Presentation rules

- **No query disappears.** Every query from every suite appears in the tables. Failures
  show as their outcome class, never as gaps, and totals say how many queries they
  cover.
- **Ratios are Trino time ÷ DataFusion time.** Above 1 means DataFusion is faster.
  Ratio charts use a log scale centred on 1.
- **Losses are shown up front.** Each suite has a "Where DataFusion is slower or fails"
  table, generated, never trimmed.
- **Fixed engine colours** across all charts: `trino`, `df`, `dfd`, `dfd-jo`. Colours
  come from the palette in `m0/report/palette.py`, checked for colour-blind safety
  and readable in light and dark themes.
- **Outcomes use glyph and colour together**, so the charts still read in greyscale:

  | Glyph | Outcome | Meaning |
  |---|---|---|
  | `✓` | `ok` | Completed, result matches |
  | `≠` | `ok_mismatch` | Completed, result differs |
  | `M` | `err_mem` | Clean memory error |
  | `K` | `killed` | Killed by the OS |
  | `T` | `timeout` | Exceeded the timeout |
  | `U` | `unresponsive` | Node stopped responding |
  | `X` | `err_unsupported` | Unsupported feature |
  | `E` | `err_other` | Other error |
  | `·` | — | Not run (e.g. skipped after a node failure) |

- **Uncertainty is visible.** Medians come with min–max or a 95% confidence interval.
  Cells with high variance (coefficient of variation above 10%) are marked `~`.

---

# {{title}}

> **{{phase_banner}}**: "Exploratory, single workstation. Not pre-registered." or
> "Pre-registered methodology {{methodology_version}}".
> Runs: {{run_ids}} · Date: {{date}} · Harness commit: {{git_sha}}

## Summary

<!-- WRITTEN: at most 5 bullets. What we learned, including the bad news. Each bullet links to a section below. -->

{{key_numbers_table}}
<!-- Generated. One row per engine:
     A1/A2 total (common queries) · geomean ratio vs trino [95% CI] ·
     queries completed / total · B2 completion rate at 50% memory ·
     killed count · $/query (Phase C only) -->

## 1. What was compared

{{systems_table}}
<!-- Generated from env.json: engine, version/commit, mode, key non-default config,
     with a link to the full config. -->

{{hardware_table}}
<!-- Generated: host/instance types, nodes, vCPU, memory per engine node, storage, catalog, region. -->

{{data_table}}
<!-- Generated: suite, scale factor, tables, rows, bytes on storage, file count. -->

## 2. Headroom (Q1)

{{suite_summary_table}}
<!-- Generated, one block per suite (A1, A2, B1). Per engine:
     queries · ok · failures by class · total hot time over queries all engines completed ·
     cold total · CPU-seconds · peak memory (max node) · geomean ratio [CI] · $/query (Phase C). -->

![Per-query speed ratio]({{chart_ratio_per_query}})
<!-- Per suite: one bar per query, sorted by ratio, log scale centred on 1.
     Failed queries appear as outcome glyphs at the axis edge. -->

![Cold vs hot]({{chart_cold_hot}})

### Where DataFusion is slower or fails

{{losses_table}}
<!-- Generated: every query with ratio < 1 or a non-ok outcome on df/dfd, with the trino
     outcome next to it and a link to the related finding. -->

<!-- WRITTEN: 3–6 sentences. Patterns in the losses, with links to findings. -->

## 3. Distribution tax (Q2)

{{distribution_tax_table}}
<!-- Generated from A3: per engine, total on 1 large node vs N small nodes with the same
     vCPU and RAM; slowdown factor; ratio to trino in each shape. -->

![Same hardware, one node vs N nodes]({{chart_distribution_tax}})

![Scaling curve]({{chart_scaling}})
<!-- Phase C (A4): speedup vs workers (1/2/4/8) per engine, with an ideal-scaling line;
     a second panel shows efficiency per node. -->

<!-- WRITTEN: does DataFusion's single-node lead survive distribution? Where is it lost (link to section 5)? -->

## 4. Memory behaviour (Q3)

![Outcome matrix]({{chart_outcome_matrix}})
<!-- Grid: rows = B1 queries, columns = memory level (100/50/25%) × engine.
     Each cell = outcome glyph + colour. The most important picture in the report. -->

{{completion_by_memory_table}}
<!-- Generated: per engine and memory level: ok / err_mem / killed / timeout / unresponsive counts.
     Calls out the clean-error vs killed split explicitly. -->

![Peak memory vs limit]({{chart_rss_vs_pool}})
<!-- Scatter per cell: x = configured limit (pool or query.max-memory-per-node),
     y = peak RSS of the node. A diagonal marks RSS = limit; points far above it show accounting gaps. -->

{{memory_threshold_table}}
<!-- Generated from bisect-mem: per query and engine, the smallest budget that completes. -->

### Concurrency

{{concurrency_table}}
<!-- Generated from B3: per engine and client count: throughput, p50/p95 latency,
     failed queries, and "collateral" failures (queries that failed because another
     query exhausted memory or killed the node). -->

<!-- WRITTEN: how each engine fails under pressure, and whether one bad query takes others down. -->

## 5. Where the time goes (Q4)

![Time breakdown]({{chart_breakdown}})
<!-- Stacked bars for the 10 queries with the largest gap: scan / exchange / compute
     (dfd from task metrics; trino from stage stats). -->

{{optimizer_variant_table}}
<!-- Generated from A6: dfd vs dfd-jo per TPC-DS query and in total; how much of the
     gap to trino join ordering closes. -->

<!-- WRITTEN: top pain points ranked by time lost or failures, each with an upstream link. -->

## 6. Correctness

{{parity_table}}
<!-- Generated: per engine: results matched / tolerance-matched / mismatched / no reference. -->

{{mismatch_table}}
<!-- Generated: every mismatch with its classification (harness, engine bug, ambiguous) and finding link. -->

{{rewrites_table}}
<!-- Generated from queries/manifest.yaml: queries that needed a DataFusion rewrite, and why. -->

## 7. Findings

{{findings_index}}
<!-- Generated from findings/*.md: id, title, status (confirmed/suspected), outcome class, upstream link. -->

## 8. Context and prior evidence

<!-- WRITTEN, short and fixed per report version. Earlier evidence that motivated M0,
     cited as prior evidence only, each with its known weaknesses:
     - ClickBench single-node results (Trino timing includes CLI JVM startup; untuned;
       a single flat table).
     - datafusion-distributed README vs Trino (the authors' own benchmark; Trino coordinator
       limited to 1 CPU; only queries all engines completed are counted; no memory pressure).
     - Native-vs-JVM engine results (Gluten, Photon, Comet, StarRocks): typically 2–3×
       when distributed.
     Link the sources. -->

## 9. Limitations

Standard text (always included, from methodology section 11):

- This measures the **foundation** (DataFusion + datafusion-distributed), not Rino.
- **No fault-tolerant execution.** Trino runs with `retry-policy=NONE`;
  datafusion-distributed has no retries.
- **No writes or row-level DML.** datafusion-distributed has no distributed write
  path, and DataFusion cannot run Iceberg `DELETE` / `UPDATE` / `MERGE`.
- **Statistics as real users run them.** Trino uses the statistics its Iceberg writes
  collect. DataFusion uses manifest and footer statistics only.
- **Vendor numbers** from other projects are prior evidence only.
- **TPC-derived.** Not official TPC results and not comparable with published TPC results.

{{run_specific_limitations}}
<!-- Generated: Phase L caveats (one workstation, WSL2, x86_64, local object store, loopback
     network) or Phase C caveats (Spot prices at run time, region), plus invalidated cells
     that were re-run and any variance flags. -->

## 10. Reproduce

{{reproduce_block}}
<!-- Generated: exact commands (m0 env up, data gen, run <plan>), versions.lock.yaml,
     config directory, plan file, and the link to the raw results artifact. -->

## Appendix

- A. Per-query tables for every suite and engine: cold, hot median, min–max, CPU-s,
  peak memory, outcome. `{{appendix_per_query}}`
- B. Configuration differences from engine defaults, with the reason for each.
  `{{appendix_config_diff}}`
- C. Environment manifest. `{{appendix_env}}`
