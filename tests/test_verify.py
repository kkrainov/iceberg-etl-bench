"""`m0 data verify`: fingerprint SQL and the cross-engine comparison."""

from decimal import Decimal

from m0 import verify


def test_fingerprint_sql_layout():
    sql, layout = verify.fingerprint_sql("tpch_sf1", "orders", [
        ("o_orderkey", "long"), ("o_comment", "string"), ("o_orderdate", "date"),
        ("o_totalprice", "decimal(12, 2)"), ("flag", "boolean"), ("b", "binary")])
    assert sql.startswith('SELECT count(*) AS a0, count("o_orderkey") AS a1, min("o_orderkey") AS a2')
    assert 'sum(length("o_comment"))' in sql and 'sum("o_totalprice")' in sql
    assert 'sum("o_orderdate")' not in sql
    assert sql.endswith('FROM iceberg."tpch_sf1"."orders"')
    assert layout[:4] == [("o_orderkey", "nn"), ("o_orderkey", "min"), ("o_orderkey", "max"), ("o_orderkey", "sum")]
    assert ("flag", "true") in layout and ("b", "nn") in layout and ("b", "min") not in layout
    assert sql.count(" AS a") == len(layout) + 1


def test_canonical_values():
    import datetime as dt

    assert verify.canonical(Decimal("1.50")) == "1.50"
    assert verify.canonical(dt.date(1998, 12, 1)) == "1998-12-01"
    assert verify.canonical(dt.datetime(2020, 1, 1, 0, 0, 0, 5)) == "2020-01-01T00:00:00.000005"
    assert verify.canonical(None) is None and verify.canonical(7) == 7


def fp(rows, **cols):
    return {"namespace": "ns", "tables": {"t": {"rows": rows, "error": None, "columns": cols}}}


def test_compare_equal_with_decimal_scale_and_float_tolerance():
    ref = fp(10, a={"type": "decimal(7, 2)", "nn": 10, "sum": "100.50"},
             f={"type": "double", "nn": 10, "sum": 1.0000000001}, s={"type": "string", "nn": 9, "len": 40})
    got = fp(10, a={"type": "decimal(7, 2)", "nn": 10, "sum": "100.5"},
             f={"type": "double", "nn": 10, "sum": 1.0}, s={"type": "string", "nn": 9, "len": 40})
    c = verify.compare(ref, got)
    assert (c.tables, c.tables_match, c.columns, c.columns_match, c.differences) == (1, 1, 3, 3, [])


def test_compare_reports_value_differences():
    ref = fp(10, a={"type": "long", "nn": 10, "min": 1, "max": 9, "sum": 45},
             s={"type": "string", "nn": 10, "min": "a", "max": "z", "len": 30})
    got = fp(10, a={"type": "long", "nn": 10, "min": 1, "max": 9, "sum": 46},
             s={"type": "string", "nn": 10, "min": "a ", "max": "z", "len": 30})
    c = verify.compare(ref, got)
    assert c.tables_match == 0 and c.columns_match == 0
    assert [(d["column"], d["stat"], d["kind"]) for d in c.differences] == [("a", "sum", "value"), ("s", "min", "value")]


def test_compare_exact_for_integers_and_decimals():
    ref = fp(1, a={"type": "decimal(38, 2)", "sum": "1.01"})
    got = fp(1, a={"type": "decimal(38, 2)", "sum": "1.0100001"})
    assert verify.compare(ref, got).differences


def test_compare_reports_unreadable_table_and_column():
    ref = fp(5, a={"type": "timestamp", "nn": 5, "min": "2020-01-01T00:00:00"}, b={"type": "int", "nn": 5})
    got = {"namespace": "ns", "tables": {"t": {"rows": 5, "error": "Execution: unsupported", "columns": {
        "a": {"type": "timestamp", "error": "NotImplemented: timestamp"}, "b": {"type": "int", "nn": 5}}}}}
    c = verify.compare(ref, got)
    assert c.columns_match == 1
    assert [(d["column"], d["kind"], d["error"]) for d in c.differences] == [
        ("a", "unreadable", "NotImplemented: timestamp")]
    got["tables"]["t"]["rows"] = None
    kinds = {d["kind"] for d in verify.compare(ref, got).differences}
    assert kinds == {"unreadable"}


def test_compare_missing_reference_table():
    c = verify.compare({"namespace": "ns", "tables": {}}, fp(1, a={"type": "int", "nn": 1}))
    assert c.missing_reference == ["t"] and c.tables_match == 0
