"""Outcome classification (methodology section 7), from signals and `classify_rules.yaml`."""

import re
from dataclasses import dataclass, field
from functools import cache

import yaml

from m0.paths import CLASSIFY_RULES

OUTCOMES = ("ok", "ok_mismatch", "err_mem", "err_unsupported", "err_other", "killed", "timeout", "unresponsive")
FAILED = frozenset(OUTCOMES) - {"ok"}


@dataclass
class EngineError:
    """An error the engine reported for a query."""

    name: str | None = None       # Trino errorCode.name, e.g. EXCEEDED_LOCAL_MEMORY_LIMIT
    type: str | None = None       # Trino errorType, e.g. INSUFFICIENT_RESOURCES
    code: int | None = None
    message: str = ""
    exception: str | None = None  # engine-side exception class


@dataclass
class Signals:
    engine: str
    error: EngineError | None = None
    client_error: str | None = None  # harness-side failure talking to the engine (no engine error)
    timed_out: bool = False
    oom_kills: int = 0               # memory.events oom_kill delta during the cell
    container_oom_killed: bool = False
    container_running: bool = True
    container_exit_code: int | None = None
    healthy_after: bool = True


@dataclass
class Classification:
    outcome: str
    error_class: str | None = None  # rule id or signal name
    error_line: str | None = None
    unrecognised: bool = False
    extra: dict = field(default_factory=dict)


@cache
def load_rules(path=CLASSIFY_RULES) -> dict:
    with open(path) as f:
        data = yaml.safe_load(f)
    for rule in data["rules"]:
        if rule["outcome"] not in OUTCOMES:
            raise ValueError(f"rule {rule['id']}: unknown outcome {rule['outcome']}")
    return data


def first_line(text: str | None, limit: int = 300) -> str:
    line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    return line[:limit]


def error_line(err: EngineError) -> str:
    prefix = f"{err.name}: " if err.name else ""
    return (prefix + first_line(err.message))[:300]


def match_rule(engine: str, err: EngineError, rules: dict) -> dict | None:
    for rule in rules["rules"]:
        if rule.get("engine") not in (None, engine):
            continue
        checks = [("error_name", err.name, re.fullmatch), ("error_type", err.type, re.fullmatch),
                  ("message", err.message, re.search)]
        if all(pattern is None or (value is not None and fn(pattern, value))
               for key, value, fn in checks for pattern in [rule.get(key)]):
            return rule
    return None


def classify(s: Signals, rules: dict | None = None) -> Classification:
    rules = load_rules() if rules is None else rules
    killed_codes = set(rules.get("killed_exit_codes", [137]))
    detail = first_line(error_line(s.error) if s.error else s.client_error)
    if s.oom_kills > 0 or s.container_oom_killed or (
            not s.container_running and s.container_exit_code in killed_codes):
        why = (f"oom_kill +{s.oom_kills}" if s.oom_kills else "container OOMKilled" if s.container_oom_killed
               else f"container exit status {s.container_exit_code}")
        return Classification("killed", "oom_killer", f"{why}; {detail}".rstrip("; "))
    memory_codes = set(rules.get("memory_exit_codes", {}).get(s.engine, []))
    if not s.container_running and s.container_exit_code in memory_codes:
        why = f"engine exited on memory exhaustion (exit status {s.container_exit_code})"
        return Classification("killed", "memory_self_exit", f"{why}; {detail}".rstrip("; "))
    if s.timed_out:
        return Classification("timeout", "harness_timeout", detail or "harness timeout; query cancelled")
    if not s.healthy_after:
        state = "container not running" if not s.container_running else "health check failed"
        if not s.container_running and s.container_exit_code is not None:
            state += f" (exit status {s.container_exit_code})"
        return Classification("unresponsive", "health_check", f"{state}; {detail}".rstrip("; "))
    if s.error is not None:
        rule = match_rule(s.engine, s.error, rules)
        if rule is None:
            return Classification("err_other", "unrecognised", error_line(s.error), unrecognised=True)
        return Classification(rule["outcome"], rule["id"], error_line(s.error))
    if s.client_error:
        return Classification("err_other", "unrecognised", detail, unrecognised=True)
    return Classification("ok")
