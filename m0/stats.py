"""Statistics over cells.jsonl (methodology section 9). Standard library only.

- Per engine x query: median of hot runs (iteration >= 1, `ok` cells, every round),
  cold time (median of iteration-0 runs), min/max, coefficient of variation of the
  hot runs (variance flag above 10%). A plan without hot runs uses iteration 0.
- A query is *completed* by an engine when every one of its cells is `ok`.
- Per suite: totals over the queries every engine completed, each engine's failures
  listed; ratio of totals and geometric mean of per-query ratios against the reference
  engine (Trino time / engine time: above 1 means the engine is faster), with a
  bootstrap 95% confidence interval that resamples each query's runs (seeded, so the
  same cells always give the same interval).

Ratios follow datafusion-distributed's benchmarks (benchmarks/src/results.rs): a
per-query median, and a total that only counts queries without errors.
"""

import math
import random
import statistics
from collections import Counter
from dataclasses import asdict, dataclass, field

REFERENCE = "trino"
CV_FLAG = 0.10
BOOTSTRAP_N = 1000
BOOTSTRAP_SEED = 20261025
# Most severe first: picks a query's outcome when its cells disagree.
SEVERITY = ("killed", "unresponsive", "err_mem", "timeout", "err_unsupported", "err_other", "ok_mismatch", "ok")


@dataclass
class QueryStats:
    engine: str
    suite: str
    query_id: str
    outcome: str                   # `ok`, or the most frequent non-ok outcome of its cells
    cells: int
    outcomes: dict = field(default_factory=dict)  # outcome -> cells
    samples: list[float] = field(default_factory=list)  # seconds the median is taken over
    hot: bool = True               # samples are hot runs (False: iteration 0 only)
    median_s: float | None = None
    min_s: float | None = None
    max_s: float | None = None
    cold_s: float | None = None    # median of iteration-0 runs
    cv: float | None = None
    error_line: str | None = None  # first error of a non-ok cell

    @property
    def key(self) -> str:
        return f"{self.suite}/{self.query_id}"

    @property
    def completed(self) -> bool:
        return self.outcome in ("ok", "ok_mismatch")

    @property
    def noisy(self) -> bool:
        return self.cv is not None and self.cv > CV_FLAG

    def to_dict(self) -> dict:
        d = asdict(self)
        d["samples"] = [round(s, 4) for s in self.samples]
        d["noisy"] = self.noisy
        return d


def cv(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = statistics.fmean(values)
    return statistics.stdev(values) / mean if mean > 0 else None


def query_outcome(counts: Counter) -> str:
    bad = {o: n for o, n in counts.items() if o not in ("ok", "ok_mismatch")}
    if not bad:
        return "ok_mismatch" if counts.get("ok_mismatch") else "ok"
    rank = {o: i for i, o in enumerate(SEVERITY)}
    return max(bad, key=lambda o: (bad[o], -rank.get(o, len(SEVERITY))))


def per_query(rows: list[dict]) -> dict[tuple[str, str, str], QueryStats]:
    """(engine, suite, query_id) -> QueryStats. Rows may carry an effective outcome."""
    groups: dict[tuple[str, str, str], list[dict]] = {}
    for r in rows:
        groups.setdefault((r["engine"], r["suite"], r["query_id"]), []).append(r)
    out = {}
    for key, cells in sorted(groups.items()):
        counts = Counter(c["outcome"] for c in cells)
        ok = [c for c in cells if c["outcome"] in ("ok", "ok_mismatch") and c.get("wall_ms") is not None]
        hot = [c["wall_ms"] / 1000 for c in ok if c["iteration"] > 0]
        cold = [c["wall_ms"] / 1000 for c in ok if c["iteration"] == 0]
        samples = hot or cold
        failed = next((c for c in cells if c["outcome"] not in ("ok", "ok_mismatch")), None)
        q = QueryStats(*key, query_outcome(counts), len(cells), dict(counts), samples, bool(hot),
                       error_line=(failed or {}).get("error_line"))
        if samples:
            q.median_s, q.min_s, q.max_s = statistics.median(samples), min(samples), max(samples)
            q.cv = cv(samples)
        if cold:
            q.cold_s = statistics.median(cold)
        out[key] = q
    return out


def geomean(values: list[float]) -> float | None:
    vals = [v for v in values if v and v > 0]
    return math.exp(statistics.fmean(math.log(v) for v in vals)) if vals else None


def percentile(sorted_values: list[float], p: float) -> float:
    """Linear interpolation between closest ranks (p in [0, 1])."""
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = p * (len(sorted_values) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def bootstrap(pairs: list[tuple[list[float], list[float]]], n: int = BOOTSTRAP_N,
              seed: int = BOOTSTRAP_SEED) -> dict:
    """95% CIs of geomean(median(a)/median(b)) and sum(median a)/sum(median b).

    Each resample draws every query's runs with replacement (per side, same count).
    """
    pairs = [(a, b) for a, b in pairs if a and b]
    if not pairs:
        return {"geomean_ci": None, "total_ratio_ci": None}
    rng = random.Random(seed)
    geo, tot = [], []
    for _ in range(n):
        logs, sa, sb = 0.0, 0.0, 0.0
        for a, b in pairs:
            ma = statistics.median(rng.choices(a, k=len(a)))
            mb = statistics.median(rng.choices(b, k=len(b)))
            logs += math.log(ma / mb)
            sa += ma
            sb += mb
        geo.append(math.exp(logs / len(pairs)))
        tot.append(sa / sb)
    geo.sort()
    tot.sort()
    return {"geomean_ci": [percentile(geo, 0.025), percentile(geo, 0.975)],
            "total_ratio_ci": [percentile(tot, 0.025), percentile(tot, 0.975)]}


@dataclass
class SuiteSummary:
    suite: str
    engines: list[str]
    queries: int                     # distinct queries of the suite in the run
    common: list[str]                # queries every engine completed
    totals: dict = field(default_factory=dict)        # engine -> sum of medians over common (s)
    cold_totals: dict = field(default_factory=dict)   # engine -> sum of cold medians over common (s)
    failures: dict = field(default_factory=dict)      # engine -> {query: outcome}
    ratio_total: dict = field(default_factory=dict)   # engine -> reference total / engine total
    geomean: dict = field(default_factory=dict)       # engine -> geomean of reference/engine
    geomean_ci: dict = field(default_factory=dict)
    total_ratio_ci: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def suite_summaries(stats: dict[tuple[str, str, str], QueryStats], reference: str = REFERENCE,
                    seed: int = BOOTSTRAP_SEED) -> list[SuiteSummary]:
    engines = sorted({k[0] for k in stats}, key=lambda e: (e != reference, e))
    out = []
    for suite in sorted({k[1] for k in stats}):
        qids = sorted({k[2] for k in stats if k[1] == suite})
        common = [q for q in qids
                  if all((s := stats.get((e, suite, q))) is not None and s.completed for e in engines)]
        s = SuiteSummary(suite, engines, len(qids), [f"{suite}/{q}" for q in common])
        for e in engines:
            got = [stats[(e, suite, q)] for q in common]
            s.totals[e] = sum(g.median_s for g in got)
            s.cold_totals[e] = sum(g.cold_s for g in got if g.cold_s is not None)
            s.failures[e] = {q: (st.outcome if (st := stats.get((e, suite, q))) else "not_run")
                             for q in qids if not (stats.get((e, suite, q)) and stats[(e, suite, q)].completed)}
        if reference in engines:
            for e in engines:
                if e == reference:
                    continue
                pairs = [(stats[(reference, suite, q)].samples, stats[(e, suite, q)].samples) for q in common]
                s.ratio_total[e] = s.totals[reference] / s.totals[e] if s.totals.get(e) else None
                s.geomean[e] = geomean([stats[(reference, suite, q)].median_s / stats[(e, suite, q)].median_s
                                        for q in common])
                ci = bootstrap(pairs, seed=seed)
                s.geomean_ci[e], s.total_ratio_ci[e] = ci["geomean_ci"], ci["total_ratio_ci"]
        out.append(s)
    return out


def ratios(stats: dict[tuple[str, str, str], QueryStats], engine: str,
           reference: str = REFERENCE) -> list[tuple[str, float, QueryStats, QueryStats]]:
    """(query, reference/engine ratio, ref stats, engine stats) for queries both completed,
    slowest for `engine` (smallest ratio) first."""
    out = []
    for (e, suite, q), st in stats.items():
        ref = stats.get((reference, suite, q))
        if e == engine and ref and st.completed and ref.completed and st.median_s and ref.median_s:
            out.append((f"{suite}/{q}", ref.median_s / st.median_s, ref, st))
    return sorted(out, key=lambda x: (x[1], x[0]))
