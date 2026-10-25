# Troubleshooting

Symptom → how to confirm → fix. Rule out the environment first (`m0 doctor`,
`m0 env status`).

## Runs

| Symptom | Confirm | Cause and fix |
|---|---|---|
| exit code 2, `objstore is not running` / `catalog is not running` | `m0 env status` | Stack down. `m0 env up --engine trino` (or `--engine df`). |
| exit code 2, `cannot list namespaces from the Iceberg catalog` | `curl -s "http://127.0.0.1:8181/catalog/v1/config?warehouse=m0"` | Catalog down or the warehouse missing; `m0 env up`. |
| exit code 1, `dfrunner not ready after 180s` | `docker compose -p m0 logs df --tail 50` | dfrunner can't reach the catalog or its config is wrong; see [environment](environment.md). |
| exit code 2, `missing namespaces` | `m0 data status --sf 1` | Data not generated for the plan's SF. `m0 data gen --suite <s> --sf <N>`. |
| exit code 4, `already exists; add --resume` | `m0 status <run-id>` | Continue with `--resume`, or pick a new `--run-id`. |
| exit code 4, `plan … changed since run … started` | `diff plans/<p>.yaml results/<run-id>/plan.yaml` | Never edit a plan under a running ID; start a new run ID. |
| exit code 4, `in progress (pid N)` | `ps -p N` | Another `m0 run` is using it. A stale `run.lock` with a dead pid is ignored automatically. |
| exit code 1 with a traceback | `tail results/<run-id>/run.log` | Harness bug. Recorded cells are kept; fix, then `--resume`. |

## Cell outcomes

| Symptom | Confirm | Cause and fix |
|---|---|---|
| `err_mem` with `ResourcesExhausted` (df) | `cells/<id>/engine_stats.json`: `error.message` names the top memory consumers; `pool.peak` vs `pool.limit` | Clean DataFusion memory error at the 4 GiB pool. Spilling is disabled on purpose (`dfrunner.disk_manager=disabled`), so sorts, joins and aggregations that would spill fail here. A result, not a fault. |
| `killed` on df with `pool.peak` well below 4 GiB | `peak_mem_bytes` vs `engine_peak_mem_bytes` in `runs.parquet`; `cells/<id>/memory.csv` | Memory DataFusion doesn't account in its pool (allocations outside reservations, Arrow buffers, result batches). An accounting gap is a finding: record RSS vs pool. |
| df `timeout`, then `INFO restarted df after timeout (engine still busy after cancel)` | `curl -s 127.0.0.1:9000/status` (`pool.reserved` > 0, `running` empty, CPU busy in `docker stats`) | The cancel aborted the query task, but an operator was inside a long CPU loop with no await point and kept running. The runner restarts the engine so later cells aren't measured on a busy engine. |
| df `err_unsupported`: `Plan: Invalid function '<f>'` | the function in `cells/<id>/query.sql` | Trino function DataFusion lacks. If a DataFusion function has the same semantics, add an override under `queries/overrides/datafusion/` with a `datafusion_override` reason in the manifest; otherwise it stays `err_unsupported`. |
| df `err_other` `IcebergError(...)` / `Failed to load manifest list` / `io operation timeout reached` | `docker compose -p m0 ps --all` (objstore exit 137?), `docker compose -p m0 logs objstore --tail 50 \| grep -E "not found\|no such host"` | Object store trouble, not DataFusion: SeaweedFS OOM-killed or its volume server dropped by its master. `docker compose -p m0 restart objstore` (data kept); the cells are invalid and must be re-run. |
| df `err_other` `table '...' not found` | `curl -s 127.0.0.1:9000/status` (namespaces), `m0 data status` | Data missing, or a typo in the query |
| `err_mem` with `EXCEEDED_LOCAL_MEMORY_LIMIT` | `cells/<id>/engine_stats.json` (`failureInfo.message` lists the top memory consumers) | Clean Trino memory error at `query.max-memory-per-node` (4 GiB). A result, not a fault. |
| `killed`: error line starts `container exit status 137` or `oom_kill +N` | `docker inspect m0-trino-1 --format '{{.State.OOMKilled}} {{.State.ExitCode}}'`, `dmesg \| grep -i oom` (may need the user), `cells/<id>/memory.csv` | The kernel killed the engine at the container limit, or someone ran `docker kill`. The runner restarts the engine automatically (`INFO restarted …`). |
| `killed` with `error_class` `memory_self_exit` (exit status 3) | `docker compose -p m0 logs trino --tail 100 \| grep -i OutOfMemoryError` | Trino's JVM hit a heap OutOfMemoryError and exited (`-XX:+ExitOnOutOfMemoryError`): untracked memory outgrew the 2 GiB heap headroom. Restarted automatically. |
| `timeout` | `error_line` is `USER_CANCELED` after `timeout_s` | The query ran longer than the plan's timeout and was cancelled. |
| `err_other`, `unrecognised = true` | `error_line`, `engine_stats.json` | No rule in `m0/classify_rules.yaml` matches. Understand it; if it's a known class, add a rule plus a real error string in `tests/fixtures/trino_errors.yaml`. |
| `err_other` `TABLE_NOT_FOUND` | `m0 data status` | Data missing or dropped. |
| `err_other`, `unrecognised = true` on df | `error_line` (`<Variant>: <message>`), `engine_stats.json` `error` | No df rule matches. Add one to `m0/classify_rules.yaml` with the real string in `tests/fixtures/df_errors.yaml`. |
| `cpu_s` or `peak_mem_bytes` empty | `ls /sys/fs/cgroup/system.slice/ \| grep docker` | The cgroup path wasn't found (not the systemd cgroup driver?). `m0 doctor` shows the driver. |

## Environment

| Symptom | Confirm | Cause and fix |
|---|---|---|
| Cold runs no slower than hot | `grep "page cache dropped" results/<run-id>/run.log` | The helper failed or was skipped. Test: `docker run --rm --privileged <cache_drop image from versions.lock.yaml> sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches'` and compare `free -m` before and after. |
| `objstore` close to its 1 GiB limit | `objstore_peak_mem_bytes` in `runs.parquet`; `grep -E '^(anon\|file) ' /sys/fs/cgroup/system.slice/docker-$(docker inspect m0-objstore-1 --format '{{.Id}}').scope/memory.stat` | A large `file` share is page cache, reclaimable. Growing `anon` is SeaweedFS itself: watch for an exit 137 in `docker compose -p m0 ps --all`. |
| Trino restart takes much longer than ~15 s | `docker compose -p m0 logs trino --tail 50` | Config error or a slow catalog. See [environment](environment.md). |
| `objstore` OOM-killed (exit 137) during df scans | `docker inspect m0-objstore-1 --format '{{.State.OOMKilled}}'` | SeaweedFS read buffers. They are capped (`-s3.readerCacheSizeMB=256 -volume.concurrentDownloadLimitMB=256` in `infra/local/compose.yaml`); if it recurs, check that the running container has these flags (`docker inspect m0-objstore-1 --format '{{.Config.Cmd}}'`) and recreate it with `m0 env up`. |
| Hot-run times noisy | `docker stats --no-stream`, `free -m` | Something else is using the host; the engine shares ~11.7 GiB with the infra. |
