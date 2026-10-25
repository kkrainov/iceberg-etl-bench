"""`m0 explain`: re-run one query of a run on the live engine and save its plan with metrics.

Trino: `EXPLAIN ANALYZE` (the plan text with per-node statistics), plus the operator
summaries of that execution from `/v1/query/{id}`. DataFusion (`df`): the query runs as
in the benchmark and dfrunner's `/queries/{id}` gives the physical plan with metrics
and per-operator metrics. Files go to `results/<run-id>/diag/explain.<engine>.<suite>.<id>/`.

It is a diagnostic, never a measurement: nothing is written to cells.jsonl.
"""

import json
import statistics
import time

from m0 import engines, env_local, runenv
from m0.engines.trino import parse_duration_ms
from m0.queries import Query, load_manifest, render_for
from m0.store import RunStore


class ExplainError(Exception):
    """A prerequisite is missing (exit code 2)."""


def find_query(spec: str, manifest: dict[str, Query] | None = None) -> Query:
    """`suite/id`, `suite.id` or a bare id that only one suite has."""
    manifest = manifest if manifest is not None else load_manifest()
    key = spec.replace(".", "/", 1) if "/" not in spec else spec
    if key in manifest:
        return manifest[key]
    hits = [q for q in manifest.values() if q.id == spec]
    if len(hits) == 1:
        return hits[0]
    if hits:
        raise ExplainError(f"query {spec!r} is ambiguous: {', '.join(q.key for q in hits)}; give suite/id")
    raise ExplainError(f"no query {spec!r} in queries/manifest.yaml")


def trino_operators(info: dict) -> list[dict]:
    """Operator summaries summed per plan node and operator type, by wall time."""
    agg: dict[tuple, dict] = {}
    for op in (info.get("queryStats") or {}).get("operatorSummaries") or []:
        if op.get("operatorType") == "ExplainAnalyzeOperator":  # the EXPLAIN wrapper, not the query
            continue
        key = (op.get("planNodeId"), op.get("operatorType"))
        a = agg.setdefault(key, {"node": key[0], "op": key[1], "wall_ms": 0.0, "cpu_ms": 0.0,
                                 "input_rows": 0, "output_rows": 0})
        a["wall_ms"] += sum(parse_duration_ms(op.get(k)) or 0.0 for k in ("addInputWall", "getOutputWall", "finishWall"))
        a["cpu_ms"] += sum(parse_duration_ms(op.get(k)) or 0.0 for k in ("addInputCpu", "getOutputCpu", "finishCpu"))
        a["input_rows"] += op.get("inputPositions") or 0
        a["output_rows"] += op.get("outputPositions") or 0
    return sorted(agg.values(), key=lambda a: -a["wall_ms"])


def df_operators(stats: dict) -> list[dict]:
    """dfrunner per-operator metrics (summed over partitions), by elapsed_compute."""
    out = []
    for i, op in enumerate(((stats.get("metrics") or {}).get("operators")) or []):
        m = op.get("metrics") or {}
        out.append({"node": i, "op": op.get("op"), "depth": op.get("depth"),
                    "wall_ms": None, "cpu_ms": round(m.get("elapsed_compute", 0) / 1e6, 3),
                    "output_rows": m.get("output_rows"), "metrics": m})
    return sorted(out, key=lambda a: -(a["cpu_ms"] or 0))


def explain(store: RunStore, engine: str, q: Query, timeout_s: float | None = None) -> dict:
    run = store.read_run()
    plan = run.get("plan") or {}
    if "sf" not in plan:
        raise ExplainError(f"run {store.run_id} has no plan in run.json")
    if engine not in engines.available():
        raise ExplainError(f"no adapter for engine {engine!r}; available: {list(engines.available())}")
    eng = engines.get(engine)
    if not env_local.container_state(eng.service).get("running") or not eng.health():
        raise ExplainError(f"{engine} is not running; start it with `m0 env up --engine {engine}` "
                           f"(it stops the other engine)")
    warnings = []
    recorded = (run.get("engines") or {}).get(engine, {}).get("config_hashes") or {}
    now_hashes = runenv.engine_entry(eng)["config_hashes"]
    if drift := sorted(k for k in set(recorded) | set(now_hashes) if recorded.get(k) != now_hashes.get(k)):
        warnings.append(f"config differs from the run: {', '.join(drift)}")
    sql = render_for(q, plan["sf"], engine)
    timeout = timeout_s or float(plan.get("timeout_s") or 600)
    out_dir = store.dir / "diag" / f"explain.{engine}.{q.suite}.{q.id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "query.sql").write_text(sql + "\n")

    t0 = time.perf_counter()
    if engine == "trino":
        res = eng.execute("EXPLAIN ANALYZE " + sql, timeout_s=timeout)
        info = eng.query_info(res.engine_query_id) if res.engine_query_id else None
        plan_text = "\n".join(str(r[0]) for r in res.rows or [])
        ops = trino_operators(info or {})
        stats_doc = info and {k: info.get(k) for k in ("queryId", "state", "queryStats")}
        if stats_doc and stats_doc.get("queryStats"):
            stats_doc["queryStats"] = {k: v for k, v in stats_doc["queryStats"].items() if k != "operatorSummaries"}
    else:
        res = eng.execute(sql, timeout_s=timeout)
        st = eng.stats(res.engine_query_id) if res.engine_query_id else None
        plan_text = ((st or {}).get("metrics") or {}).get("plan") or ""
        ops = df_operators(st or {})
        stats_doc = st and {k: v for k, v in st.items() if k != "metrics"}
    wall_s = time.perf_counter() - t0
    error = (res.error.message if res.error else res.client_error) or ("timeout" if res.timed_out else None)
    (out_dir / "plan.txt").write_text(plan_text + "\n")
    (out_dir / "operators.json").write_text(json.dumps(ops, indent=1))
    (out_dir / "stats.json").write_text(json.dumps(stats_doc, indent=1, default=str))

    walls = [r["wall_ms"] / 1000 for r in store.rows() if r["engine"] == engine and r["suite"] == q.suite
             and r["query_id"] == q.id and r["outcome"] == "ok" and r.get("wall_ms") and r["iteration"] > 0]
    return {"run_id": store.run_id, "engine": engine, "query": q.key, "sf": plan["sf"], "dir": str(out_dir),
            "wall_s": round(wall_s, 3), "recorded_median_s": statistics.median(walls) if walls else None,
            "error": error, "warnings": warnings, "operators": ops,
            "files": [str(out_dir / f) for f in ("query.sql", "plan.txt", "operators.json", "stats.json")]}


def render(r: dict, top: int = 10) -> list[str]:
    rec = f", recorded hot median {r['recorded_median_s']:.2f}s" if r["recorded_median_s"] else ""
    lines = [f"{r['engine']} {r['query']} sf{r['sf']}: ran in {r['wall_s']:.2f}s{rec}"
             + (f"; ERROR {r['error'][:200]}" if r["error"] else "")]
    lines += [f"WARN  {w}" for w in r["warnings"]]
    total = sum((o["wall_ms"] if o["wall_ms"] is not None else o["cpu_ms"]) or 0 for o in r["operators"]) or 1
    key = "wall_ms" if r["engine"] == "trino" else "cpu_ms"
    lines.append(f"top operators by {'wall' if key == 'wall_ms' else 'elapsed_compute'} time (summed over "
                 f"threads), of {len(r['operators'])}:")
    for o in r["operators"][:top]:
        t = o[key] or 0
        node = f"node {o['node']}" if r["engine"] == "trino" else f"depth {o.get('depth')}"
        rows = f"{o['output_rows']:,}" if isinstance(o.get("output_rows"), int) else "-"
        lines.append(f"  {t:10.1f} ms {t / total:5.1%}  {str(o['op'])[:34]:<34} {node:<10} out rows {rows}")
    lines.append(f"plan with metrics: {r['dir']}/plan.txt (operators.json, stats.json, query.sql)")
    return lines
