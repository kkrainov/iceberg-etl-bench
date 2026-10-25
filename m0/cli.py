"""`m0` command-line entry point.

Every command prints a short summary (or JSON with --json) and exits with a
code from m0.output.Exit. Only implemented commands are registered, so
`m0 --help` is always the truth about what exists.
"""

import argparse
import json
import sys
import time
from dataclasses import asdict

from m0 import datagen, doctor, env_local
from m0.output import (
    FAIL,
    OK,
    WARN,
    Check,
    Exit,
    checks_exit,
    print_check,
    print_checks,
    print_footer,
)


def cmd_doctor(args: argparse.Namespace) -> Exit:
    checks = doctor.run_checks()
    print_checks(checks, args.json)
    return checks_exit(checks)


def cmd_env(args: argparse.Namespace) -> Exit:
    try:
        if args.action == "up":
            checks = env_local.up(args.engine, args.engine_mem, args.engine_cpus)
        elif args.action == "down":
            if args.purge and not args.yes:
                print("refused: --purge deletes generated data and catalog state; add --yes to confirm")
                return Exit.GUARD_REFUSED
            env_local.down(args.purge)
            checks = [Check("stack", "ok", "removed (data volumes deleted)" if args.purge else "stopped (data kept)")]
        else:
            checks = env_local.status_checks()
    except env_local.EnvError as e:
        print_checks([Check(f"env.{args.action}", FAIL, str(e))], args.json)
        return Exit.PREREQUISITE if args.action == "status" else Exit.HARNESS_ERROR
    print_checks(checks, args.json)
    return checks_exit(checks)


def cmd_data(args: argparse.Namespace) -> Exit:
    try:
        expected = datagen.load_expected()
        if args.action == "gen":
            return data_gen(args, expected)
        if args.action == "verify":
            return data_verify(args, expected)
        return data_status(args, expected)
    except datagen.Prerequisite as e:
        print_checks([Check(f"data.{args.action}", FAIL, str(e))], args.json)
        return Exit.PREREQUISITE
    except datagen.DataError as e:
        print_checks([Check(f"data.{args.action}", FAIL, str(e))], args.json)
        return Exit.HARNESS_ERROR


def data_gen(args: argparse.Namespace, expected: dict) -> Exit:
    if args.suite is None or args.sf is None:
        raise datagen.Prerequisite("`m0 data gen` needs --suite and --sf")
    suite, sf, ns = args.suite, args.sf, datagen.namespace(args.suite, args.sf)
    datagen.tables_for(expected, suite, sf)  # unknown SF -> Prerequisite
    cur = datagen.require_trino()
    datagen.check_sources(cur, suite, sf, expected)
    todo = datagen.plan(cur, suite, sf, expected, args.force)
    width = max(len(ns) + 1 + len(t) for t in todo)
    checks = []
    if any(build for _, build in todo.values()):
        need = datagen.disk_required_gib(expected, suite, sf)
        root, free = datagen.free_disk_gib()
        if free < need:
            raise datagen.Prerequisite(f"{free:.0f} GiB free at {root}; {suite} SF{sf} needs ~{need:.0f} GiB")
        checks.append(Check("disk", OK, f"{free:.0f} GiB free at {root}, ~{need:.0f} GiB needed"))
        if not args.json:
            print_check(checks[-1], width)

    def show(r: datagen.TableResult) -> None:
        if not args.json:
            print_check(datagen.to_check(ns, r), width)

    start = time.monotonic()
    results = datagen.generate(cur, suite, sf, expected, todo, on_result=show)
    elapsed = time.monotonic() - start
    manifest = datagen.write_manifest(suite, sf, results)
    built = sum(r.action in ("created", "regenerated") for r in results)
    failed = [r for r in results if not r.ok]
    summary = Check(ns, FAIL if failed else OK,
                    f"{built} built, {len(results) - built - len(failed)} skipped, {len(failed)} failed "
                    f"in {elapsed:.0f}s; manifest {manifest}")
    checks += [datagen.to_check(ns, r) for r in results] + [summary]
    if args.json:
        print_checks(checks, True, {"namespace": ns, "seconds": round(elapsed, 1),
                                    "tables": [asdict(r) for r in results], "manifest": manifest})
    else:
        print_check(summary, width)
        print_footer(checks)
    return Exit.HARNESS_ERROR if failed else Exit.OK


def data_status(args: argparse.Namespace, expected: dict) -> Exit:
    suites = [args.suite] if args.suite else list(datagen.SUITES)
    sfs = [args.sf] if args.sf is not None else datagen.scale_factors(expected)
    cur = datagen.require_trino()
    detailed = len(suites) * len(sfs) == 1
    checks, shown, tables = [], [], {}
    for suite in suites:
        for sf in sfs:
            ns = datagen.namespace(suite, sf)
            results = datagen.status(cur, suite, sf, expected)
            tables[ns] = [asdict(r) for r in results]
            table_checks = [datagen.to_check(ns, r, snapshot=True) for r in results]
            bad = [c for c in table_checks if c.status == FAIL]
            rows = sum(r.rows or 0 for r in results)
            size = datagen.human_bytes(sum(r.bytes or 0 for r in results))
            summary = Check(ns, FAIL if bad else OK,
                            f"{len(results) - len(bad)}/{len(results)} tables valid, {rows:,} rows, {size}")
            ns_checks = table_checks + [summary]
            if drift := datagen.snapshot_drift(suite, sf, results):
                ns_checks.append(datagen.drift_check(ns, drift))
            checks += ns_checks
            shown += ns_checks if detailed else [c for c in ns_checks if c.status != OK or c is summary]
    if args.json:
        print_checks(checks, True, {"tables": tables})
    else:
        print_checks(shown, False)
    return checks_exit(checks)


def data_verify(args: argparse.Namespace, expected: dict) -> Exit:
    from m0 import catalog, engines, verify

    if args.engine is None:
        raise datagen.Prerequisite("`m0 data verify` needs --engine (trino or df)")
    against = args.against or ("trino" if args.engine != "trino" else None)
    suites = [args.suite] if args.suite else list(datagen.SUITES)
    sfs = [args.sf] if args.sf is not None else datagen.scale_factors(expected)
    try:
        present = catalog.namespaces()
    except catalog.CatalogError as e:
        raise datagen.Prerequisite(f"catalog not reachable ({e}); start the stack with `m0 env up`") from e
    namespaces = [datagen.namespace(s, sf) for s in suites for sf in sfs if datagen.namespace(s, sf) in present]
    if not namespaces:
        raise datagen.Prerequisite("no generated namespaces match --suite/--sf; run `m0 data gen` first")
    eng = engines.get(args.engine)
    if not eng.health():
        raise datagen.Prerequisite(f"{args.engine} is not running or not healthy; `m0 env up --engine {args.engine}`")
    checks, report, shown = [], {}, 0
    start = time.monotonic()
    for ns in namespaces:
        got = verify.collect(eng, ns)
        errors = [t for t, v in got["tables"].items() if v["error"]]
        line = f"{len(got['tables'])} tables fingerprinted on {args.engine}"
        if errors:
            line += f", {len(errors)} failed as a whole: {', '.join(errors)}"
        ref = verify.load(against, ns) if against else None
        if against and ref is None:
            checks.append(Check(ns, WARN, f"{line}; no {against} fingerprints yet "
                                          f"(run `m0 data verify --engine {against}`)"))
            continue
        if ref is None:
            checks.append(Check(ns, FAIL if errors else OK, line))
            continue
        cmp = verify.compare(ref, got)
        report[ns] = asdict(cmp)
        status = OK if not cmp.differences and not cmp.missing_reference else FAIL
        checks.append(Check(ns, status, f"vs {against}: tables {cmp.tables_match}/{cmp.tables} match, columns "
                                        f"{cmp.columns_match}/{cmp.columns} match, {len(cmp.differences)} differences"))
        for d in cmp.differences:
            if shown >= 25:
                break
            shown += 1
            what = f"{d['table']}.{d['column']}" if d.get("column") else d["table"]
            detail = d.get("error") or f"{d['stat']}: {against} {d['ref']!r} vs {args.engine} {d['got']!r}"
            checks.append(Check(f"  {what}", FAIL, f"[{d['kind']}] {detail}"[:220]))
    report_path = None
    if report:
        report_path = verify.VERIFY_DIR / args.engine / f"vs-{against}.json"
        report_path.write_text(json.dumps(report, indent=1, default=str))
    elapsed = time.monotonic() - start
    extra = {"engine": args.engine, "against": against, "seconds": round(elapsed, 1),
             "report": str(report_path) if report_path else None, "comparisons": report}
    if args.json:
        print_checks(checks, True, extra)
    else:
        print_checks(checks, False)
        print(f"fingerprints {verify.VERIFY_DIR / args.engine}; "
              f"{'report ' + str(report_path) if report_path else 'no comparison'} ({elapsed:.0f}s)")
    if any(c.status == FAIL for c in checks):
        return Exit.QUERIES_FAILED
    return Exit.OK


def counts_line(counts: dict) -> str:
    order = ("ok", "ok_mismatch", "err_mem", "err_unsupported", "err_other", "killed", "timeout", "unresponsive")
    return ", ".join(f"{k} {counts[k]}" for k in sorted(counts, key=lambda k: order.index(k) if k in order else 99))


def cell_line(row: dict) -> str:
    wall = f"{row['wall_ms'] / 1000:.1f}s" if row.get("wall_ms") is not None else "-"
    expected = " (expected)" if row.get("expected_outcome") == row["outcome"] else ""
    line = f"{row['outcome'].upper():<15} {row['cell_id']}  {wall}  {row.get('error_line') or ''}"
    return line[:200] + expected


def cmd_run(args: argparse.Namespace) -> Exit:
    from m0 import plan as plan_mod
    from m0 import queries, runner

    def emit(kind: str, payload) -> None:
        if args.json:
            return
        if kind == "start":
            print(f"run {payload['run_id']}: {payload['todo']} cells to run of {payload['planned']} planned "
                  f"-> {payload['dir']}", flush=True)
        elif kind == "cell":
            print(cell_line(payload), flush=True)
        elif kind == "event":
            print(f"INFO  {payload}", flush=True)

    try:
        r = runner.Runner(args.plan, args.engine, args.query, args.resume, args.run_id, emit).run()
    except (plan_mod.PlanError, queries.QueryError) as e:
        print_checks([Check("run", FAIL, str(e))], args.json)
        return Exit.PREREQUISITE
    except runner.RunRefused as e:
        print_checks([Check("run", FAIL, f"refused: {e}")], args.json)
        return Exit.GUARD_REFUSED
    except (runner.RunPrerequisite, env_local.EnvError) as e:
        print_checks([Check("run", FAIL, str(e))], args.json)
        return Exit.PREREQUISITE
    code = Exit.HARNESS_ERROR if r.interrupted else Exit.QUERIES_FAILED if r.failed else Exit.OK
    if args.json:
        print(json.dumps({"run_id": r.run_id, "dir": str(r.dir), "planned": r.planned, "recorded": r.done,
                          "executed": r.executed, "seconds": round(r.seconds, 1), "counts": r.counts,
                          "failed": r.failed, "interrupted": r.interrupted, "exit": int(code)}, indent=2))
        return code
    for engine, counts in sorted(r.counts.items()):
        print(f"{engine:<8} {counts_line(counts)}")
    state = "interrupted; continue with --resume" if r.interrupted else "done"
    print(f"run {r.run_id}: {r.done}/{r.planned} cells recorded, {r.executed} run now in "
          f"{r.seconds / 60:.1f} min, {r.failed} not ok ({state})")
    print(f"results {r.dir}")
    return code


def run_state(s, rows: list[dict], planned: int) -> str:
    from m0 import runner

    pid = runner.lock_holder(s)
    return f"running (pid {pid})" if pid else "complete" if len(rows) >= planned else "incomplete"


def planned_cells(run: dict) -> int:
    from m0 import plan as plan_mod

    return len(plan_mod.expand(run["plan"])) if run.get("plan") else 0


def cmd_status(args: argparse.Namespace) -> Exit:
    from m0 import runner, store

    runs = store.list_runs()
    if args.run in (None, "latest"):
        if not runs:
            print_checks([Check("status", FAIL, "no runs under results/")], args.json)
            return Exit.PREREQUISITE
        s = runs[-1]
    else:
        s = store.RunStore(args.run)
        if not s.exists():
            print_checks([Check("status", FAIL, f"no run {args.run!r} under results/")], args.json)
            return Exit.PREREQUISITE
    rows, run = s.rows(), s.read_run()
    planned = planned_cells(run)
    state = run_state(s, rows, planned)
    failures = [r for r in rows if r["outcome"] != "ok"]
    last = failures[-1] if failures else None
    counts = runner.summarize_rows(rows)
    if args.json:
        print(json.dumps({"run_id": s.run_id, "dir": str(s.dir), "state": state, "planned": planned,
                          "recorded": len(rows), "counts": counts, "started_at": run.get("started_at"),
                          "finished_at": run.get("finished_at"), "last_failure": last}, indent=2, default=str))
        return Exit.OK
    print(f"run      {s.run_id} (plan {run.get('plan', {}).get('name')}): {state}")
    print(f"progress {len(rows)}/{planned} cells; started {run.get('started_at')}, "
          f"last end {run.get('finished_at')}")
    for engine, c in sorted(counts.items()):
        print(f"{engine:<8} {counts_line(c)}")
    print(f"last failure: {cell_line(last) if last else 'none'}")
    print(f"path     {s.dir}")
    return Exit.OK


def cmd_results(args: argparse.Namespace) -> Exit:
    from m0 import store

    root = store.RESULTS_DIR
    patterns = store.keep_patterns(root) + (args.keep or [])
    runs = []
    for d in store.run_dirs(root):
        s = store.RunStore(d.name, root)
        run, rows = s.read_run(), s.rows()
        current = bool(run)
        planned = planned_cells(run) if current else None
        runs.append({"run_id": d.name, "plan": (run.get("plan") or {}).get("name"),
                     "started_at": run.get("started_at"), "cells": len(rows), "planned": planned,
                     "state": run_state(s, rows, planned) if current else "old format",
                     "bytes": store.dir_bytes(d), "keep": store.is_kept(d.name, patterns)})
    if args.action == "list":
        if args.json:
            print(json.dumps({"dir": str(root), "keep_file": str(root / store.KEEP_FILE),
                              "runs": runs}, indent=2))
            return Exit.OK
        width = max((len(r["run_id"]) for r in runs), default=6)
        for r in runs:
            cells = f"{r['cells']}/{r['planned']}" if r["planned"] is not None else str(r["cells"])
            print(f"{'KEEP' if r['keep'] else '':<5}{r['run_id']:<{width}}  {r['state']:<20} {cells:>9} cells  "
                  f"{r['bytes'] / 1024**2:7.1f} MiB  {r['started_at'] or '-'}")
        print(f"-- {len(runs)} runs, {sum(r['keep'] for r in runs)} kept "
              f"(keep list {root / store.KEEP_FILE})")
        return Exit.OK
    # prune: every run not kept and not in progress; a dry run unless --yes
    doomed = [r for r in runs if not r["keep"] and not r["state"].startswith("running")]
    if args.yes:
        for r in doomed:
            store.delete_run(root / r["run_id"], root)
    if args.json:
        print(json.dumps({"deleted" if args.yes else "would_delete": [r["run_id"] for r in doomed],
                          "kept": [r["run_id"] for r in runs if r not in doomed]}, indent=2))
        return Exit.OK
    for r in doomed:
        print(f"{'DELETED' if args.yes else 'PRUNE':<8} {r['run_id']}  ({r['bytes'] / 1024**2:.1f} MiB)")
    size = sum(r["bytes"] for r in doomed) / 1024**2
    if args.yes:
        print(f"-- deleted {len(doomed)} runs ({size:.1f} MiB), kept {len(runs) - len(doomed)}")
    else:
        print(f"-- would delete {len(doomed)} runs ({size:.1f} MiB), keep {len(runs) - len(doomed)}; "
              f"add --yes to delete")
    return Exit.OK


def engines_available() -> tuple[str, ...]:
    from m0 import engines

    return engines.available()


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine-readable output")

    parser = argparse.ArgumentParser(prog="m0", description="iceberg-etl-bench harness")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doctor", parents=[common], help="check prerequisites and machine settings")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("env", parents=[common], help="manage the local stack (Phase L)")
    p.add_argument("action", choices=["up", "down", "status"])
    p.add_argument("--engine", choices=env_local.ENGINES, help="also start this engine (others are stopped)")
    p.add_argument("--engine-mem", default="8g", help="engine container memory limit (default 8g)")
    p.add_argument("--engine-cpus", default="12", help="engine container CPU limit (default 12)")
    p.add_argument("--purge", action="store_true", help="with down: also delete data volumes")
    p.add_argument("--yes", action="store_true", help="confirm a destructive action")
    p.set_defaults(func=cmd_env)

    p = sub.add_parser("data", parents=[common], help="generate and validate the Iceberg benchmark tables")
    p.add_argument("action", choices=["gen", "status", "verify"])
    p.add_argument("--suite", choices=datagen.SUITES, help="tpch, tpcds or etl (gen: required)")
    p.add_argument("--sf", type=int, help="scale factor, one of those in datagen/expected.yaml (gen: required)")
    p.add_argument("--force", action="store_true", help="with gen: regenerate tables that already validate")
    p.add_argument("--engine", choices=engines_available(), help="with verify: engine that reads the tables")
    p.add_argument("--against", choices=engines_available(),
                   help="with verify: reference engine (default trino unless --engine is trino)")
    p.set_defaults(func=cmd_data)

    p = sub.add_parser("run", parents=[common], help="execute a run plan and record every cell")
    p.add_argument("plan", help="plan file, e.g. plans/phase-l-smoke.yaml")
    p.add_argument("--engine", action="append", help="only cells of this engine (repeatable)")
    p.add_argument("--query", action="append", help="only this query: suite/id or id (repeatable)")
    p.add_argument("--resume", action="store_true",
                   help="continue a run (the latest of this plan, or --run-id), skipping recorded cells")
    p.add_argument("--run-id", help="run ID (default <plan>-<timestamp>)")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("status", parents=[common], help="progress and last failure of a run")
    p.add_argument("run", nargs="?", default="latest", help="run ID or latest (default)")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("results", parents=[common], help="list runs under results/, prune runs not kept")
    p.add_argument("action", choices=["list", "prune"])
    p.add_argument("--keep", action="append",
                   help="also keep this run ID or glob (repeatable; results/keep.txt lists the rest)")
    p.add_argument("--yes", action="store_true", help="with prune: delete (default: dry run)")
    p.set_defaults(func=cmd_results)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
