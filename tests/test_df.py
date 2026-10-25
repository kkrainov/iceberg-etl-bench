"""DataFusion adapter: error mapping, byte sizes, comparable result types."""

import datetime as dt
from decimal import Decimal

import pyarrow as pa
import pyarrow.parquet as pq

from m0.engines.base import ExecResult
from m0.engines.df import DfEngine, engine_error, parse_bytes
from m0.store import RunStore, comparable_type, normalize_table


def test_engine_error_from_structured_body():
    body = {"query_id": "m0-1", "error": {
        "variant": "ResourcesExhausted", "contexts": [],
        "root_message": "Resources exhausted: Failed to allocate additional 1.0 MB",
        "message": "Resources exhausted: Failed to allocate additional 1.0 MB"}}
    e = engine_error(body, 422)
    assert (e.name, e.type, e.code, e.exception) == ("ResourcesExhausted", None, 422,
                                                     "DataFusionError::ResourcesExhausted")
    assert e.message.startswith("Resources exhausted")


def test_engine_error_keeps_first_context():
    body = {"error": {"variant": "Execution", "contexts": ["IcebergError(FeatureUnsupported)", "x"],
                      "root_message": "r", "message": "m"}}
    e = engine_error(body, 422)
    assert (e.name, e.type, e.message) == ("Execution", "IcebergError(FeatureUnsupported)", "m")


def test_engine_error_unstructured_body():
    e = engine_error({"error": "bad request: missing field `sql`"}, 400)
    assert e.name == "HttpError" and e.code == 400 and "missing field" in e.message


def test_parse_bytes():
    assert parse_bytes("4GiB") == 4 * 1024**3
    assert parse_bytes("512MiB") == 512 * 1024**2
    assert parse_bytes("123") == 123
    assert parse_bytes(None) is None and parse_bytes("lots") is None


def test_pool_limit_matches_trino_query_memory():
    # Memory parity (methodology section 2): DataFusion pool = Trino query.max-memory-per-node.
    from m0.engines.trino import TrinoEngine

    assert DfEngine().pool_limit_bytes() == TrinoEngine().pool_limit_bytes() == 4 * 1024**3


def test_config_files_exist():
    assert all(p.is_file() for p in DfEngine().config_files())


def test_metrics_from_stats():
    stats = {"elapsed_ms": 12.5, "process_cpu_s": 0.7, "pool": {"peak": 1234, "limit": 4}}
    assert DfEngine().metrics(stats) == {"engine_elapsed_ms": 12.5, "engine_cpu_s": 0.7,
                                         "engine_peak_mem_bytes": 1234}
    assert DfEngine().metrics(None)["engine_peak_mem_bytes"] is None


def test_comparable_types():
    assert comparable_type(pa.string_view()) == pa.string()
    assert comparable_type(pa.large_string()) == pa.string()
    assert comparable_type(pa.binary_view()) == pa.binary()
    assert comparable_type(pa.dictionary(pa.int32(), pa.large_string())) == pa.string()
    assert comparable_type(pa.large_list(pa.string_view())) == pa.list_(pa.string())
    for keep in (pa.int64(), pa.decimal128(38, 2), pa.date32(), pa.timestamp("us"), pa.float64()):
        assert comparable_type(keep) == keep


def test_normalize_table_keeps_values_and_engine_types():
    t = pa.table({"name": pa.array(["a", None], type=pa.string_view()),
                  "n": pa.array([1, 2], type=pa.int64())})
    out = normalize_table(t, [("name", "Utf8View"), ("n", "Int64")])
    assert out.schema.types == [pa.string(), pa.int64()]
    assert out.column("name").to_pylist() == ["a", None]
    assert out.schema.metadata[b"engine_types"] == b'["Utf8View", "Int64"]'


def test_arrow_and_trino_results_store_the_same_types(tmp_path):
    store = RunStore("r", root=tmp_path)
    (tmp_path / "r").mkdir()
    trino_cols = [("c", "varchar"), ("s", "decimal(17,2)"), ("d", "date"), ("n", "bigint")]
    rows = [["x", Decimal("1.50"), dt.date(2000, 1, 2), 3]]
    df = pa.table({"c": pa.array(["x"], pa.string_view()), "s": pa.array([Decimal("1.50")], pa.decimal128(17, 2)),
                   "d": pa.array([dt.date(2000, 1, 2)], pa.date32()), "n": pa.array([3], pa.int64())})
    assert store.write_result("trino.c", trino_cols, rows, 10) == "full"
    assert store.write_result("df.c", [(f.name, str(f.type)) for f in df.schema], None, 10, table=df) == "full"
    a = pq.read_table(tmp_path / "r" / "rows" / "trino.c.parquet")
    b = pq.read_table(tmp_path / "r" / "rows" / "df.c.parquet")
    assert a.schema.types == b.schema.types
    assert a.to_pylist() == b.to_pylist()


def test_arrow_result_truncation(tmp_path):
    store = RunStore("r", root=tmp_path)
    (tmp_path / "r").mkdir()
    t = pa.table({"n": list(range(10))})
    assert store.write_result("c", [("n", "Int64")], None, 4, table=t) == "truncated"
    assert pq.read_table(tmp_path / "r" / "rows" / "c.parquet").num_rows == 4


def test_exec_result_rows_from_table():
    r = ExecResult(wall_ms=1.0, table=pa.table({"a": [1, 2], "b": ["x", "y"]}))
    assert r.completed and r.row_count == 2
    assert r.as_rows() == [[1, "x"], [2, "y"]]
    assert ExecResult(wall_ms=None).completed is False
