# Troubleshooting

Symptom → how to confirm → fix. Rule out the environment first (`m0 doctor`,
`m0 env status`).

## Runs

| Symptom | Confirm | Cause and fix |
|---|---|---|
| exit code 2, `objstore is not running` / `catalog is not running` | `m0 env status` | Stack down. `m0 env up --engine trino`. |
| exit code 2, `missing namespaces` | `m0 data status --sf 1` | Data not generated for the plan's SF. `m0 data gen --suite <s> --sf <N>`. |
| exit code 4, `already exists; add --resume` | `m0 status <run-id>` | Continue with `--resume`, or pick a new `--run-id`. |
| exit code 4, `plan … changed since run … started` | `diff plans/<p>.yaml results/<run-id>/plan.yaml` | Never edit a plan under a running ID; start a new run ID. |
| exit code 4, `in progress (pid N)` | `ps -p N` | Another `m0 run` is using it. A stale `run.lock` with a dead pid is ignored automatically. |
| exit code 1 with a traceback | `tail results/<run-id>/run.log` | Harness bug. Recorded cells are kept; fix, then `--resume`. |

## Cell outcomes

| Symptom | Confirm | Cause and fix |
|---|---|---|
| `err_mem` with `EXCEEDED_LOCAL_MEMORY_LIMIT` | `cells/<id>/engine_stats.json` (`failureInfo.message` lists the top memory consumers) | Clean Trino memory error at `query.max-memory-per-node` (4 GiB). A result, not a fault. |
| `killed`: error line starts `container exit status 137` or `oom_kill +N` | `docker inspect m0-trino-1 --format '{{.State.OOMKilled}} {{.State.ExitCode}}'`, `dmesg \| grep -i oom` (may need the user), `cells/<id>/memory.csv` | The kernel killed the engine at the container limit, or someone ran `docker kill`. The runner restarts the engine automatically (`INFO restarted …`). |
| `killed` with `error_class` `memory_self_exit` (exit status 3) | `docker compose -p m0 logs trino --tail 100 \| grep -i OutOfMemoryError` | Trino's JVM hit a heap OutOfMemoryError and exited (`-XX:+ExitOnOutOfMemoryError`): untracked memory outgrew the 2 GiB heap headroom. Restarted automatically. |
| `timeout` | `error_line` is `USER_CANCELED` after `timeout_s` | The query ran longer than the plan's timeout and was cancelled. |
| `err_other`, `unrecognised = true` | `error_line`, `engine_stats.json` | No rule in `m0/classify_rules.yaml` matches. Understand it; if it's a known class, add a rule plus a real error string in `tests/fixtures/trino_errors.yaml`. |
| `err_other` `TABLE_NOT_FOUND` | `m0 data status` | Data missing or dropped. |
| `cpu_s` or `peak_mem_bytes` empty | `ls /sys/fs/cgroup/system.slice/ \| grep docker` | The cgroup path wasn't found (not the systemd cgroup driver?). `m0 doctor` shows the driver. |

## Environment

| Symptom | Confirm | Cause and fix |
|---|---|---|
| Cold runs no slower than hot | `grep "page cache dropped" results/<run-id>/run.log` | The helper failed or was skipped. Test: `docker run --rm --privileged <cache_drop image from versions.lock.yaml> sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches'` and compare `free -m` before and after. |
| `objstore` close to its 1 GiB limit | `objstore_peak_mem_bytes` in `runs.parquet`; `grep -E '^(anon\|file) ' /sys/fs/cgroup/system.slice/docker-$(docker inspect m0-objstore-1 --format '{{.Id}}').scope/memory.stat` | A large `file` share is page cache, reclaimable. Growing `anon` is SeaweedFS itself: watch for an exit 137 in `docker compose -p m0 ps --all`. |
| Trino restart takes much longer than ~15 s | `docker compose -p m0 logs trino --tail 50` | Config error or a slow catalog. See [environment](environment.md). |
| Hot-run times noisy | `docker stats --no-stream`, `free -m` | Something else is using the host; the engine shares ~11.7 GiB with the infra. |
