"""DataFusion adapter: error mapping, byte sizes, metrics, JSON results comparable with Trino's."""

import datetime as dt
from decimal import Decimal
from pathlib import Path

from m0.engines.base import ExecResult, canonical
from m0.engines.df import DfEngine, engine_error, operator_time, parse_bytes


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
    stats = {"elapsed_ms": 12.5, "planning_ms": 2.0, "process_cpu_s": 0.7, "pool": {"peak": 1234, "limit": 4},
             "metrics": {"operators": [
                 {"depth": 0, "op": "SortPreservingMergeExec", "metrics": {"elapsed_compute": 1_000_000}},
                 {"depth": 1, "op": "AggregateExec", "metrics": {"elapsed_compute": 5_000_000}},
                 {"depth": 2, "op": "RepartitionExec", "metrics": {"elapsed_compute": 10, "repartition_time": 2_000_000,
                                                                   "send_time": 1_000_000, "fetch_time": 9e9}},
                 {"depth": 3, "op": "DataSourceExec", "metrics": {"elapsed_compute": 7}}]}}
    assert DfEngine().metrics(stats) == {"engine_elapsed_ms": 12.5, "engine_cpu_s": 0.7,
                                         "engine_peak_mem_bytes": 1234, "planning_ms": 2.0,
                                         "scan_ms": None, "exchange_ms": 4.0, "compute_ms": 5.0}
    none = DfEngine().metrics(None)
    assert none["engine_peak_mem_bytes"] is None and none["compute_ms"] is None


def test_scan_time_only_from_a_scan_timer():
    ops = [{"op": "DataSourceExec", "metrics": {"elapsed_compute": 5, "time_elapsed_processing": 3_000_000}}]
    assert operator_time(ops)["scan_ms"] == 3.0
    assert operator_time([])["scan_ms"] is None


def test_canonical_json_values():
    assert canonical(Decimal("1.50")) == "1.50"
    assert canonical(Decimal("-0.00")) == "0.00"
    assert canonical(Decimal("1E+2")) == "100"
    assert canonical(dt.date(2000, 1, 2)) == "2000-01-02"
    assert canonical(dt.datetime(2000, 1, 2, 3, 4, 5, 6000)) == "2000-01-02T03:04:05.006000"
    tz = dt.timezone(dt.timedelta(hours=2))
    assert canonical(dt.datetime(2000, 1, 2, 3, 4, 5, tzinfo=tz)) == "2000-01-02T01:04:05.000000Z"
    assert canonical(float("nan")) == "NaN" and canonical(float("-inf")) == "-Infinity"
    assert canonical(b"\x0a\xff") == "0aff"
    assert canonical([1, Decimal("2.0"), None]) == [1, "2.0", None]
    assert [canonical(v) for v in (None, True, 3, 0.25, "x")] == [None, True, 3, 0.25, "x"]


# The same SELECT on both engines, recorded 2026-10-25 on the local stack (tpch_sf1.lineitem,
# first two rows): bigint, decimal(12,2), date, CAST(date AS timestamp), CAST(.. AS real),
# CAST(.. AS double), varchar, boolean, NULL varchar, decimal(12,2), decimal(38,4).
# DF_BODY is dfrunner's response (dfrunner/src/query.rs `to_json`); TRINO_ROWS are the
# trino client's Python values, which the Trino adapter passes through `canonical`.
DF_BODY = {"columns": [
    {"name": "l_orderkey", "type": "Int64"},
    {"name": "l_extendedprice", "type": "Decimal128(12, 2)"},
    {"name": "l_shipdate", "type": "Date32"},
    {"name": "ts", "type": "Timestamp(ns)"},
    {"name": "r", "type": "Float32"},
    {"name": "d", "type": "Float64"},
    {"name": "l_comment", "type": "Utf8"},
    {"name": "b", "type": "Boolean"},
    {"name": "n", "type": "Utf8View"},
    {"name": "l_quantity", "type": "Decimal128(12, 2)"},
    {"name": "q4", "type": "Decimal128(38, 4)"}],
    "rows": [
        [1, "21168.23", "1996-03-13", "1996-03-13T00:00:00.000000", 0.04, 0.02,
         "egular courts above the", False, None, "17.00", "17.0000"],
        [1, "45983.16", "1996-04-12", "1996-04-12T00:00:00.000000", 0.09, 0.06,
         "ly final dependencies: slyly bold ", False, None, "36.00", "36.0000"]]}
TRINO_COLUMNS = [
    ('l_orderkey', 'bigint'),
    ('l_extendedprice', 'decimal(12, 2)'),
    ('l_shipdate', 'date'),
    ('ts', 'timestamp(3)'),
    ('r', 'real'),
    ('d', 'double'),
    ('l_comment', 'varchar'),
    ('b', 'boolean'),
    ('n', 'varchar'),
    ('l_quantity', 'decimal(12, 2)'),
    ('q4', 'decimal(38, 4)')]
TRINO_ROWS = [
    [1, Decimal("21168.23"), dt.date(1996, 3, 13), dt.datetime(1996, 3, 13), 0.04, 0.02,
     'egular courts above the', False, None, Decimal("17.00"), Decimal("17.0000")],
    [1, Decimal("45983.16"), dt.date(1996, 4, 12), dt.datetime(1996, 4, 12), 0.09, 0.06,
     'ly final dependencies: slyly bold ', False, None, Decimal("36.00"), Decimal("36.0000")]]


def test_df_and_trino_results_store_the_same_json():
    trino = [[canonical(v) for v in r] for r in TRINO_ROWS]
    assert trino == DF_BODY["rows"]
    assert len(TRINO_COLUMNS) == len(DF_BODY["columns"])


def test_exec_result_completed():
    r = ExecResult(wall_ms=1.0, rows=[[1, "x"], [2, "y"]])
    assert r.completed and r.row_count == 2
    assert ExecResult(wall_ms=None).completed is False and ExecResult(wall_ms=None).row_count is None


def test_variants_are_wired_everywhere():
    """Every DataFusion variant has an adapter, a compose service, the DataFusion dialect,
    the DataFusion error rules and a plan engine name."""
    import yaml

    from m0 import engines, env_local, plan
    from m0.engines.df import VARIANTS
    from m0.paths import LOCAL_COMPOSE
    from m0.queries import DIALECT

    assert set(VARIANTS) == set(engines.DF_ENGINES)
    services = yaml.safe_load(LOCAL_COMPOSE.read_text())["services"]
    rules = yaml.safe_load((Path(__file__).parents[1] / "m0" / "classify_rules.yaml").read_text())["rules"]
    for name, v in VARIANTS.items():
        eng = engines.get(name)
        assert (eng.name, eng.service) == (name, name)
        assert name in env_local.ENGINES and name in plan.ENGINES and DIALECT[name] == "datafusion"
        svc = services[name]
        assert svc["profiles"] == [name] and svc["ports"] == ["127.0.0.1:9000:9000"]
        assert svc["image"] == services[v.build_service]["image"]
        assert services[v.build_service]["build"]["args"]["DFRUNNER_VARIANT"] == v.build
        assert ("DFRUNNER_CONFIG_EXTRA" in svc["environment"]) == v.column_stats
        assert all(name in r["engine"] for r in rules if r["id"].startswith("df."))
        assert all(p.is_file() for p in eng.config_files())


def test_cs_variants_add_only_column_stats():
    from m0.engines.df import CS_CONFIG_FILE, DfEngine, read_properties

    assert read_properties(CS_CONFIG_FILE) == {"iceberg.column_stats_enabled": "true"}
    assert set(DfEngine("df-jo-cs").config_files()) - set(DfEngine("df-jo").config_files()) == {CS_CONFIG_FILE}


def test_versions_lock_pins_the_jo_patch():
    import hashlib

    from m0 import versions
    from m0.paths import REPO_ROOT

    pr = versions.load()["engines"]["datafusion_pr24456"]
    for key, path in (("patch_sha256", pr["patch"]), ("lockfile_sha256", "dfrunner/patches/jo.Cargo.lock")):
        assert hashlib.sha256((REPO_ROOT / path).read_bytes()).hexdigest() == pr[key], path


def test_describe_only_its_own_container(monkeypatch):
    """All variants share port 9000: a variant whose container isn't running must not
    record the running variant's versions and settings in run.json."""
    from m0 import env_local

    eng = DfEngine("df-jo")
    monkeypatch.setattr(env_local, "container_state", lambda svc: {"running": svc == "df-sail"})
    monkeypatch.setattr(eng, "status", lambda: {"mode": "single", "versions": {"variant": "sail"}})
    assert "error" in eng.describe()
    monkeypatch.setattr(env_local, "container_state", lambda svc: {"running": svc == "df-jo"})
    assert eng.describe()["versions"] == {"variant": "sail"}
