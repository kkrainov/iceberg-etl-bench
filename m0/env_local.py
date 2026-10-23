"""`m0 env` for Phase L: the local Docker Compose stack.

Infra (object store, catalog) always runs; an engine is added with `--engine`.
Only one engine runs at a time (methodology section 5).
"""

import json
import os
import subprocess
import time
import urllib.error
import urllib.request

from m0 import versions
from m0.output import FAIL, OK, WARN, Check
from m0.paths import LOCAL_COMPOSE

ENGINES = ("trino",)
INFRA_SERVICES = ("objstore", "catalog-db", "catalog-migrate", "catalog")

BUCKET = "m0-bench"
WAREHOUSE = "m0"
CATALOG_URL = "http://127.0.0.1:8181"
DEFAULT_PROJECT = "00000000-0000-0000-0000-000000000000"

WAREHOUSE_SPEC = {
    "warehouse-name": WAREHOUSE,
    "project-id": DEFAULT_PROJECT,
    "storage-profile": {
        "type": "s3",
        "bucket": BUCKET,
        "key-prefix": "warehouse",
        "endpoint": "http://objstore:8333",
        "region": "us-east-1",
        "path-style-access": True,
        "flavor": "s3-compat",
        # Client-managed access: engines use their own static credentials.
        "sts-enabled": False,
        "remote-signing-enabled": False,
    },
    "storage-credential": {
        "type": "s3",
        "credential-type": "access-key",
        "access-key-id": "m0local",
        "secret-access-key": "m0localsecret",
    },
}


class EnvError(Exception):
    pass


def compose(*args: str, timeout: float = 600, check: bool = True) -> subprocess.CompletedProcess:
    env = {**os.environ, **versions.compose_env(versions.load())}
    cmd = ["docker", "compose", "-f", str(LOCAL_COMPOSE), "--profile", "trino", *args]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    if check and res.returncode != 0:
        raise EnvError(f"docker compose {' '.join(args)} failed:\n{res.stderr.strip()[-2000:]}")
    return res


def http_json(method: str, path: str, body: dict | None = None, timeout: float = 30) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(CATALOG_URL + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"raw": raw.decode(errors="replace")[:500]}


def weed_shell(command: str) -> str:
    res = compose("exec", "-T", "objstore", "sh", "-c", f"echo '{command}' | weed shell", timeout=60)
    return res.stdout


def buckets() -> set[str]:
    # `s3.bucket.list` prints one "  <name>\tsize:..." line per bucket.
    return {line.split()[0] for line in weed_shell("s3.bucket.list").splitlines() if "\t" in line}


def ensure_bucket() -> str:
    if BUCKET in buckets():
        return f"{BUCKET} exists"
    weed_shell(f"s3.bucket.create -name {BUCKET}")
    if BUCKET not in buckets():
        raise EnvError(f"could not create bucket {BUCKET!r}")
    return f"{BUCKET} created"


def ensure_catalog() -> str:
    status, info = http_json("GET", "/management/v1/info")
    if status != 200:
        raise EnvError(f"catalog /management/v1/info returned {status}: {info}")
    actions = []
    if not info.get("bootstrapped"):
        status, body = http_json("POST", "/management/v1/bootstrap", {"accept-terms-of-use": True})
        if status >= 300:
            raise EnvError(f"catalog bootstrap failed ({status}): {body}")
        actions.append("bootstrapped")
    if WAREHOUSE not in list_warehouses():
        status, body = http_json("POST", "/management/v1/warehouse", WAREHOUSE_SPEC, timeout=60)
        if status >= 300:
            raise EnvError(f"warehouse creation failed ({status}): {body}")
        actions.append(f"warehouse {WAREHOUSE!r} created")
    return ", ".join(actions) or "ready"


def list_warehouses() -> list[str]:
    status, body = http_json("GET", f"/management/v1/warehouse?project-id={DEFAULT_PROJECT}")
    if status != 200:
        return []
    return [w.get("name") for w in body.get("warehouses", [])]


def trino_smoke(timeout_s: float = 180) -> str:
    """End-to-end check: Trino -> REST catalog -> object store, write and read back."""
    import trino  # imported lazily so `m0 doctor` works before deps are synced

    deadline = time.monotonic() + timeout_s
    last_error = None
    while time.monotonic() < deadline:
        try:
            conn = trino.dbapi.connect(host="127.0.0.1", port=8080, user="m0", catalog="iceberg")
            cur = conn.cursor()
            for sql in (
                "CREATE SCHEMA IF NOT EXISTS iceberg.m0_smoke",
                "CREATE OR REPLACE TABLE iceberg.m0_smoke.ping AS SELECT 1 AS x",
                "SELECT count(*) FROM iceberg.m0_smoke.ping",
            ):
                cur.execute(sql)
                rows = cur.fetchall()
            cur.execute("DROP TABLE iceberg.m0_smoke.ping")
            cur.fetchall()
            if rows != [[1]]:
                raise EnvError(f"unexpected smoke result {rows}")
            return "write/read through catalog ok"
        except EnvError:
            raise
        except Exception as e:  # server still starting, or a real config error
            last_error = e
            time.sleep(3)
    raise EnvError(f"Trino smoke test failed: {last_error}")


def services() -> list[dict]:
    res = compose("ps", "--all", "--format", "json", check=False)
    out = []
    for line in res.stdout.splitlines():
        line = line.strip()
        if line:
            item = json.loads(line)
            out.extend(item if isinstance(item, list) else [item])
    return out


def up(engine: str | None, engine_mem: str, engine_cpus: str) -> list[Check]:
    os.environ["M0_ENGINE_MEM"], os.environ["M0_ENGINE_CPUS"] = engine_mem, engine_cpus
    if engine is None:
        # Bring infra up; stop any engine so only one engine ever runs.
        compose("up", "-d", "--wait", "objstore", "catalog")
        compose("stop", *ENGINES)
    else:
        others = [e for e in ENGINES if e != engine]
        if others:
            compose("stop", *others)
        # Always recreate: picks up config edits and guarantees a cold engine.
        compose("up", "-d", "--wait", "--force-recreate", engine)
    checks = [Check("bucket", OK, ensure_bucket()), Check("catalog", OK, ensure_catalog())]
    if engine == "trino":
        checks.append(Check("trino", OK, trino_smoke()))
    return checks + status_checks()


def down(purge: bool) -> None:
    compose("down", *(["--volumes"] if purge else []))


def status_checks() -> list[Check]:
    checks = []
    by_service = {s["Service"]: s for s in services()}
    for name in (*INFRA_SERVICES, *ENGINES):
        s = by_service.get(name)
        if s is None:
            status = FAIL if name in INFRA_SERVICES else OK
            checks.append(Check(f"service.{name}", status, "not created" if status == FAIL else "not running"))
            continue
        state, health, code = s.get("State"), s.get("Health") or "-", s.get("ExitCode")
        if name == "catalog-migrate":
            ok = state == "exited" and code == 0
            checks.append(Check(f"service.{name}", OK if ok else FAIL, f"{state} (exit {code})"))
        elif name in ENGINES and state != "running":
            checks.append(Check(f"service.{name}", OK, state))
        else:
            ok = state == "running" and health in ("healthy", "-")
            checks.append(Check(f"service.{name}", OK if ok else FAIL, f"{state}, {health}"))
    if all(c.status == OK for c in checks if c.name in ("service.catalog", "service.objstore")):
        whs = list_warehouses()
        checks.append(Check("warehouse", OK if WAREHOUSE in whs else WARN,
                            "present" if WAREHOUSE in whs else "missing; run `m0 env up`"))
    return checks
