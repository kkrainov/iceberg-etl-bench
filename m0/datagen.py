"""`m0 data gen|status`: TPC-H, TPC-DS and derived etl tables in Iceberg, via Trino.

Every table is a CTAS into `iceberg.<suite>_sf<N>.<table>` (Iceberg v2, Parquet,
zstd, unpartitioned, Trino's default target file size). A table that exists and
validates is skipped, so re-running is a no-op and an interrupted run resumes.
Validation reads only Iceberg metadata ($properties, $snapshots).
"""

import json
import shutil
import string
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

import yaml

from m0.output import FAIL, OK, WARN, Check
from m0.paths import DATAGEN_DIR, RESULTS_DIR

SUITES = ("tpch", "tpcds", "etl")
CATALOG = "iceberg"
TRINO_HOST, TRINO_PORT, TRINO_USER = "127.0.0.1", 8080, "m0"
WITH = "format_version = 2, format = 'PARQUET', compression_codec = 'ZSTD'"
# Required table properties, as Iceberg reports them in "<t>$properties".
PROPERTIES = {"format-version": "2", "write.format.default": "PARQUET", "write.parquet.compression-codec": "zstd"}
GIB = 1024**3
DISK_RESERVE_GIB = 5
EXPECTED_FILE = DATAGEN_DIR / "expected.yaml"
ETL_DIR = DATAGEN_DIR / "etl"
MANIFEST_DIR = RESULTS_DIR / "datagen"


class DataError(Exception):
    pass


class Prerequisite(DataError):
    """Something required is missing (Trino down, source tables, disk)."""


@dataclass
class TableState:
    snapshot_id: int | None = None
    rows: int | None = None
    bytes: int | None = None
    files: int | None = None
    delete_files: int = 0
    properties: dict = field(default_factory=dict)


@dataclass
class TableResult:
    table: str
    action: str  # created | regenerated | skipped | missing | invalid | failed
    ok: bool
    rows: int | None = None
    expected: int | None = None
    bytes: int | None = None
    files: int | None = None
    snapshot_id: int | None = None
    seconds: float | None = None
    problems: list[str] = field(default_factory=list)


# ---------- expected values and SQL ----------

def load_expected(path=EXPECTED_FILE) -> dict:
    with open(path) as f:
        data = yaml.safe_load(f)
    rows = data.get("rows") or {}
    if set(rows) != set(SUITES):
        raise DataError(f"{path}: rows must have exactly the suites {SUITES}")
    return data


def scale_factors(expected: dict) -> list[int]:
    return sorted({sf for suite in expected["rows"].values() for sf in suite})


def namespace(suite: str, sf: int) -> str:
    return f"{suite}_sf{sf}"


def load_params() -> dict:
    with open(ETL_DIR / "params.yaml") as f:
        return yaml.safe_load(f)


def tables_for(expected: dict, suite: str, sf: int) -> dict[str, int | None]:
    """Expected rows per table, in generation order."""
    by_sf = expected["rows"][suite]
    if sf not in by_sf:
        raise Prerequisite(f"no expected row counts for {suite} SF{sf} in {EXPECTED_FILE.name}")
    tables = dict(by_sf[sf])
    if suite == "etl":
        order = load_params()["tables"]
        if set(order) != set(tables):
            raise DataError(f"etl tables in params.yaml {order} differ from expected.yaml {sorted(tables)}")
        tables = {t: tables[t] for t in order}
    return tables


def render_sql(suite: str, sf: int, table: str, params: dict | None = None) -> str:
    ns = namespace(suite, sf)
    target = f"{CATALOG}.{ns}.{table}"
    if suite in ("tpch", "tpcds"):
        return f"CREATE TABLE {target}\nWITH ({WITH})\nAS SELECT * FROM {suite}.sf{sf}.{table}"
    params = load_params() if params is None else params
    values = {k: v for k, v in params.items() if not isinstance(v, (list, dict))}
    values.update(target=target, target_ns=f"{CATALOG}.{ns}", src=f"{CATALOG}.{namespace('tpcds', sf)}", **{"with": WITH})
    template = string.Template((ETL_DIR / f"{table}.sql").read_text())
    return template.substitute(values)


# ---------- disk ----------

def disk_required_gib(expected: dict, suite: str, sf: int) -> float:
    return expected["disk_gib_per_sf"][suite] * sf * 2 + DISK_RESERVE_GIB


def free_disk_gib() -> tuple[str, float]:
    from m0.doctor import docker_info  # docker volumes live under Docker's root dir

    root = (docker_info() or {}).get("DockerRootDir") or "/"
    try:
        return root, shutil.disk_usage(root).free / GIB
    except OSError:
        return "/", shutil.disk_usage("/").free / GIB


# ---------- Trino access ----------

def connect():
    import trino

    return trino.dbapi.connect(host=TRINO_HOST, port=TRINO_PORT, user=TRINO_USER, catalog=CATALOG)


def query(cur, sql: str) -> list:
    cur.execute(sql)
    return cur.fetchall()


def require_trino():
    try:
        cur = connect().cursor()
        query(cur, "SELECT 1")
        return cur
    except Exception as e:
        raise Prerequisite(f"Trino not reachable at {TRINO_HOST}:{TRINO_PORT} ({type(e).__name__}); "
                           "start it with `m0 env up --engine trino`") from e


def existing_tables(cur, ns: str) -> set[str]:
    rows = query(cur, f"SELECT table_name FROM {CATALOG}.information_schema.tables WHERE table_schema = '{ns}'")
    return {r[0] for r in rows}


def table_state(cur, ns: str, table: str) -> TableState:
    props = dict(query(cur, f'SELECT key, value FROM {CATALOG}.{ns}."{table}$properties"'))
    state = TableState(properties=props)
    snap = props.get("current-snapshot-id")
    if snap in (None, "", "none"):
        return state
    state.snapshot_id = int(snap)
    rows = query(cur, f'SELECT summary FROM {CATALOG}.{ns}."{table}$snapshots" WHERE snapshot_id = {state.snapshot_id}')
    summary = rows[0][0] if rows else {}
    state.rows = int(summary.get("total-records", 0))
    state.files = int(summary.get("total-data-files", 0))
    state.bytes = int(summary.get("total-files-size", 0))
    state.delete_files = int(summary.get("total-delete-files", 0))
    return state


def validate(state: TableState, expected_rows: int | None) -> list[str]:
    problems = []
    if state.snapshot_id is None:
        problems.append("no snapshot")
    if expected_rows is None:
        problems.append(f"no expected count in datagen/expected.yaml (observed {state.rows})")
    elif state.rows != expected_rows:
        problems.append(f"rows {state.rows}, expected {expected_rows}")
    if state.delete_files:
        problems.append(f"{state.delete_files} delete files")
    for key, want in PROPERTIES.items():
        if str(state.properties.get(key, "")).lower() != want.lower():
            problems.append(f"{key}={state.properties.get(key)!r}, expected {want!r}")
    return problems


def result_from(table: str, action: str, state: TableState | None, expected: int | None,
                seconds: float | None = None) -> TableResult:
    if state is None:
        return TableResult(table, "missing", False, expected=expected, problems=["missing"])
    problems = validate(state, expected)
    return TableResult(table, action if not problems else "invalid", not problems, state.rows, expected,
                       state.bytes, state.files, state.snapshot_id, seconds, problems)


# ---------- commands ----------

def status(cur, suite: str, sf: int, expected: dict) -> list[TableResult]:
    ns = namespace(suite, sf)
    existing = existing_tables(cur, ns)
    out = []
    for table, exp in tables_for(expected, suite, sf).items():
        state = table_state(cur, ns, table) if table in existing else None
        out.append(result_from(table, "present", state, exp))
    return out


def plan(cur, suite: str, sf: int, expected: dict, force: bool) -> dict[str, tuple[TableState | None, bool]]:
    """Per table in generation order: (current state or None, needs (re)generation)."""
    ns = namespace(suite, sf)
    existing = existing_tables(cur, ns)
    out = {}
    for table, exp in tables_for(expected, suite, sf).items():
        state = table_state(cur, ns, table) if table in existing else None
        out[table] = (state, force or state is None or bool(validate(state, exp)))
    return out


def check_sources(cur, suite: str, sf: int, expected: dict) -> None:
    if suite != "etl":
        return
    bad = [r.table for r in status(cur, "tpcds", sf, expected) if r.table in ("store_sales", "customer") and not r.ok]
    if bad:
        raise Prerequisite(f"etl needs valid {namespace('tpcds', sf)}.{{{','.join(bad)}}}; "
                           f"run `m0 data gen --suite tpcds --sf {sf}` first")


def generate(cur, suite: str, sf: int, expected: dict, todo: dict, on_result=None) -> list[TableResult]:
    """Build the tables `plan` marked; validate the rest. Stops at the first failure."""
    ns = namespace(suite, sf)
    if any(build for _, build in todo.values()):
        query(cur, f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{ns}")
    results = []
    for table, exp in tables_for(expected, suite, sf).items():
        state, build = todo[table]
        if build:
            res = build_table(cur, suite, sf, table, exp, exists=state is not None)
        else:
            res = result_from(table, "skipped", state, exp)
        results.append(res)
        if on_result:
            on_result(res)
        if not res.ok:
            break
    return results


def build_table(cur, suite: str, sf: int, table: str, expected_rows: int | None, exists: bool) -> TableResult:
    ns = namespace(suite, sf)
    if exists:
        query(cur, f"DROP TABLE {CATALOG}.{ns}.{table}")
    start = time.monotonic()
    try:
        query(cur, render_sql(suite, sf, table))
    except Exception as e:
        first_line = str(e).splitlines()[0][:300] if str(e) else type(e).__name__
        return TableResult(table, "failed", False, expected=expected_rows,
                           seconds=round(time.monotonic() - start, 1), problems=[first_line])
    seconds = round(time.monotonic() - start, 1)
    return result_from(table, "regenerated" if exists else "created", table_state(cur, ns, table), expected_rows, seconds)


# ---------- manifest (results/datagen/<ns>.json) ----------

def manifest_path(suite: str, sf: int):
    return MANIFEST_DIR / f"{namespace(suite, sf)}.json"


def read_manifest(suite: str, sf: int) -> dict:
    path = manifest_path(suite, sf)
    return json.loads(path.read_text()) if path.is_file() else {}


def write_manifest(suite: str, sf: int, results: list[TableResult]) -> str:
    """Record what was generated; status compares current snapshots against it."""
    previous = read_manifest(suite, sf).get("tables", {})
    now = datetime.now(UTC).isoformat(timespec="seconds")
    tables = dict(previous)
    for r in results:
        if r.ok and (r.action != "skipped" or r.table not in previous):
            entry = asdict(r)
            entry["generated_at"] = now if r.action != "skipped" else None
            tables[r.table] = entry
    path = manifest_path(suite, sf)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"namespace": namespace(suite, sf), "updated_at": now, "tables": tables}, indent=2))
    return str(path)


def snapshot_drift(suite: str, sf: int, results: list[TableResult]) -> list[str]:
    recorded = read_manifest(suite, sf).get("tables", {})
    return [r.table for r in results
            if r.ok and r.table in recorded and recorded[r.table].get("snapshot_id") != r.snapshot_id]


# ---------- formatting ----------

def human_bytes(n: int | None) -> str:
    if n is None:
        return "-"
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n:.0f} {unit}" if unit in ("B", "KiB") else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


def to_check(ns: str, r: TableResult, snapshot: bool = False) -> Check:
    if not r.ok:
        return Check(f"{ns}.{r.table}", FAIL, f"{r.action}: {'; '.join(r.problems)}")
    extra = f", {r.seconds:.1f}s" if r.seconds is not None else ""
    if snapshot:
        extra += f", snapshot {r.snapshot_id}"
    detail = f"{r.action}: {r.rows:,} rows, {human_bytes(r.bytes)}, {r.files} files{extra}"
    return Check(f"{ns}.{r.table}", OK, detail)


def drift_check(ns: str, tables: list[str]) -> Check:
    return Check(f"{ns}.snapshots", WARN, f"changed since `m0 data gen` recorded them: {', '.join(tables)}")
