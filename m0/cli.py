"""`m0` command-line entry point.

Every command prints a short summary (or JSON with --json) and exits with a
code from m0.output.Exit. Only implemented commands are registered, so
`m0 --help` is always the truth about what exists.
"""

import argparse
import sys
import time
from dataclasses import asdict

from m0 import datagen, doctor, env_local
from m0.output import (
    FAIL,
    OK,
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
    p.add_argument("action", choices=["gen", "status"])
    p.add_argument("--suite", choices=datagen.SUITES, help="tpch, tpcds or etl (gen: required)")
    p.add_argument("--sf", type=int, help="scale factor, one of those in datagen/expected.yaml (gen: required)")
    p.add_argument("--force", action="store_true", help="with gen: regenerate tables that already validate")
    p.set_defaults(func=cmd_data)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
