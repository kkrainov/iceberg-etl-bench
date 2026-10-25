import pytest

from m0 import plan as plan_mod
from m0.queries import load_manifest, render

BASE = {"name": "t", "phase": "L", "sf": 1, "seed": 7, "engines": ["trino"], "rounds": None, "mem_levels": [100],
        "iterations": 2, "cold": "per_query", "timeout_s": 600, "result_rows_cap": 10,
        "queries": [{"suite": "tpch", "ids": ["q01", "q03", "q06"]}, {"suite": "tpcds", "ids": ["q03"]}]}


def make(**kw) -> dict:
    p = {**BASE, **kw}
    plan_mod.validate(p)
    if p["rounds"] is None:
        p["rounds"] = 2 if len(p["engines"]) > 1 else 1
    return p


def test_expand_is_deterministic():
    a, b = plan_mod.expand(make()), plan_mod.expand(make())
    assert a == b
    assert len(a) == 4 * 3  # queries x (1 cold + 2 hot)


def test_seed_changes_order_but_not_cells():
    a, b = plan_mod.expand(make(seed=1)), plan_mod.expand(make(seed=2))
    assert {c.cell_id for c in a} == {c.cell_id for c in b}
    orders = {tuple(c.cell_id for c in plan_mod.expand(make(seed=s))) for s in range(10)}
    assert len(orders) > 1


def test_cell_ids_are_stable_and_readable():
    cells = plan_mod.expand(make())
    assert "trino.m100.r0.tpch.q01.i0" in {c.cell_id for c in cells}
    assert len({c.cell_id for c in cells}) == len(cells)
    assert [c.seq for c in cells] == list(range(len(cells)))


def test_iterations_follow_each_query_and_cold_policy():
    cells = plan_mod.expand(make(cold="per_query"))
    for i in range(0, len(cells), 3):
        group = cells[i:i + 3]
        assert [c.iteration for c in group] == [0, 1, 2]
        assert len({(c.suite, c.query_id) for c in group}) == 1
        assert [c.restart_before for c in group] == [True, False, False]
    per_block = plan_mod.expand(make(cold="per_block"))
    assert [c.restart_before for c in per_block].count(True) == 1 and per_block[0].restart_before


def test_abba_engine_blocks_with_same_query_order_per_round():
    cells = plan_mod.expand(make(engines=["trino", "df"]))
    blocks = []
    for c in cells:
        if not blocks or blocks[-1][0] != c.block:
            blocks.append((c.block, c.engine, c.round))
    assert [b[1] for b in blocks] == ["trino", "df", "df", "trino"]
    assert [b[2] for b in blocks] == [0, 0, 1, 1]
    order = {}
    for c in cells:
        order.setdefault((c.round, c.engine), [])
        if c.iteration == 0:
            order[(c.round, c.engine)].append(c.query_id + c.suite)
    assert order[(0, "trino")] == order[(0, "df")]
    assert order[(1, "trino")] == order[(1, "df")]


def test_per_query_iteration_override_and_expect():
    p = make(queries=[{"suite": "etl", "ids": ["X1_mem"], "iterations": 0, "expect": "err_mem"}])
    cells = plan_mod.expand(p)
    assert len(cells) == 1 and cells[0].expect == "err_mem"


@pytest.mark.parametrize("bad", [
    {"engines": ["presto"]}, {"cold": "never"}, {"queries": [{"suite": "tpch", "ids": ["q99"]}]},
    {"mem_levels": [0]}, {"iterations": -1},
])
def test_invalid_plans_are_rejected(bad):
    with pytest.raises(plan_mod.PlanError):
        make(**bad)


def test_filters():
    cells = plan_mod.expand(make())
    q03 = [c for c in cells if plan_mod.matches(c, None, ["q03"])]
    assert {c.suite for c in q03} == {"tpch", "tpcds"}
    assert all(c.suite == "tpch" for c in cells if plan_mod.matches(c, ["trino"], ["tpch/q03"]))
    assert not [c for c in cells if plan_mod.matches(c, ["df"], None)]


@pytest.mark.parametrize("name", ["phase-l-smoke", "phase-l-cold-check"])
def test_repository_plans_load(name):
    p = plan_mod.load(plan_mod.Path(__file__).parent.parent / "plans" / f"{name}.yaml")
    assert plan_mod.expand(p)


def test_smoke_plan_shape():
    p = plan_mod.load(plan_mod.Path(__file__).parent.parent / "plans" / "phase-l-smoke.yaml")
    cells = plan_mod.expand(p)
    assert p["sf"] == 1 and p["cold"] == "per_block"
    assert [c for c in cells if c.expect == "err_mem"]
    # Two engines, two rounds (ABBA), 8 queries x (1 + 3 hot) + X1_mem per block.
    assert len(cells) == 4 * (8 * 4 + 1)
    blocks = [c.engine for c in cells if c.seq == 0 or c.block != cells[c.seq - 1].block]
    assert blocks == ["trino", "df", "df", "trino"]


def test_every_manifest_query_renders_for_every_engine():
    for q in load_manifest().values():
        for engine in ("trino", "df", "dfd"):
            sql = render(q, 1, engine)
            assert "${" not in sql and "iceberg." in sql


def test_every_manifest_query_renders():
    for q in load_manifest().values():
        sql = render(q, 10)
        assert "${" not in sql and "--" not in sql
        for ns in q.namespaces:
            assert f"iceberg.{ns}_sf10." in sql
