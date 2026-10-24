from pathlib import Path

import pytest
import yaml

from m0.classify import EngineError, Signals, classify, load_rules

FIXTURES = yaml.safe_load((Path(__file__).parent / "fixtures" / "trino_errors.yaml").read_text())["errors"]


def err(case: dict) -> EngineError:
    return EngineError(case["error_name"], case["error_type"], case["error_code"], case["message"], case["exception"])


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_real_trino_errors(name):
    case = FIXTURES[name]
    c = classify(Signals("trino", error=err(case)))
    assert c.outcome == case["expect"], (name, c)
    assert c.unrecognised == case.get("unrecognised", False)
    assert c.error_line.startswith(case["error_name"] + ": ")


def test_rules_file_is_valid():
    rules = load_rules()
    assert 137 in rules["killed_exit_codes"]
    assert len({r["id"] for r in rules["rules"]}) == len(rules["rules"])


def test_ok():
    assert classify(Signals("trino")).outcome == "ok"


def test_oom_kill_beats_everything():
    mem = err(FIXTURES["local_mem"])
    c = classify(Signals("trino", error=mem, timed_out=True, oom_kills=1, healthy_after=False))
    assert c.outcome == "killed" and "oom_kill +1" in c.error_line


def test_container_exit_137_is_killed():
    c = classify(Signals("trino", client_error="TrinoConnectionError: failed to fetch", container_running=False,
                         container_exit_code=137, healthy_after=False))
    assert c.outcome == "killed"
    c = classify(Signals("trino", container_running=False, container_oom_killed=True, healthy_after=False))
    assert c.outcome == "killed"


def test_timeout_beats_cancel_error():
    cancelled = EngineError("USER_CANCELED", "USER_ERROR", 3, "Query was canceled by user 'm0'")
    c = classify(Signals("trino", error=cancelled, timed_out=True))
    assert c.outcome == "timeout"


def test_unresponsive_after_query():
    c = classify(Signals("trino", healthy_after=False))
    assert c.outcome == "unresponsive" and "health check failed" in c.error_line
    c = classify(Signals("trino", container_running=False, container_exit_code=3, healthy_after=False))
    assert c.outcome == "unresponsive" and "exit status 3" in c.error_line


def test_unknown_error_is_flagged():
    c = classify(Signals("trino", error=EngineError("SOMETHING_NEW", "INTERNAL_ERROR", 65536, "boom\nstack")))
    assert (c.outcome, c.error_class, c.unrecognised, c.error_line) == (
        "err_other", "unrecognised", True, "SOMETHING_NEW: boom")


def test_client_error_without_engine_error_is_unrecognised():
    c = classify(Signals("trino", client_error="ReadTimeout: x"))
    assert c.outcome == "err_other" and c.unrecognised


def test_rules_are_engine_scoped():
    e = err(FIXTURES["local_mem"])
    assert classify(Signals("df", error=e)).outcome == "err_other"
