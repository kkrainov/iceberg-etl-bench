import re

import pytest

from m0 import cli, datagen
from m0.output import Exit

EXPECTED = datagen.load_expected()
GOOD_PROPS = {"format-version": "2", "write.format.default": "PARQUET", "write.parquet.compression-codec": "zstd"}


class FakeCursor:
    """Answers the metadata queries datagen issues; records every statement."""

    def __init__(self, tables: dict[str, dict]):
        self.tables = tables  # "<ns>.<table>" -> {"rows": n, "props": {...}}
        self.executed: list[str] = []
        self._rows: list = []

    def execute(self, sql: str) -> None:
        self.executed.append(sql)
        if m := re.search(r"table_schema = '(\w+)'", sql):
            ns = m.group(1)
            self._rows = [[k.split(".", 1)[1]] for k in self.tables if k.startswith(ns + ".")]
        elif m := re.search(r'\.(\w+)\."(\w+)\$properties"', sql):
            t = self.tables[f"{m.group(1)}.{m.group(2)}"]
            self._rows = list({**GOOD_PROPS, "current-snapshot-id": "42", **t.get("props", {})}.items())
        elif m := re.search(r'\.(\w+)\."(\w+)\$snapshots"', sql):
            t = self.tables[f"{m.group(1)}.{m.group(2)}"]
            self._rows = [[{"total-records": str(t["rows"]), "total-data-files": "1",
                            "total-files-size": "100", "total-delete-files": "0"}]]
        elif m := re.match(r"CREATE TABLE iceberg\.(\w+)\.(\w+)", sql):
            ns, table = m.groups()
            suite, sf = ns.rsplit("_sf", 1)
            self.tables[f"{ns}.{table}"] = {"rows": EXPECTED["rows"][suite][int(sf)][table]}
            self._rows = [[0]]
        elif m := re.match(r"DROP TABLE iceberg\.(\w+)\.(\w+)", sql):
            del self.tables[f"{m.group(1)}.{m.group(2)}"]
            self._rows = []
        else:
            self._rows = []

    def fetchall(self) -> list:
        return self._rows


def full_suite(suite: str, sf: int, **overrides) -> dict:
    tables = {f"{suite}_sf{sf}.{t}": {"rows": n} for t, n in EXPECTED["rows"][suite][sf].items()}
    tables.update(overrides)
    return tables


def ctas(cur: FakeCursor) -> list[str]:
    return [s for s in cur.executed if s.startswith("CREATE TABLE")]


def test_expected_covers_suites_and_scale_factors():
    assert datagen.scale_factors(EXPECTED) == [1, 10]
    for suite in datagen.SUITES:
        for sf in (1, 10):
            tables = datagen.tables_for(EXPECTED, suite, sf)
            assert tables and all(isinstance(n, int) and n > 0 for n in tables.values()), (suite, sf)
    assert len(EXPECTED["rows"]["tpcds"][10]) == 24
    assert len(EXPECTED["rows"]["tpch"][10]) == 8


def test_unknown_scale_factor_is_a_prerequisite_error():
    with pytest.raises(datagen.Prerequisite):
        datagen.tables_for(EXPECTED, "tpch", 3)


def test_etl_order_follows_params():
    assert list(datagen.tables_for(EXPECTED, "etl", 1)) == datagen.load_params()["tables"]


def test_render_standard_ctas():
    sql = datagen.render_sql("tpcds", 10, "store_sales")
    assert sql.startswith("CREATE TABLE iceberg.tpcds_sf10.store_sales\n")
    assert "compression_codec = 'ZSTD'" in sql and "format_version = 2" in sql
    assert sql.endswith("SELECT * FROM tpcds.sf10.store_sales")


@pytest.mark.parametrize("table", datagen.load_params()["tables"])
def test_render_etl_sql_is_fully_substituted(table):
    sql = datagen.render_sql("etl", 10, table)
    assert "$" not in sql.replace('"$', "")  # no unrendered ${placeholders}
    assert f"CREATE TABLE iceberg.etl_sf10.{table}" in sql
    assert "iceberg.tpcds_sf10." in sql or "iceberg.etl_sf10." in sql
    assert str(datagen.load_params()["seed"]) in sql


def test_validate():
    good = datagen.TableState(snapshot_id=1, rows=5, files=1, bytes=10, properties=dict(GOOD_PROPS))
    assert datagen.validate(good, 5) == []
    assert datagen.validate(good, 6) == ["rows 5, expected 6"]
    assert "no expected count" in datagen.validate(good, None)[0]
    snappy = datagen.TableState(snapshot_id=1, rows=5, properties={**GOOD_PROPS, "write.parquet.compression-codec": "snappy"})
    assert any("compression" in p for p in datagen.validate(snappy, 5))
    assert "no snapshot" in datagen.validate(datagen.TableState(properties=dict(GOOD_PROPS)), None)


def test_generate_creates_missing_and_skips_valid():
    cur = FakeCursor(full_suite("tpch", 1))
    del cur.tables["tpch_sf1.lineitem"]
    cur.tables["tpch_sf1.orders"] = {"rows": 7}  # invalid -> regenerated
    todo = datagen.plan(cur, "tpch", 1, EXPECTED, force=False)
    results = datagen.generate(cur, "tpch", 1, EXPECTED, todo)
    assert all(r.ok for r in results)
    actions = {r.table: r.action for r in results}
    assert actions["lineitem"] == "created" and actions["orders"] == "regenerated"
    assert sum(a == "skipped" for a in actions.values()) == 6
    assert "DROP TABLE iceberg.tpch_sf1.orders" in cur.executed
    assert len(ctas(cur)) == 2


def test_rerun_is_a_noop_and_force_rebuilds():
    cur = FakeCursor(full_suite("tpch", 1))
    results = datagen.generate(cur, "tpch", 1, EXPECTED, datagen.plan(cur, "tpch", 1, EXPECTED, force=False))
    assert {r.action for r in results} == {"skipped"} and not ctas(cur)
    results = datagen.generate(cur, "tpch", 1, EXPECTED, datagen.plan(cur, "tpch", 1, EXPECTED, force=True))
    assert {r.action for r in results} == {"regenerated"} and len(ctas(cur)) == 8


def test_generate_stops_at_first_failure():
    class Failing(FakeCursor):
        def execute(self, sql):
            if sql.startswith("CREATE TABLE iceberg.tpch_sf1.supplier"):
                raise RuntimeError("Query exceeded per-node memory limit\nstack")
            super().execute(sql)

    cur = Failing({})
    results = datagen.generate(cur, "tpch", 1, EXPECTED, datagen.plan(cur, "tpch", 1, EXPECTED, force=False))
    assert [r.table for r in results] == ["region", "nation", "supplier"]
    assert results[-1].action == "failed" and results[-1].problems == ["Query exceeded per-node memory limit"]


def test_status_reports_missing_and_wrong_counts():
    cur = FakeCursor(full_suite("tpch", 1, **{"tpch_sf1.nation": {"rows": 24}}))
    del cur.tables["tpch_sf1.region"]
    bad = {r.table: r for r in datagen.status(cur, "tpch", 1, EXPECTED) if not r.ok}
    assert set(bad) == {"region", "nation"}
    assert bad["region"].action == "missing" and bad["nation"].problems == ["rows 24, expected 25"]


def test_etl_requires_tpcds_source():
    with pytest.raises(datagen.Prerequisite, match="m0 data gen --suite tpcds --sf 1"):
        datagen.check_sources(FakeCursor({}), "etl", 1, EXPECTED)
    datagen.check_sources(FakeCursor(full_suite("tpcds", 1)), "etl", 1, EXPECTED)


def test_disk_requirement_scales_with_sf():
    one, ten = (datagen.disk_required_gib(EXPECTED, "tpcds", sf) for sf in (1, 10))
    assert ten > one >= datagen.DISK_RESERVE_GIB


def test_gen_needs_suite_and_sf(capsys):
    assert cli.main(["data", "gen", "--sf", "1"]) == Exit.PREREQUISITE
    assert "--suite" in capsys.readouterr().out


def test_trino_down_is_a_prerequisite(monkeypatch, capsys):
    def refuse():
        raise ConnectionRefusedError()

    monkeypatch.setattr(datagen, "connect", refuse)
    assert cli.main(["data", "status", "--suite", "tpch", "--sf", "1"]) == Exit.PREREQUISITE
    assert "m0 env up --engine trino" in capsys.readouterr().out
