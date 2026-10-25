"""Runner guards: --resume against run.json (no engines or containers needed)."""

import functools

import pytest

from m0 import plan as plan_mod
from m0 import runner, store
from m0.paths import PLANS_DIR

PLAN = PLANS_DIR / "phase-l-smoke.yaml"


@pytest.fixture
def results(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "RunStore", functools.partial(store.RunStore, root=tmp_path))
    monkeypatch.setattr(runner, "list_runs", functools.partial(store.list_runs, tmp_path))
    monkeypatch.setattr(runner.env_local, "container_state", lambda svc: {"running": True})
    return tmp_path


def start_run(root, plan_hash: str, recorded: list[str]) -> store.RunStore:
    s = store.RunStore("r1", root)
    plan = plan_mod.load(PLAN)
    s.write_run({"run_id": "r1", "plan_hash": plan_hash, "plan": plan, "started_at": "2026-01-01"})
    for cell_id in recorded:
        s.append({"cell_id": cell_id, "engine": "trino", "outcome": "ok"})
    return s


def test_existing_run_needs_resume(results):
    start_run(results, plan_mod.plan_hash(plan_mod.load(PLAN)), [])
    with pytest.raises(runner.RunRefused, match="already exists"):
        runner.Runner(str(PLAN), run_id="r1").prepare()


def test_resume_refuses_a_changed_plan(results):
    start_run(results, "0000000000000000", [])
    with pytest.raises(runner.RunRefused, match="changed since run r1 started"):
        runner.Runner(str(PLAN), resume=True, run_id="r1").prepare()


def test_resume_skips_recorded_cells(results):
    cells = plan_mod.expand(plan_mod.load(PLAN))
    start_run(results, plan_mod.plan_hash(plan_mod.load(PLAN)), [c.cell_id for c in cells[:5]])
    r = runner.Runner(str(PLAN), resume=True)  # latest run of the plan, found through run.json
    assert r.store.run_id == "r1"
    todo = r.prepare()
    assert [c.cell_id for c in todo] == [c.cell_id for c in cells[5:]]
