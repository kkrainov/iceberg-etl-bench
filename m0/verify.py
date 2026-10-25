"""`m0 data verify`: can an engine read every generated table, and does it read the
same values as Trino?

For every table in a namespace, an engine-neutral fingerprint is computed through
the engine under test with one aggregate query: the row count, plus per column the
non-null count, min and max, and the sum (numerics) or the sum of lengths (strings).
Column lists and types come from the Iceberg REST catalog, not from the engine.

Fingerprints are stored per engine in `results/verify/<engine>/<namespace>.json` and
compared with the reference engine's (Trino, which wrote the tables). A table whose
query fails is retried column by column, so each unreadable column is named.
"""

import datetime as dt
import json
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from m0 import catalog
from m0.paths import RESULTS_DIR
from m0.queries import CATALOG

VERIFY_DIR = RESULTS_DIR / "verify"
NUMERIC = ("int", "long", "float", "double", "decimal")
FLOATS = ("float", "double")
STRINGS = ("string",)
ORDERED = ("date", "time", "timestamp", "timestamptz", "timestamp_ns", "timestamptz_ns")
FLOAT_ABS, FLOAT_REL = 1e-9, 1e-6   # methodology section 8
QUERY_TIMEOUT_S = 600


def base_type(iceberg_type: str) -> str:
    return iceberg_type.split("(")[0].strip()


def stats_for(iceberg_type: str) -> tuple[str, ...]:
    t = base_type(iceberg_type)
    if t in NUMERIC:
        return ("nn", "min", "max", "sum")
    if t in STRINGS:
        return ("nn", "min", "max", "len")
    if t in ORDERED:
        return ("nn", "min", "max")
    if t == "boolean":
        return ("nn", "true")
    return ("nn",)


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def stat_expr(stat: str, col: str) -> str:
    c = quote(col)
    return {"nn": f"count({c})", "min": f"min({c})", "max": f"max({c})", "sum": f"sum({c})",
            "len": f"sum(length({c}))", "true": f"sum(CASE WHEN {c} THEN 1 ELSE 0 END)"}[stat]


def fingerprint_sql(namespace: str, table: str, columns: list[tuple[str, str]]) -> tuple[str, list]:
    """One aggregate query; returns (sql, [(column, stat)] for each output after the row count).

    The same text runs on Trino and DataFusion: only count/min/max/sum/length,
    double-quoted identifiers and positional aliases.
    """
    exprs, layout = ["count(*) AS a0"], []
    for col, typ in columns:
        for stat in stats_for(typ):
            layout.append((col, stat))
            exprs.append(f"{stat_expr(stat, col)} AS a{len(layout)}")
    sql = f"SELECT {', '.join(exprs)} FROM {CATALOG}.{quote(namespace)}.{quote(table)}"
    return sql, layout


def canonical(value):
    """JSON-safe, engine-neutral form of one aggregate value."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, float):
        return value
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


@dataclass
class TableFingerprint:
    table: str
    rows: int | None = None
    columns: dict = field(default_factory=dict)   # column -> {"type": t, stat: value, "error": msg}
    error: str | None = None
    seconds: float | None = None


def error_text(res) -> str:
    if res.timed_out:
        return "timeout"
    if res.error is not None:
        return f"{res.error.name or ''}: {res.error.message}".strip(": ")[:500]
    return (res.client_error or "unknown error")[:500]


def run_one(engine, sql: str):
    res = engine.execute(sql, timeout_s=QUERY_TIMEOUT_S)
    rows = res.as_rows() if res.completed else None
    return res, rows


def fingerprint_table(engine, namespace: str, table: str) -> TableFingerprint:
    columns = catalog.table_schema(namespace, table)
    fp = TableFingerprint(table, columns={c: {"type": t} for c, t in columns})
    sql, layout = fingerprint_sql(namespace, table, columns)
    start = time.monotonic()
    res, rows = run_one(engine, sql)
    if rows:
        values = rows[0]
        fp.rows = values[0]
        for (col, stat), v in zip(layout, values[1:], strict=True):
            fp.columns[col][stat] = canonical(v)
    else:
        fp.error = error_text(res)
        # Isolate the unreadable columns: one query per column.
        for col, typ in columns:
            csql, clayout = fingerprint_sql(namespace, table, [(col, typ)])
            cres, crows = run_one(engine, csql)
            if crows:
                fp.rows = crows[0][0]
                for (_, stat), v in zip(clayout, crows[0][1:], strict=True):
                    fp.columns[col][stat] = canonical(v)
            else:
                fp.columns[col]["error"] = error_text(cres)
    fp.seconds = round(time.monotonic() - start, 2)
    return fp


def path_for(engine: str, namespace: str):
    return VERIFY_DIR / engine / f"{namespace}.json"


def collect(engine, namespace: str, on_table=None) -> dict:
    out = {"engine": engine.name, "namespace": namespace, "collected_at": dt.datetime.now(dt.UTC).isoformat(),
           "engine_info": engine.describe(), "tables": {}}
    for table in catalog.tables(namespace):
        fp = fingerprint_table(engine, namespace, table)
        out["tables"][table] = {"rows": fp.rows, "columns": fp.columns, "error": fp.error, "seconds": fp.seconds}
        if on_table:
            on_table(fp)
    path = path_for(engine.name, namespace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1))
    return out


def load(engine: str, namespace: str) -> dict | None:
    path = path_for(engine, namespace)
    return json.loads(path.read_text()) if path.is_file() else None


# ---------- comparison ----------

def _num(v) -> Decimal | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def values_equal(a, b, iceberg_type: str, stat: str) -> bool:
    if a == b:
        return True
    if a is None or b is None:
        return False
    na, nb = _num(a), _num(b)
    if na is not None and nb is not None and stat != "len" and isinstance(a, int | float | str) and isinstance(
            b, int | float | str):
        if base_type(iceberg_type) in FLOATS and stat in ("min", "max", "sum"):
            fa, fb = float(na), float(nb)
            return abs(fa - fb) <= FLOAT_ABS or abs(fa - fb) / max(abs(fa), abs(fb)) <= FLOAT_REL
        if base_type(iceberg_type) in NUMERIC or stat in ("nn", "len", "true"):
            return na == nb
    return False


@dataclass
class Comparison:
    namespace: str
    tables: int = 0
    tables_match: int = 0
    columns: int = 0
    columns_match: int = 0
    differences: list = field(default_factory=list)   # dicts: table, column, stat, ref, got, kind
    missing_reference: list = field(default_factory=list)


def compare(ref: dict, got: dict) -> Comparison:
    c = Comparison(got["namespace"])
    for table, g in sorted(got["tables"].items()):
        c.tables += 1
        r = ref["tables"].get(table)
        if r is None:
            c.missing_reference.append(table)
            continue
        diffs = []
        if g.get("rows") != r.get("rows"):
            kind = "unreadable" if g.get("rows") is None else "rows"
            diffs.append({"table": table, "column": None, "stat": "rows", "ref": r.get("rows"),
                          "got": g.get("rows"), "kind": kind, "error": g.get("error")})
        for col, rc in r["columns"].items():
            c.columns += 1
            gc = g["columns"].get(col)
            col_diffs = []
            if gc is None:
                col_diffs.append({"stat": "missing", "ref": None, "got": None, "kind": "missing"})
            elif "error" in gc:
                col_diffs.append({"stat": "read", "ref": None, "got": None, "kind": "unreadable",
                                  "error": gc["error"]})
            else:
                for stat, rv in rc.items():
                    if stat in ("type", "error"):
                        continue
                    if not values_equal(rv, gc.get(stat), rc["type"], stat):
                        col_diffs.append({"stat": stat, "ref": rv, "got": gc.get(stat), "kind": "value"})
            if col_diffs:
                diffs += [{"table": table, "column": col, "type": rc["type"], **d} for d in col_diffs]
            else:
                c.columns_match += 1
        if not diffs:
            c.tables_match += 1
        c.differences += diffs
    return c
