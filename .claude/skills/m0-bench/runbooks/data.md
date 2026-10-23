# Data generation (Phase L)

## What gets generated

`m0 data gen --suite <s> --sf <N>` runs CTAS statements through Trino into the Iceberg
catalog `iceberg`, namespace `<suite>_sf<N>`:

| Suite | Tables | Source |
|---|---|---|
| `tpch` | the 8 TPC-H tables | Trino `tpch` connector, standard column names (`l_orderkey`…) and DECIMAL money columns (`configs/trino/local/catalog/tpch.properties`) |
| `tpcds` | the 24 TPC-DS tables (not `dbgen_version`) | Trino `tpcds` connector |
| `etl` | `store_sales_skewed`, `store_sales_dups` | `datagen/etl/*.sql` over `tpcds_sf<N>`; parameters in `datagen/etl/params.yaml` |

Every table: Iceberg v2, Parquet, zstd, unpartitioned, Trino's default file size.
No session properties are changed for generation. Order: `tpch` and `tpcds` in any
order; `etl` needs a valid `tpcds` at the same SF (otherwise exit code 2).

**etl tables** (deterministic: the keys are xxhash64 of the row's primary key with a
fixed seed, so regeneration gives the same rows; files and snapshot IDs differ):
- `store_sales_skewed`: `store_sales` plus `skew_customer_sk`, a power-law (Zipf-like,
  s = 1.2) customer key. Top key ≈ 14% of rows, top 10 ≈ 41%, top 100 ≈ 65% (SF1 and
  SF10). Used by J3 (skewed join) and W1.
- `store_sales_dups`: a hash-sampled 50% of `store_sales_skewed`, each row repeated
  1–3 times with `version` (1..n) and `updated_at`; unique key
  `(ss_ticket_number, ss_item_sk, version)`. About as many rows as `store_sales`. Used by
  W1 (`ROW_NUMBER()` dedup over `skew_customer_sk`).
- Fact-to-fact joins (J1, J2) use `store_sales`/`store_returns` and
  `lineitem`/`orders` directly.

## Time and size on the reference machine (Trino 8 GiB, 2026-10-24)

| Suite | SF1 time | SF1 size | SF10 time | SF10 size |
|---|---|---|---|---|
| `tpch` | 12 s | 209 MiB | 50 s | 2.1 GiB |
| `tpcds` | 46 s | 258 MiB | 170 s | 2.7 GiB |
| `etl` | 10 s | 210 MiB | ~100 s | 2.0 GiB |

Everything at SF10: about 5.5 minutes. Slowest single tables: `web_sales` (52 s),
`store_sales_dups` (53 s), `store_sales` (43 s). A re-run that skips everything
takes about 1 s per suite.

## Validation and regeneration

- `m0 data status` checks every table in `datagen/expected.yaml` from Iceberg metadata
  only: row count, no delete files, format v2, Parquet, zstd. Exit code 0 only if all
  requested tables validate; missing or wrong tables give exit code 2.
- `m0 data status --suite tpcds --sf 10` lists rows, size, file count and snapshot ID
  per table. A `snapshots` warning means a table changed since `m0 data gen` recorded
  it in `results/datagen/<ns>.json`.
- `m0 data gen` skips tables that validate, rebuilds invalid ones and creates missing
  ones, so an interrupted run resumes where it stopped. It stops at the first failed
  table (exit code 1).
- `m0 data gen --suite etl --sf 10 --force` drops and rebuilds every table of that
  suite and SF. Changing `datagen/etl/params.yaml` or the SQL requires this, plus new
  etl counts in `expected.yaml`.

Check the skew of the etl key (read-only):

```sql
WITH c AS (SELECT skew_customer_sk, count(*) n FROM iceberg.etl_sf10.store_sales_skewed GROUP BY 1)
SELECT max(n) * 1e0 / sum(n) AS top_key_share FROM c
```

## Data facts that matter for queries and result comparison

- **TPC-DS `CHAR(n)` columns become `varchar`** (Iceberg `string`) without error or
  cast, and lose their padding: `c_salutation` `'Dr.       '` is stored as `'Dr.'`.
  `varchar(n)` loses its length bound. Decimals, dates and integers map one to one.
- **`reason` has 75 rows at every SF.** The TPC-DS spec says 35 (SF1) and 45 (SF10);
  this is what Trino's `tpcds` connector generates. All other counts match the specs.
- `store_sales_dups.updated_at` is `timestamp(6)` (Iceberg `timestamp`, no zone);
  `version` is `bigint`.
- Parquet files: written by `parquet-mr-trino`, decimals as INT32/INT64, some strings
  `DELTA_LENGTH_BYTE_ARRAY`-encoded, ~128 MiB row groups, at most 8 files per table.

## Common failures

| Symptom | Check | Likely cause and fix |
|---|---|---|
| exit code 2, `Trino not reachable` | `m0 env status` | Start it: `m0 env up --engine trino` |
| exit code 2, `etl needs valid tpcds_sf<N>` | `m0 data status --suite tpcds --sf 10` | Generate `tpcds` at that SF first |
| exit code 2, disk | `df -h /var/lib/docker` | Needs about 2× the suite's size plus 5 GiB free |
| `HIVE_WRITER_CLOSE_ERROR` / S3 errors in a CTAS | `docker compose -p m0 ps --all` (objstore `Exited (137)`?) | SeaweedFS ran out of memory. Its limits are in `infra/local/compose.yaml`; `m0 env up --engine trino` restarts it. Re-run `m0 data gen`: it resumes. |
| `rows X, expected Y` on a fresh build | Is the SF or connector config different? | A connector config change (`configs/trino/local/catalog/`) changes the data; never edit `expected.yaml` to match without understanding why |
| `no expected count` | The etl SQL or params changed | Record the new counts in `expected.yaml` (exact, from the generator) |
