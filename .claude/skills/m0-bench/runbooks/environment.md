# Local environment (Phase L)

## What `m0 env up` does

1. Starts `objstore` (SeaweedFS, S3 API on `127.0.0.1:8333`), `catalog-db`
   (Postgres), `catalog-migrate` (one-shot schema migration) and `catalog`
   (Lakekeeper REST catalog on `127.0.0.1:8181`), and waits for health checks.
2. Creates the bucket `m0-bench` if it's missing.
3. Bootstraps Lakekeeper and creates the warehouse `m0` if missing. Creating the
   warehouse validates that the catalog can write and read the bucket.
4. With `--engine trino`:
   - stops other engines;
   - recreates the Trino container (so it starts cold and picks up config edits) on
     `127.0.0.1:8080`, with an 8 GiB / 12 CPU limit by default (`--engine-mem`,
     `--engine-cpus`);
   - writes, reads and drops `iceberg.m0_smoke.ping`.

5. With `--engine df`:
   - stops Trino (only one engine runs at a time; the build also needs the memory);
   - builds the `m0-dfrunner:local` image from `dfrunner/` (`docker compose build df`):
     pinned Rust builder and slim runtime from `versions.lock.yaml`, BuildKit cache
     mounts for the cargo registry, git checkouts and `target/`. The first build takes
     about 10 minutes; later ones only recompile what changed. The check line shows the
     seconds taken;
   - recreates the `df` container on `127.0.0.1:9000` with the same 8 GiB / 12 CPU
     limits as Trino and swap disabled (`memswap_limit` = `mem_limit`);
   - waits for the health check (`dfrunner healthcheck` → `GET /health`), then reads
     `iceberg.tpch_sf1.region` through the catalog (read-only smoke test).

`m0 env up --engine trino` stops `df` again, and `m0 run` switches between engines by
itself (stop one, recreate the other).

**Storage access is client-managed.** Lakekeeper vends no credentials. Every engine
uses the same static local S3 credentials, which mirrors IAM-role access on AWS.

`dfrunner` gets them as `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`; endpoint, region
and path style come from `configs/datafusion/local/dfrunner.properties`, overridden by
what the catalog returns per table (`s3.endpoint: http://objstore:8333/`).

Data lives in Docker volumes `m0_objstore-data` and `m0_catalog-db-data`.
`m0 env down` keeps them; `m0 env down --purge --yes` deletes them.

## Common failures

| Symptom | Check | Likely cause and fix |
|---|---|---|
| `service "<x>" is not running` during `env up` | `docker compose -p m0 ps --all` | A container exited. Read the last log lines: `docker compose -p m0 logs <x> --tail 50`. |
| `catalog-migrate` not `exited (exit 0)` | `docker compose -p m0 logs catalog-migrate` | Postgres not ready or credentials changed. Purging and retrying fixes local state, but ask the user first. |
| `warehouse creation failed (4xx/5xx)` | The error body in the message | Lakekeeper couldn't validate storage: bucket missing, wrong endpoint or credentials in `m0/env_local.py` `WAREHOUSE_SPEC` |
| Trino unhealthy or smoke test fails | `docker compose -p m0 logs trino \| grep -E "ERROR\|Exception" \| head` | Bad property in `configs/trino/local/` (Trino refuses unknown or renamed properties at startup), catalog unreachable, or S3 credentials |
| Trino `has been replaced. Use … instead` in logs | `grep "has been replaced"` | Property renamed in this Trino version; update the config |
| Trino OOM-killed (container exit status 137 in `ps --all`) | `docker inspect m0-trino-1 --format '{{.State.OOMKilled}}'` | Container memory limit too small for the heap (80% of the limit) plus native memory |
| Port already allocated | `m0 doctor` port checks | Another process holds 8080, 8181, 8333 or 9000 |
| `docker compose build df failed` | the error tail in the message; `docker compose -p m0 build df --progress plain 2>&1 \| grep -E "^#.*(error\|warning)" \| head` | Rust compile error (fix `dfrunner/src`), a git dependency that can't be fetched (network), or `--locked` refusing a stale `Cargo.lock` (run `cargo update -p <crate> --precise <version>` on the host, never a bare `cargo update`) |
| Build killed or the host freezes during the build | `free -m`, `dmesg \| tail` (may need the user) | Linking DataFusion with thin LTO needs ~4 GiB. Stop engines first (`m0 env up` without `--engine` stops them); `--engine df` does this itself. |
| `df` unhealthy after `up` | `docker compose -p m0 logs df --tail 50` | Config error (the log names the key), or the catalog wasn't reachable within `catalog.startup_wait_s` |
| `dfrunner smoke test failed` | `curl -s 127.0.0.1:9000/status`, `docker compose -p m0 logs df \| grep -E "ERROR\|WARN" \| tail` | Catalog error (`namespaces` shows it) or S3: wrong endpoint, credentials or path style; compare with `configs/trino/local/catalog/iceberg.properties` |
| Everything slow or flaky | `docker stats --no-stream`, `free -g` | WSL memory pressure: is a second engine or another stack running? |

Inspecting the catalog directly (read-only):

```bash
curl -s http://127.0.0.1:8181/management/v1/info
curl -s "http://127.0.0.1:8181/management/v1/warehouse?project-id=00000000-0000-0000-0000-000000000000"
```
