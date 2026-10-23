"""`m0` command-line entry point.

Every command prints a short summary (or JSON with --json) and exits with a
code from m0.output.Exit. Only implemented commands are registered, so
`m0 --help` is always the truth about what exists.
"""

import argparse
import sys

from m0 import doctor, env_local
from m0.output import FAIL, Check, Exit, checks_exit, print_checks


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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
