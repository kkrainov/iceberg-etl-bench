"""Result store: `results/<run-id>/`.

Layout (also in the skill's file map):
  plan.yaml          the plan as run (resume refuses a changed plan)
  cells.json         every planned cell, in execution order
  cells.jsonl        one JSON line per recorded cell, appended as cells finish (the source of truth)
  runs.parquet       the same rows as a table, rebuilt from cells.jsonl
  env.json           environment manifest
  run.log            timestamped events: restarts, cache drops, non-ok cells
  cells/<cell_id>/   query.sql, engine_stats.json, memory.csv
  rows/<cell_id>.parquet   result rows of every iteration-0 cell (capped), for the differ
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from m0.paths import RESULTS_DIR

RESULT_BYTES_CAP = 64 * 1024**2

SCHEMA = pa.schema([
    ("run_id", pa.string()), ("cell_id", pa.string()), ("seq", pa.int32()), ("block", pa.int32()),
    ("phase", pa.string()), ("plan", pa.string()), ("engine", pa.string()), ("engine_variant", pa.string()),
    ("suite", pa.string()), ("query_id", pa.string()), ("round", pa.int32()), ("iteration", pa.int32()),
    ("cold", pa.bool_()), ("cold_policy", pa.string()), ("restart_s", pa.float64()),
    ("cache_dropped", pa.bool_()), ("mem_level", pa.int32()), ("concurrency", pa.int32()),
    ("client_id", pa.int32()), ("started_at", pa.string()), ("wall_ms", pa.float64()),
    ("engine_elapsed_ms", pa.float64()), ("cpu_s", pa.float64()), ("engine_cpu_s", pa.float64()),
    ("peak_mem_bytes", pa.int64()), ("peak_mem_source", pa.string()), ("mem_start_bytes", pa.int64()),
    ("engine_peak_mem_bytes", pa.int64()), ("mem_limit_bytes", pa.int64()), ("pool_limit_bytes", pa.int64()),
    ("objstore_peak_mem_bytes", pa.int64()), ("objstore_cpu_s", pa.float64()), ("oom_kills", pa.int32()),
    ("mem_samples", pa.int32()), ("outcome", pa.string()), ("expected_outcome", pa.string()),
    ("error_class", pa.string()), ("error_line", pa.string()), ("unrecognised", pa.bool_()),
    ("engine_query_id", pa.string()), ("rows", pa.int64()), ("result_stored", pa.string()),
    ("result_hash", pa.string()), ("diff_status", pa.string()), ("nodes", pa.int32()),
    ("instance_type", pa.string()), ("spot_usd_per_h", pa.float64()),
])


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class RunStore:
    def __init__(self, run_id: str, root: Path = RESULTS_DIR):
        self.run_id = run_id
        self.dir = root / run_id

    # ---- paths ----
    @property
    def jsonl(self) -> Path:
        return self.dir / "cells.jsonl"

    @property
    def parquet(self) -> Path:
        return self.dir / "runs.parquet"

    def cell_dir(self, cell_id: str) -> Path:
        d = self.dir / "cells" / cell_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def exists(self) -> bool:
        return (self.dir / "plan.yaml").is_file()

    # ---- run-level files ----
    def init(self, plan_text: str, cells: list[dict]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "plan.yaml").write_text(plan_text)
        (self.dir / "cells.json").write_text(json.dumps(cells, indent=1))

    def plan_text(self) -> str:
        return (self.dir / "plan.yaml").read_text()

    def planned(self) -> list[dict]:
        path = self.dir / "cells.json"
        return json.loads(path.read_text()) if path.is_file() else []

    def write_json(self, name: str, data: dict) -> None:
        tmp = self.dir / f".{name}.tmp"
        tmp.write_text(json.dumps(data, indent=2, default=str))
        tmp.replace(self.dir / name)

    def read_json(self, name: str) -> dict:
        path = self.dir / name
        return json.loads(path.read_text()) if path.is_file() else {}

    def log(self, message: str) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with (self.dir / "run.log").open("a") as f:
            f.write(f"{now()} {message}\n")

    # ---- cells ----
    def rows(self) -> list[dict]:
        if not self.jsonl.is_file():
            return []
        out = []
        for line in self.jsonl.read_text().splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:  # torn last line after a hard kill
                    continue
        return out

    def recorded(self) -> set[str]:
        return {r["cell_id"] for r in self.rows()}

    def append(self, row: dict) -> None:
        unknown = set(row) - set(SCHEMA.names)
        if unknown:
            raise ValueError(f"columns not in schema: {sorted(unknown)}")
        with self.jsonl.open("a") as f:
            f.write(json.dumps(row, default=str) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def write_parquet(self) -> Path:
        rows = self.rows()
        table = pa.Table.from_pylist([{k: r.get(k) for k in SCHEMA.names} for r in rows], schema=SCHEMA)
        tmp = self.dir / ".runs.parquet.tmp"
        pq.write_table(table, tmp)
        tmp.replace(self.parquet)
        return self.parquet

    # ---- result rows ----
    def write_result(self, cell_id: str, columns: list[tuple[str, str]], rows: list[list],
                     max_rows: int) -> str:
        """Store result rows as Parquet; returns 'full' or 'truncated'."""
        kept = rows[:max_rows]
        table = result_table(columns, kept)
        status = "full" if len(kept) == len(rows) else "truncated"
        while table.nbytes > RESULT_BYTES_CAP and table.num_rows > 1:
            table, status = table.slice(0, table.num_rows // 2), "truncated"
        d = self.dir / "rows"
        d.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, d / f"{cell_id}.parquet")
        return status


def arrow_type(engine_type: str) -> pa.DataType | None:
    """Arrow type for a Trino type name, None to let pyarrow infer."""
    t = engine_type.lower().replace(" ", "")
    simple = {"boolean": pa.bool_(), "tinyint": pa.int8(), "smallint": pa.int16(), "integer": pa.int32(),
              "bigint": pa.int64(), "real": pa.float32(), "double": pa.float64(), "date": pa.date32(),
              "varbinary": pa.binary()}
    if t in simple:
        return simple[t]
    if t.startswith(("varchar", "char")):
        return pa.string()
    if t.startswith("decimal("):
        p, s = (int(x) for x in t[8:-1].split(","))
        return pa.decimal128(p, s)
    if t.startswith("timestamp(") and "withtimezone" not in t:
        return pa.timestamp("us")
    return None


def result_table(columns: list[tuple[str, str]], rows: list[list]) -> pa.Table:
    arrays, names = [], []
    for i, (name, engine_type) in enumerate(columns):
        values = [r[i] for r in rows]
        typ = arrow_type(engine_type)
        try:
            arr = pa.array(values, type=typ)
        except (pa.ArrowInvalid, pa.ArrowTypeError, TypeError, ValueError):
            arr = pa.array([None if v is None else str(v) for v in values], type=pa.string())
        arrays.append(arr)
        names.append(name)  # compared by position (methodology 8), so repeated names are fine
    meta = {"engine_types": json.dumps([t for _, t in columns])}
    return pa.Table.from_arrays(arrays, names=names).replace_schema_metadata(meta)


def list_runs(root: Path = RESULTS_DIR) -> list[RunStore]:
    """Runs ordered by start time (oldest first)."""
    if not root.is_dir():
        return []
    runs = [RunStore(p.name, root) for p in root.iterdir() if (p / "plan.yaml").is_file()]
    return sorted(runs, key=lambda r: (r.read_json("env.json").get("started_at") or "", r.run_id))
