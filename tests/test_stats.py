"""Stats (methodology section 9): medians, CV, totals over common queries, geomean, bootstrap."""

import math

import pytest

from m0 import stats


def cell(engine, qid, it, wall_s, outcome="ok", suite="tpch", rnd=0):
    return {"engine": engine, "suite": suite, "query_id": qid, "iteration": it, "round": rnd,
            "wall_ms": wall_s * 1000 if wall_s is not None else None, "outcome": outcome,
            "error_line": None if outcome == "ok" else f"{outcome} happened"}


def runs(engine, qid, cold, hot, **kw):
    return [cell(engine, qid, 0, cold, **kw)] + [cell(engine, qid, i + 1, h, **kw) for i, h in enumerate(hot)]


def test_per_query_median_min_max_cold_cv():
    q = stats.per_query(runs("trino", "q01", 5.0, [1.0, 3.0, 2.0]))[("trino", "tpch", "q01")]
    assert (q.median_s, q.min_s, q.max_s, q.cold_s) == (2.0, 1.0, 3.0, 5.0)
    assert q.cv == pytest.approx(0.5)
    assert q.noisy and q.completed and q.hot


def test_without_hot_runs_iteration_zero_is_the_sample():
    q = stats.per_query(runs("df", "q01", 4.0, []))[("df", "tpch", "q01")]
    assert q.median_s == 4.0 and not q.hot and q.cv is None


def test_any_failed_cell_means_not_completed():
    rows = runs("df", "q01", 1.0, [1.0, 1.0]) + [cell("df", "q01", 3, 120.0, "timeout")]
    q = stats.per_query(rows)[("df", "tpch", "q01")]
    assert not q.completed and q.outcome == "timeout" and q.error_line == "timeout happened"
    assert q.median_s == 1.0  # timings of the ok cells stay available


def test_query_outcome_prefers_most_frequent_then_most_severe():
    from collections import Counter

    assert stats.query_outcome(Counter({"ok": 3})) == "ok"
    assert stats.query_outcome(Counter({"ok": 3, "ok_mismatch": 1})) == "ok_mismatch"
    assert stats.query_outcome(Counter({"timeout": 2, "killed": 1})) == "timeout"
    assert stats.query_outcome(Counter({"timeout": 1, "killed": 1})) == "killed"


def test_suite_totals_only_over_queries_every_engine_completed():
    rows = (runs("trino", "q01", 2, [2, 2]) + runs("df", "q01", 1, [1, 1])
            + runs("trino", "q02", 8, [8, 8]) + runs("df", "q02", 2, [2, 2])
            + runs("trino", "q03", 3, [3, 3]) + [cell("df", "q03", 0, 120, "timeout")])
    (s,) = stats.suite_summaries(stats.per_query(rows))
    assert s.common == ["tpch/q01", "tpch/q02"]
    assert s.totals == {"trino": 10, "df": 3}
    assert s.failures == {"trino": {}, "df": {"q03": "timeout"}}
    assert s.ratio_total["df"] == pytest.approx(10 / 3)
    assert s.geomean["df"] == pytest.approx(math.sqrt(2 * 4))
    lo, hi = s.geomean_ci["df"]
    assert lo <= s.geomean["df"] <= hi


def test_geomean():
    assert stats.geomean([2, 8]) == pytest.approx(4)
    assert stats.geomean([]) is None


def test_bootstrap_is_deterministic_and_brackets_the_estimate():
    pairs = [([1.0, 1.1, 0.9, 1.2], [0.5, 0.6, 0.4, 0.5]), ([2.0, 2.2, 1.8], [1.0, 1.0, 1.1])]
    a = stats.bootstrap(pairs, n=500, seed=1)
    assert a == stats.bootstrap(pairs, n=500, seed=1)
    assert a != stats.bootstrap(pairs, n=500, seed=2)
    lo, hi = a["geomean_ci"]
    assert lo < 2.0 < hi
    assert stats.bootstrap([], n=10) == {"geomean_ci": None, "total_ratio_ci": None}


def test_bootstrap_of_constant_runs_is_a_point():
    lo, hi = stats.bootstrap([([2.0, 2.0], [1.0, 1.0])], n=50)["total_ratio_ci"]
    assert lo == hi == 2.0


def test_percentile_interpolates():
    assert stats.percentile([1, 2, 3, 4, 5], 0.5) == 3
    assert stats.percentile([1, 2], 0.25) == 1.25
    assert stats.percentile([7], 0.975) == 7


def test_ratios_slowest_first():
    rows = (runs("trino", "q01", 1, [1]) + runs("df", "q01", 1, [2])
            + runs("trino", "q02", 4, [4]) + runs("df", "q02", 1, [1]))
    r = stats.ratios(stats.per_query(rows), "df")
    assert [(q, round(x, 2)) for q, x, _, _ in r] == [("tpch/q01", 0.5), ("tpch/q02", 4.0)]
