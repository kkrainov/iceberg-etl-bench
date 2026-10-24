import datetime as dt
from decimal import Decimal

import pyarrow.parquet as pq
import pytest

from m0.store import SCHEMA, RunStore, arrow_type, list_runs


def row(cell_id: str, outcome: str = "ok") -> dict:
    return {"run_id": "r1", "cell_id": cell_id, "engine": "trino", "outcome": outcome, "wall_ms": 12.5,
            "peak_mem_bytes": 2**33, "cold": True, "iteration": 0}


def test_append_recorded_and_parquet(tmp_path):
    s = RunStore("r1", tmp_path)
    s.init("name: x\n", [{"cell_id": "a"}, {"cell_id": "b"}])
    s.append(row("a"))
    s.append(row("b", "err_mem"))
    assert s.recorded() == {"a", "b"}
    t = pq.read_table(s.write_parquet())
    assert t.schema.equals(SCHEMA)
    assert t.column("outcome").to_pylist() == ["ok", "err_mem"]
    assert t.column("peak_mem_bytes").to_pylist() == [2**33, 2**33]


def test_torn_last_line_is_ignored(tmp_path):
    s = RunStore("r1", tmp_path)
    s.init("name: x\n", [])
    s.append(row("a"))
    with s.jsonl.open("a") as f:
        f.write('{"cell_id": "b", "eng')
    assert s.recorded() == {"a"}


def test_unknown_columns_are_rejected(tmp_path):
    s = RunStore("r1", tmp_path)
    s.init("name: x\n", [])
    with pytest.raises(ValueError):
        s.append({**row("a"), "made_up": 1})


def test_result_rows_typed_and_capped(tmp_path):
    s = RunStore("r1", tmp_path)
    s.init("name: x\n", [])
    cols = [("k", "varchar"), ("v", "decimal(12, 2)"), ("d", "date"), ("n", "bigint"), ("a", "array(bigint)")]
    rows = [["x", Decimal("1.50"), dt.date(2000, 1, 1), 1, [1, 2]], [None, None, None, None, None]] * 3
    assert s.write_result("c1", cols, rows, max_rows=100) == "full"
    t = pq.read_table(tmp_path / "r1" / "rows" / "c1.parquet")
    assert [str(f.type) for f in t.schema][:4] == ["string", "decimal128(12, 2)", "date32[day]", "int64"]
    assert t.num_rows == 6
    assert s.write_result("c2", cols, rows, max_rows=4) == "truncated"
    assert pq.read_table(tmp_path / "r1" / "rows" / "c2.parquet").num_rows == 4


def test_arrow_type_mapping():
    assert str(arrow_type("timestamp(6)")) == "timestamp[us]"
    assert arrow_type("timestamp(3) with time zone") is None
    assert str(arrow_type("varchar(25)")) == "string"


def test_list_runs_by_start_time(tmp_path):
    for run_id, started in (("b", "2026-01-02"), ("a", "2026-01-03")):
        s = RunStore(run_id, tmp_path)
        s.init("name: x\n", [])
        s.write_json("env.json", {"started_at": started})
    assert [r.run_id for r in list_runs(tmp_path)] == ["b", "a"]
