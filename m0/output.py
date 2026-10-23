"""Output conventions shared by all commands: short text by default, `--json` on request."""

import json
from dataclasses import asdict, dataclass
from enum import IntEnum


class Exit(IntEnum):
    OK = 0
    HARNESS_ERROR = 1
    PREREQUISITE = 2  # something required is missing or not running
    QUERIES_FAILED = 3  # run finished, some cells failed (that is data, not an error)
    GUARD_REFUSED = 4  # budget/TTL guard or a destructive action without --yes


OK, WARN, FAIL, INFO = "ok", "warn", "fail", "info"


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""


def checks_exit(checks: list[Check]) -> Exit:
    return Exit.PREREQUISITE if any(c.status == FAIL for c in checks) else Exit.OK


def print_checks(checks: list[Check], as_json: bool, extra: dict | None = None) -> None:
    if as_json:
        payload = {"ok": checks_exit(checks) == Exit.OK, "checks": [asdict(c) for c in checks]}
        payload.update(extra or {})
        print(json.dumps(payload, indent=2))
        return
    width = max((len(c.name) for c in checks), default=0)
    for c in checks:
        print(f"{c.status.upper():<5} {c.name:<{width}}  {c.detail}")
    fails = sum(c.status == FAIL for c in checks)
    warns = sum(c.status == WARN for c in checks)
    print(f"-- {len(checks)} checks, {fails} failed, {warns} warnings")
