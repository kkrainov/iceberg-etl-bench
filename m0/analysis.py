"""`m0 summarize` and `m0 compare`: a run's cells, diffs and stats as one summary.

The summary is a dict (printed whole by `--json`); `render_*` turns it into at most
40 lines. Recorded outcomes are never changed: a completed cell whose answer differs
from the reference is shown as `ok_mismatch` (effective outcome) in the summary only.
"""

import re
from collections import Counter

from m0 import differ, stats
from m0.queries import load_manifest
from m0.store import RunStore

MAX_LINES = 40
OUTCOME_ORDER = ("ok", "ok_mismatch", "err_mem", "err_unsupported", "err_other", "killed", "timeout",
                 "unresponsive")


def counts_line(counts: dict) -> str:
    order = {o: i for i, o in enumerate(OUTCOME_ORDER)}
    return ", ".join(f"{k} {counts[k]}" for k in sorted(counts, key=lambda k: order.get(k, 99)))


def short(text: str | None, width: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= width else text[: width - 3] + "..."


def signature(error_line: str | None) -> str:
    """Error line with numbers, ids and quoted names masked, for grouping."""
    s = error_line or ""
    s = re.sub(r"'[^']*'|\"[^\"]*\"", "'…'", s)
    s = re.sub(r"\b[0-9a-f]{8,}\b|\d+(\.\d+)?", "N", s)
    return short(s, 90)


def effective_rows(rows: list[dict], diffs: list[differ.Diff]) -> list[dict]:
    bad = {(d.engine, d.suite, d.query_id) for d in diffs if d.status == differ.MISMATCH}
    out = []
    for r in rows:
        if r["outcome"] == "ok" and (r["engine"], r["suite"], r["query_id"]) in bad:
            r = {**r, "outcome": "ok_mismatch"}
        out.append(r)
    return out


def group_failures(rows: list[dict]) -> list[dict]:
    """Non-ok cells (except ok_mismatch) grouped by engine, outcome and error signature."""
    groups: dict[tuple, dict] = {}
    for r in rows:
        if r["outcome"] in ("ok", "ok_mismatch"):
            continue
        key = (r["engine"], r["outcome"], signature(r.get("error_line")))
        g = groups.setdefault(key, {"engine": key[0], "outcome": key[1], "error": r.get("error_line"),
                                    "queries": [], "cells": 0, "expected": True})
        g["cells"] += 1
        q = f"{r['suite']}/{r['query_id']}"
        if q not in g["queries"]:
            g["queries"].append(q)
        g["expected"] &= r.get("expected_outcome") == r["outcome"]
    for g in groups.values():
        g["queries"].sort()
    return sorted(groups.values(), key=lambda g: (g["engine"], -len(g["queries"]), g["outcome"]))


class MergeError(ValueError):
    pass


def merged_cells(store: RunStore, others: list[RunStore]) -> tuple[list[dict], list[dict]]:
    """Cells and answers of `store` plus those of `others` (e.g. the baseline engines of
    an earlier run on the same machine and data). Each engine must come from one run."""
    rows, answers = store.rows(), store.answers()
    owner = {r["engine"]: store.run_id for r in rows}
    for o in others:
        o_rows = o.rows()
        for e in sorted({r["engine"] for r in o_rows}):
            if e in owner:
                raise MergeError(f"engine {e} is in both {owner[e]} and {o.run_id}")
            owner[e] = o.run_id
        rows += o_rows
        answers += o.answers()
    return rows, answers


def summarize(store: RunStore, seed: int = stats.BOOTSTRAP_SEED, others: list[RunStore] | None = None) -> dict:
    """Summary of one run; with `others`, their engines are added (MergeError if an engine
    is in two runs), so engines from separate runs get ratios and answer diffs."""
    others = others or []
    run = store.read_run()
    rows, answers = merged_cells(store, others)
    manifest = load_manifest()
    diffs = differ.diff_answers(answers, manifest)
    eff = effective_rows(rows, diffs)
    qstats = stats.per_query(eff)
    suites = stats.suite_summaries(qstats, seed=seed)
    engines = sorted({r["engine"] for r in rows}, key=lambda e: (e != stats.REFERENCE, e))
    plan = run.get("plan") or {}
    planned = 0
    from m0 import plan as plan_mod

    for p in [plan, *((o.read_run().get("plan") or {}) for o in others)]:
        if p:
            planned += len(plan_mod.expand(p))
    walls = [r["wall_ms"] for r in rows if r.get("wall_ms")]
    correctness = {}
    for e in engines:
        ds = [d for d in diffs if d.engine == e]
        if ds:
            rules = Counter(rule for d in ds if d.status == differ.MATCH for rule in d.rules)
            correctness[e] = {"counts": dict(Counter(d.status for d in ds)), "rules": dict(rules)}
    return {
        "run_id": store.run_id, "dir": str(store.dir), "plan": plan.get("name"), "sf": plan.get("sf"),
        "merged": [{"run_id": o.run_id, "plan": (o.read_run().get("plan") or {}).get("name"),
                    "sf": (o.read_run().get("plan") or {}).get("sf"),
                    "engines": sorted({r["engine"] for r in o.rows()})} for o in others],
        "rounds": plan.get("rounds"), "iterations": plan.get("iterations"),
        "started_at": run.get("started_at"), "finished_at": run.get("finished_at"),
        "planned": planned, "recorded": len(rows), "engines": engines,
        "query_seconds": round(sum(walls) / 1000, 1),
        "recorded_counts": {e: dict(Counter(r["outcome"] for r in rows if r["engine"] == e)) for e in engines},
        "counts": {e: dict(Counter(r["outcome"] for r in eff if r["engine"] == e)) for e in engines},
        "correctness": correctness,
        "diffs": [d.to_dict() for d in diffs],
        "suites": [s.to_dict() for s in suites],
        "queries": [q.to_dict() for q in qstats.values()],
        "slowest": {e: [{"query": q, "ratio": r, "ref_s": a.median_s, "engine_s": b.median_s,
                         "noisy": a.noisy or b.noisy}
                        for q, r, a, b in stats.ratios(qstats, e)]
                    for e in engines if e != stats.REFERENCE},
        "failures": group_failures(eff),
    }


# ---- text ----

def _fmt_ci(ci) -> str:
    return f"[{ci[0]:.2f}-{ci[1]:.2f}]" if ci else ""


def _queries_text(queries: list[str], width: int) -> str:
    """tpcds/q14a q23b ... with the suite named once per suite."""
    parts, last_suite = [], None
    for q in queries:
        suite, qid = q.split("/", 1)
        parts.append(q if suite != last_suite else qid)
        last_suite = suite
    text = " ".join(parts)
    if len(text) > width:
        cut = text[:width].rsplit(" ", 1)[0]
        text = f"{cut} +{len(queries) - len(cut.split())}"
    return text


def render_summary(s: dict, max_lines: int = MAX_LINES) -> list[str]:
    ref = stats.REFERENCE
    state = "complete" if s["recorded"] >= s["planned"] else f"incomplete ({s['recorded']}/{s['planned']} cells)"
    head = [f"run {s['run_id']} (plan {s['plan']}, sf {s['sf']}, {s['rounds']} rounds x {s['iterations']} hot): "
            f"{state}, {s['recorded']} cells, {s['query_seconds'] / 60:.1f} min of query time"]
    for m in s.get("merged") or []:
        warn = "" if m["sf"] == s["sf"] else f"; WARN sf {m['sf']} differs"
        head.append(f"  + {', '.join(m['engines'])} from run {m['run_id']} (plan {m['plan']}{warn})")
    for e in s["engines"]:
        head.append(f"  {e:<6} {counts_line(s['counts'][e])}")
    for e, c in s["correctness"].items():
        counts = ", ".join(f"{k} {c['counts'][k]}" for k in differ.STATUSES if k in c["counts"])
        rules = ", ".join(f"{k} {v}" for k, v in sorted(c["rules"].items()))
        head.append(f"  {e} vs {ref} answers: {counts}" + (f" (matches using {rules})" if rules else ""))
    head.append("suite   queries common  " + "  ".join(f"{e + ' s':>8}" for e in s["engines"])
                + "  ratio total  geomean [95% CI]   not completed")
    for su in s["suites"]:
        nc = ", ".join(f"{e} {len(su['failures'].get(e, {}))}" for e in s["engines"])
        times = "  ".join(f"{su['totals'][e]:8.1f}" for e in s["engines"])
        others = [e for e in s["engines"] if e != ref and e in su["ratio_total"]] or [None]
        for e in others:
            rt = su["ratio_total"].get(e) if e else None
            gm = su["geomean"].get(e) if e else None
            ratio = (f"{rt:5.2f} {e:<5}" if rt else " " * 11)
            geo = f"{gm:.2f} {_fmt_ci(su['geomean_ci'].get(e))}" if gm else ""
            head.append(f"{su['suite']:<7} {su['queries']:>7} {len(su['common']):>6}  {times}  {ratio}  "
                        f"{geo:<18} {nc}")
    lines = head
    footer = [f"ratio = {ref} time / engine time over common queries (above 1: engine faster); "
              f"~ = CV > {stats.CV_FLAG:.0%}. Full detail: m0 summarize {s['run_id']} --json"]

    budget = max_lines - len(lines) - len(footer)
    slow_rows = [(e, x) for e, xs in s["slowest"].items() for x in xs[:10]]
    slow_rows.sort(key=lambda ex: ex[1]["ratio"])
    mism = [d for d in s["diffs"] if d["status"] == differ.MISMATCH]
    fails = s["failures"]
    n_slow = min(len(slow_rows), 10, max(budget // 3, 3))
    rest = budget - (n_slow + 1 if n_slow else 0)
    want_m = len(mism) + 1 if mism else 0
    want_f = len(fails) + 1 if fails else 0
    take_m = min(want_m, max(rest // 2, rest - want_f))
    take_f = min(want_f, rest - take_m)
    take_m = min(want_m, rest - take_f)

    if n_slow:
        lines.append(f"slowest relative to {ref} (ratio, {ref} s, engine s):")
        for e, x in slow_rows[:n_slow]:
            lines.append(f"  {e:<5} {x['query']:<12} {x['ratio']:6.2f}  {x['ref_s']:7.2f}  {x['engine_s']:7.2f}"
                         f"{' ~' if x['noisy'] else ''}")
    if take_f:
        n_cells = sum(g["cells"] for g in fails)
        shown = fails[: take_f - 1]
        more = len(fails) - len(shown)
        lines.append(f"not ok: {n_cells} cells in {len(fails)} groups" + (f" (+{more} groups in --json)" if more else ""))
        for g in shown:
            exp = " (expected)" if g["expected"] else ""
            lines.append(f"  {g['engine']:<5} {g['outcome']:<15} {_queries_text(g['queries'], 40):<40} "
                         f"{g['cells']:>3} cells{exp}: {short(g['error'] or '-', 110)}")
    if take_m:
        shown = mism[: take_m - 1]
        more = len(mism) - len(shown)
        lines.append(f"answer mismatches vs {ref}: {len(mism)}" + (f" (+{more} in --json)" if more else ""))
        for d in shown:
            lines.append(f"  {d['engine']:<5} {d['suite'] + '/' + d['query_id']:<12} {short(d['detail'], 150)}")
    return lines + footer


# ---- compare ----

def _engine_info(run: dict, engine: str) -> dict:
    e = (run.get("engines") or {}).get(engine) or {}
    return {"config_hashes": e.get("config_hashes") or {},
            "version": ((e.get("info") or {}).get("versions") or (e.get("info") or {}).get("version"))}


def compare(a: RunStore, b: RunStore, seed: int = stats.BOOTSTRAP_SEED, noise: float = 0.10,
            pairs: list[tuple[str, str]] | None = None) -> dict:
    """Run b against run a, per engine: time ratio b/a (above 1: b is slower).

    `pairs` compares engine x of run a with engine y of run b instead (e.g. `df` of a
    baseline run with a variant `df-jo` of a later one); a and b may be the same run.
    Entries are keyed `x:y` when x and y differ."""
    ra, rb = a.read_run(), b.read_run()
    manifest = load_manifest()
    sa = stats.per_query(effective_rows(a.rows(), differ.diff_answers(a.answers(), manifest)))
    sb = stats.per_query(effective_rows(b.rows(), differ.diff_answers(b.answers(), manifest)))
    if pairs is None:
        common = sorted({k[0] for k in sa} & {k[0] for k in sb}, key=lambda e: (e != stats.REFERENCE, e))
        pairs = [(e, e) for e in common]
    out = {"a": {"run_id": a.run_id, "plan": (ra.get("plan") or {}).get("name"), "sf": (ra.get("plan") or {}).get("sf"),
                 "git": (ra.get("git") or {}).get("sha")},
           "b": {"run_id": b.run_id, "plan": (rb.get("plan") or {}).get("name"), "sf": (rb.get("plan") or {}).get("sf"),
                 "git": (rb.get("git") or {}).get("sha")},
           "engines": {}}
    images_a, images_b = ra.get("images") or {}, rb.get("images") or {}
    out["images_changed"] = sorted(k for k in set(images_a) | set(images_b)
                                   if (images_a.get(k) or {}).get("id") != (images_b.get(k) or {}).get("id"))
    for ea, eb in pairs:
        e = ea if ea == eb else f"{ea}:{eb}"
        ia, ib = _engine_info(ra, ea), _engine_info(rb, eb)
        changed = sorted(k for k in set(ia["config_hashes"]) | set(ib["config_hashes"])
                         if ia["config_hashes"].get(k) != ib["config_hashes"].get(k))
        qa_keys = {(s, q) for (x, s, q) in sa if x == ea}
        qb_keys = {(s, q) for (x, s, q) in sb if x == eb}
        both = sorted(qa_keys & qb_keys)
        done = [(s, q) for s, q in both if sa[(ea, s, q)].completed and sb[(eb, s, q)].completed]
        per = []
        for s, q in done:
            qa, qb = sa[(ea, s, q)], sb[(eb, s, q)]
            r = qb.median_s / qa.median_s if qa.median_s else None
            band = max(noise, 2 * max(qa.cv or 0, qb.cv or 0))
            per.append({"query": f"{s}/{q}", "a_s": qa.median_s, "b_s": qb.median_s, "ratio": r,
                        "beyond_noise": r is not None and abs(r - 1) > band})
        changes = [{"query": f"{s}/{q}", "a": sa[(ea, s, q)].outcome, "b": sb[(eb, s, q)].outcome}
                   for s, q in both if sa[(ea, s, q)].outcome != sb[(eb, s, q)].outcome]
        mixed = sum(sa[(ea, s, q)].hot != sb[(eb, s, q)].hot for s, q in done)
        ta = sum(sa[(ea, s, q)].median_s for s, q in done)
        tb = sum(sb[(eb, s, q)].median_s for s, q in done)
        ci = stats.bootstrap([(sb[(eb, s, q)].samples, sa[(ea, s, q)].samples) for s, q in done], seed=seed)
        out["engines"][e] = {
            "engine_a": ea, "engine_b": eb,
            "version_a": ia["version"], "version_b": ib["version"], "config_changed": changed,
            "queries_both": len(both), "completed_both": len(done), "hot_vs_first_run": mixed,
            "only_a": len(qa_keys) - len(both), "only_b": len(qb_keys) - len(both),
            "total_a": ta, "total_b": tb, "ratio_total": tb / ta if ta else None,
            "geomean": stats.geomean([p["ratio"] for p in per if p["ratio"]]),
            "geomean_ci": ci["geomean_ci"], "total_ratio_ci": ci["total_ratio_ci"],
            "queries": sorted(per, key=lambda p: -(p["ratio"] or 0)), "outcome_changes": changes,
        }
    return out


def render_compare(c: dict, max_lines: int = MAX_LINES) -> list[str]:
    lines = [f"A {c['a']['run_id']} (plan {c['a']['plan']}, sf {c['a']['sf']}, git {str(c['a']['git'])[:8]})",
             f"B {c['b']['run_id']} (plan {c['b']['plan']}, sf {c['b']['sf']}, git {str(c['b']['git'])[:8]})"]
    if c["a"]["sf"] != c["b"]["sf"]:
        lines.append("WARN  scale factors differ; times are not comparable")
    lines.append("images changed: " + (", ".join(c["images_changed"]) or "none"))
    lines.append("engine  both  done  total A s  total B s   B/A  geomean B/A [95% CI]   config changed")
    for e, x in c["engines"].items():
        if x["hot_vs_first_run"]:
            lines.append(f"WARN  {e}: {x['hot_vs_first_run']} queries compare hot runs with first runs "
                         f"(a plan without hot runs)")
    for e, x in c["engines"].items():
        rt = f"{x['ratio_total']:5.2f}" if x["ratio_total"] else "    -"
        gm = f"{x['geomean']:.2f} {_fmt_ci(x['geomean_ci'])}" if x["geomean"] else "-"
        lines.append(f"{e:<7} {x['queries_both']:>4}  {x['completed_both']:>4}  {x['total_a']:9.1f}  "
                     f"{x['total_b']:9.1f}  {rt}  {gm:<22} {', '.join(x['config_changed']) or 'none'}")
    footer = [f"B/A = B time / A time (above 1: B slower); beyond noise = |B/A - 1| > max(10%, 2 x CV). "
              f"Full detail: --json"]
    budget = max_lines - len(lines) - len(footer)
    per_engine = max(budget // max(len(c["engines"]), 1), 3)
    for e, x in c["engines"].items():
        moved = [p for p in x["queries"] if p["beyond_noise"]]
        slower = [p for p in moved if p["ratio"] > 1][:4]
        faster = sorted((p for p in moved if p["ratio"] < 1), key=lambda p: p["ratio"])[:4]
        block = [f"{e}: {len(moved)} queries moved beyond noise, {len(x['outcome_changes'])} outcome changes"]
        block += [f"  slower {p['query']:<12} {p['ratio']:5.2f}  {p['a_s']:7.2f} -> {p['b_s']:7.2f} s" for p in slower]
        block += [f"  faster {p['query']:<12} {p['ratio']:5.2f}  {p['a_s']:7.2f} -> {p['b_s']:7.2f} s" for p in faster]
        block += [f"  outcome {o['query']:<12} {o['a']} -> {o['b']}" for o in x["outcome_changes"][:4]]
        lines += block[:per_engine]
    return lines[: max_lines - len(footer)] + footer
