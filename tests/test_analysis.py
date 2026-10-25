"""summarize / compare output bounds, explain and logs helpers, on synthetic runs."""

import random
from datetime import UTC, datetime, timedelta

import pytest

from m0 import analysis, cli, explain, logs
from m0 import plan as plan_mod
from m0.output import Exit
from m0.paths import PLANS_DIR
from m0.queries import load_manifest
from m0.store import RunStore

TYPES = {"trino": "decimal(38, 2)", "df": "Decimal128(38, 2)"}


def fake_run(root, run_id="r1", fail_every=7, mismatch_every=11, seed=0) -> RunStore:
    """A full A1+A2 run with many distinct failures and mismatches."""
    plan = plan_mod.load(PLANS_DIR / "phase-l-a1a2-sf1.yaml")
    s = RunStore(run_id, root)
    s.write_run({"run_id": run_id, "plan": plan, "started_at": "2026-10-25T10:00:00+00:00", "git": {"sha": "abc"}})
    rng = random.Random(seed)
    t0 = datetime(2026, 10, 25, 10, tzinfo=UTC)
    answered = set()
    for i, c in enumerate(plan_mod.expand(plan)):
        n = int(c.query_id[1:3])
        failing = c.engine == "df" and n % fail_every == 0
        outcome = "err_other" if failing else "ok"
        if c.engine == "trino" and c.suite == "tpcds" and n == 50:
            outcome = "timeout"
        row = {"cell_id": c.cell_id, "seq": c.seq, "block": c.block, "round": c.round, "engine": c.engine,
               "mem_level": 100, "suite": c.suite, "query_id": c.query_id, "iteration": c.iteration,
               "started_at": (t0 + timedelta(seconds=i)).isoformat(),
               "wall_ms": (1000 if c.engine == "trino" else 400) * (1 + n / 50) * rng.uniform(0.9, 1.1),
               "outcome": outcome, "error_line": f"Plan: error {n} in {c.query_id}" if failing else None,
               "engine_query_id": f"q-{i}"}
        s.append(row)
        key = (c.engine, c.suite, c.query_id)
        if outcome == "ok" and key not in answered:
            answered.add(key)
            value = "1.00" if not (c.engine == "df" and n % mismatch_every == 0) else "2.00"
            s.write_answer(row, [("x", TYPES[c.engine])], [[value]], 100)
    return s


@pytest.fixture
def run(tmp_path):
    return fake_run(tmp_path)


def test_summary_of_a_full_run_stays_under_40_lines(run):
    s = analysis.summarize(run)
    lines = analysis.render_summary(s)
    assert len(lines) <= analysis.MAX_LINES, "\n".join(lines)
    text = "\n".join(lines)
    assert "not ok:" in text and "answer mismatches vs trino" in text and "slowest relative" in text
    assert "--json" in text
    assert s["counts"]["df"].get("ok_mismatch")  # effective outcome from the diff
    assert s["recorded_counts"]["df"].get("ok_mismatch") is None  # never written back


def test_summary_detail_is_complete_in_json(run):
    s = analysis.summarize(run)
    mism = [d for d in s["diffs"] if d["status"] == "mismatch"]
    assert len(mism) == len({(d["suite"], d["query_id"]) for d in mism}) > 5
    assert {g["outcome"] for g in s["failures"]} == {"err_other", "timeout"}
    assert len(s["queries"]) == 2 * 125


def test_failures_grouped_by_signature():
    rows = [{"engine": "df", "outcome": "err_other", "suite": "tpcds", "query_id": q, "expected_outcome": None,
             "error_line": f"Plan: column 'x{q}' at 12 not found"} for q in ("q01", "q02", "q02")]
    (g,) = analysis.group_failures(rows)
    assert g["queries"] == ["tpcds/q01", "tpcds/q02"] and g["cells"] == 3


def test_compare_runs_stays_bounded(tmp_path):
    a, b = fake_run(tmp_path, "a", seed=1), fake_run(tmp_path, "b", fail_every=5, seed=2)
    c = analysis.compare(a, b)
    lines = analysis.render_compare(c)
    assert len(lines) <= analysis.MAX_LINES
    assert c["engines"]["trino"]["ratio_total"] == pytest.approx(1, rel=0.1)
    assert c["engines"]["df"]["outcome_changes"]


def test_cli_unknown_run_is_a_prerequisite(capsys):
    assert cli.main(["summarize", "no-such-run"]) == Exit.PREREQUISITE
    assert cli.main(["compare", "no-such-run", "latest"]) == Exit.PREREQUISITE
    assert cli.main(["logs", "no-such-run", "df", "tpch/q01"]) == Exit.PREREQUISITE
    assert "no run" in capsys.readouterr().out


# ---- explain ----

def test_find_query():
    m = load_manifest()
    assert explain.find_query("tpch/q01", m).key == "tpch/q01"
    assert explain.find_query("tpcds.q14a", m).key == "tpcds/q14a"
    assert explain.find_query("q14a", m).key == "tpcds/q14a"
    with pytest.raises(explain.ExplainError, match="ambiguous"):
        explain.find_query("q01", m)
    with pytest.raises(explain.ExplainError, match="no query"):
        explain.find_query("q100", m)


def test_trino_operators_summed_per_node():
    info = {"queryStats": {"operatorSummaries": [
        {"planNodeId": "4", "operatorType": "TableScanOperator", "addInputWall": "0.00ns", "getOutputWall": "1.50s",
         "finishWall": "0.00ns", "getOutputCpu": "1.00s", "outputPositions": 10},
        {"planNodeId": "4", "operatorType": "TableScanOperator", "getOutputWall": "500.00ms", "outputPositions": 5},
        {"planNodeId": "7", "operatorType": "HashBuilderOperator", "addInputWall": "100ms", "outputPositions": 0}]}}
    ops = explain.trino_operators(info)
    assert ops[0]["op"] == "TableScanOperator" and ops[0]["wall_ms"] == 2000 and ops[0]["output_rows"] == 15


def test_df_operators_by_elapsed_compute():
    st = {"metrics": {"operators": [{"depth": 0, "op": "SortExec", "metrics": {"elapsed_compute": 1_000_000}},
                                    {"depth": 1, "op": "HashJoinExec",
                                     "metrics": {"elapsed_compute": 5_000_000, "output_rows": 3}}]}}
    ops = explain.df_operators(st)
    assert [o["op"] for o in ops] == ["HashJoinExec", "SortExec"] and ops[0]["cpu_ms"] == 5.0


# ---- logs ----

def test_parse_time_handles_docker_nanoseconds():
    t = logs.parse_time("2026-10-25T12:23:44.123456789Z")
    assert t == datetime(2026, 10, 25, 12, 23, 44, 123456, tzinfo=UTC)
    assert logs.parse_time("2026-10-25T11:51:40.956+00:00").microsecond == 956000
    assert logs.parse_time("0001-01-01T00:00:00Z") is None


def test_windows_one_per_round(run):
    q = load_manifest()["tpch/q01"]
    ws = logs.windows(run.rows(), "df", q)
    assert [w["round"] for w in ws] == [0, 1]
    assert all(w["cells"] == 4 and w["end"] > w["start"] for w in ws)


def test_logs_say_when_the_container_was_recreated(run, monkeypatch):
    q = load_manifest()["tpch/q01"]
    monkeypatch.setattr(logs.env_local, "container_state",
                        lambda svc: {"id": "abc", "started_at": "2030-01-01T00:00:00Z"})
    r = logs.collect(run.rows(), "df", "df", q, None, 10)
    assert all(w["status"].startswith("gone: container recreated") for w in r["windows"])
    monkeypatch.setattr(logs.env_local, "container_state", lambda svc: {"id": None})
    assert logs.collect(run.rows(), "df", "df", q, None, 10)["windows"][0]["status"] == "gone: no container"


def test_logs_grep_and_tail(run, monkeypatch):
    q = load_manifest()["tpch/q01"]
    monkeypatch.setattr(logs.env_local, "container_state",
                        lambda svc: {"id": "abc", "started_at": "2026-01-01T00:00:00Z"})
    monkeypatch.setattr(logs, "docker_logs", lambda cid, a, b: ["t1 Query q-1 started", "t2 other", "t3 Query q-1 x"])
    r = logs.collect(run.rows(), "df", "df", q, "query q-1", 1)
    assert r["matched"] == 4 and r["lines"] == ["t3 Query q-1 x"]
