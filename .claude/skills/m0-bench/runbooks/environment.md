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

**Storage access is client-managed.** Lakekeeper vends no credentials. Every engine
uses the same static local S3 credentials, which mirrors IAM-role access on AWS.

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
| Port already allocated | `m0 doctor` port checks | Another process holds 8080, 8181 or 8333 |
| Everything slow or flaky | `docker stats --no-stream`, `free -g` | WSL memory pressure: is a second engine or another stack running? |

Inspecting the catalog directly (read-only):

```bash
curl -s http://127.0.0.1:8181/management/v1/info
curl -s "http://127.0.0.1:8181/management/v1/warehouse?project-id=00000000-0000-0000-0000-000000000000"
```
