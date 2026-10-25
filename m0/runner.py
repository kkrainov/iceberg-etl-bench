"""`m0 run`: execute a plan's cells, measure them and record the results.

Per cell: (restart engine + drop page cache when the cold policy says so, or
after `killed`/`unresponsive`) -> start the cgroup sampler -> execute and fetch
all rows (harness clock) -> stop the sampler -> health check -> final engine
stats -> classify -> append one line to cells.jsonl (plus the answer of the first
completed run per engine x query, and diagnostics when the plan asks for them).
"""

import json
import os
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from m0 import engines, env_local, runenv
from m0 import plan as plan_mod
from m0.classify import Signals, classify
from m0.metrics import Cgroup, Sampler, cgroup_dir
from m0.queries import load_manifest, render_for
from m0.store import RunStore, list_runs, now

RESTART_AFTER = ("killed", "unresponsive")


class RunRefused(Exception):
    """Guard: the run exists without --resume, the plan changed, or a run is in progress."""


class RunPrerequisite(Exception):
    """Stack or engine not available."""


@dataclass
class RunResult:
    run_id: str
    dir: Path
    planned: int
    done: int
    executed: int
    seconds: float
    counts: dict = field(default_factory=dict)   # engine -> {outcome: n}
    failed: int = 0
    interrupted: bool = False


def default_run_id(plan: dict) -> str:
    return f"{plan['name']}-{datetime.now(UTC):%Y%m%d-%H%M%SZ}"


def resolve_run(plan: dict, run_id: str | None, resume: bool) -> RunStore:
    if run_id is None and resume:
        same = [r for r in list_runs() if r.read_run().get("plan", {}).get("name") == plan["name"]]
        if not same:
            raise RunRefused(f"--resume: no earlier run of plan {plan['name']!r}; give --run-id")
        return same[-1]
    return RunStore(run_id or default_run_id(plan))


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def lock_holder(store: RunStore) -> int | None:
    path = store.dir / "run.lock"
    try:
        pid = int(path.read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if _pid_alive(pid) else None


def summarize_rows(rows: list[dict]) -> dict:
    counts: dict = defaultdict(Counter)
    for r in rows:
        counts[r["engine"]][r["outcome"]] += 1
    return {e: dict(c) for e, c in counts.items()}


class Runner:
    def __init__(self, plan_path: str, engine_filter=None, query_filter=None, resume=False, run_id=None,
                 emit=None):
        self.plan_path = Path(plan_path)
        self.plan = plan_mod.load(self.plan_path)
        self.plan_hash = plan_mod.plan_hash(self.plan)
        self.cells = plan_mod.expand(self.plan)
        self.manifest = load_manifest()
        self.store = resolve_run(self.plan, run_id, resume)
        self.resume = resume
        self.engine_filter, self.query_filter = engine_filter, query_filter
        self.emit = emit or (lambda kind, payload: None)
        self.adapters: dict = {}
        self.answered: set = set()

    # ---- setup ----
    def prepare(self) -> list[plan_mod.Cell]:
        s = self.store
        if s.exists():
            if not self.resume:
                raise RunRefused(f"run {s.run_id} already exists; add --resume to continue it")
            if s.read_run().get("plan_hash") != self.plan_hash:
                raise RunRefused(f"plan {self.plan_path} changed since run {s.run_id} started (compare with "
                                 f"`plan` in its run.json); use a new run ID")
            if pid := lock_holder(s):
                raise RunRefused(f"run {s.run_id} is in progress (pid {pid})")
        todo_all = [c for c in self.cells if plan_mod.matches(c, self.engine_filter, self.query_filter)]
        if not todo_all:
            raise RunRefused("no cells match --engine/--query")
        missing = sorted({c.engine for c in todo_all} - set(engines.available()))
        if missing:
            raise RunPrerequisite(f"no adapter yet for engine(s) {missing}; available: {list(engines.available())}")
        for svc in ("objstore", "catalog"):
            if not env_local.container_state(svc).get("running"):
                raise RunPrerequisite(f"{svc} is not running; start the stack with `m0 env up --engine trino`")
        recorded = s.recorded()
        self.answered = s.answered()
        self.adapters = {e: engines.get(e) for e in sorted({c.engine for c in todo_all})}
        return [c for c in todo_all if c.cell_id not in recorded]

    def write_run_json(self) -> None:
        """New run: plan and environment. Resumed run: one more entry under `resumes`."""
        run = self.store.read_run()
        snapshot = runenv.collect(list(self.adapters.values()))
        if not run:
            run = {"run_id": self.store.run_id, "plan_file": str(self.plan_path), "plan_hash": self.plan_hash,
                   "plan": self.plan, "started_at": now(), "finished_at": None, "resumes": [], **snapshot}
        else:
            for name, entry in snapshot["engines"].items():
                run["engines"].setdefault(name, entry)
            run["resumes"].append({"at": now(), "git": snapshot["git"], "images": snapshot["images"],
                                   "containers": snapshot["containers"]})
        self.store.write_run(run)

    def check_namespaces(self, todo: list[plan_mod.Cell]) -> None:
        """Every namespace the cells need exists in the Iceberg catalog (engine-neutral)."""
        needed = sorted({f"{ns}_sf{self.plan['sf']}" for c in todo
                         for ns in self.manifest[f"{c.suite}/{c.query_id}"].namespaces})
        try:
            have = env_local.namespaces()
        except Exception as e:  # catalog.CatalogError, connection errors
            raise RunPrerequisite(f"cannot list namespaces from the Iceberg catalog: {e}") from e
        if missing := [n for n in needed if n not in have]:
            raise RunPrerequisite(f"missing namespaces {missing}; run `m0 data gen` for them")

    def record_engine_info(self, eng) -> None:
        """Engine-reported version and settings into run.json, once the engine is up."""
        run = self.store.read_run()
        entry = run.get("engines", {}).get(eng.name)
        if entry is not None and (not entry.get("info") or "error" in entry["info"]):
            entry["info"] = eng.describe()
            self.store.write_run(run)

    # ---- execution ----
    def run(self) -> RunResult:
        start = time.monotonic()
        todo = self.prepare()
        s = self.store
        s.dir.mkdir(parents=True, exist_ok=True)
        (s.dir / "run.lock").write_text(str(os.getpid()))
        executed, interrupted = 0, False
        try:
            if todo:
                self.write_run_json()
            self.emit("start", {"run_id": s.run_id, "todo": len(todo), "planned": len(self.cells), "dir": str(s.dir)})
            if todo:
                self.check_namespaces(todo)
                self.adapters[todo[0].engine].start()
            prev_block, restart_reason = None, None
            for cell in todo:
                eng = self.adapters[cell.engine]
                events, dropped = [], False
                # A block's first executed cell is always cold, also when --query/--resume
                # skipped the cell the plan marked; a restart after a failure is not.
                cold = cell.restart_before or (cell.block != prev_block and cell.iteration == 0)
                if cold or restart_reason or cell.block != prev_block:
                    why = f"after {restart_reason}" if restart_reason else "cold run" if cold else "new block"
                    restart_s = eng.restart()
                    events.append({"event": "restart", "reason": why, "s": round(restart_s, 1)})
                    self.record_engine_info(eng)
                    if restart_reason:
                        self.emit("event", f"restarted {eng.name} after {restart_reason} ({restart_s:.0f}s)")
                    if cold:
                        drop_s = env_local.drop_page_cache()
                        dropped = True
                        events.append({"event": "cache_drop", "s": round(drop_s, 1)})
                    restart_reason = None
                row = self.execute_cell(cell, eng, events, dropped)
                executed += 1
                prev_block = cell.block
                if row["outcome"] != "ok":
                    self.emit("cell", row)
                if row["outcome"] in RESTART_AFTER:
                    restart_reason = row["outcome"]
                elif row["outcome"] == "timeout" and eng.busy():
                    # The cancelled query still holds CPU or memory (e.g. a DataFusion
                    # operator in a long loop without an await point): don't measure the
                    # next cells on a busy engine.
                    restart_reason = "timeout (engine still busy after cancel)"
        except KeyboardInterrupt:
            interrupted = True
        finally:
            run = s.read_run()
            if run and executed:
                run["finished_at"] = now()
                s.write_run(run)
            (s.dir / "run.lock").unlink(missing_ok=True)
        rows = s.rows()
        return RunResult(s.run_id, s.dir, len(self.cells), len(rows), executed, time.monotonic() - start,
                         summarize_rows(rows), sum(r["outcome"] != "ok" for r in rows), interrupted)

    def execute_cell(self, cell: plan_mod.Cell, eng, events: list[dict], dropped: bool) -> dict:
        s, plan = self.store, self.plan
        q = self.manifest[f"{cell.suite}/{cell.query_id}"]
        sql = render_for(q, plan["sf"], cell.engine)
        eng_state = env_local.container_state(eng.service)
        obj_state = env_local.container_state("objstore")
        sampler = Sampler({"engine": Cgroup(cgroup_dir(eng_state["id"]) if eng_state.get("id") else None),
                           "objstore": Cgroup(cgroup_dir(obj_state["id"]) if obj_state.get("id") else None)})
        started_at = now()
        sampler.start()
        res = eng.execute(sql, timeout_s=cell.timeout_s or float(plan["timeout_s"]))
        m = sampler.stop()

        after = env_local.container_state(eng.service)
        running = bool(after.get("running")) and after.get("id") == eng_state.get("id")
        healthy = running and eng.health()
        stats = eng.stats(res.engine_query_id) if healthy and res.engine_query_id else None
        cls = classify(Signals(
            engine=eng.name, error=res.error, client_error=res.client_error, timed_out=res.timed_out,
            oom_kills=m["engine"].oom_kills, container_oom_killed=bool(after.get("oom_killed")) and not running,
            container_running=running, container_exit_code=after.get("exit_code") if not running else None,
            healthy_after=healthy))
        if plan_mod.wants_diag(plan, cell):
            d = s.diag_dir(cell.cell_id)
            (d / "query.sql").write_text(sql + "\n")
            (d / "stats.json").write_text(json.dumps(stats, indent=1))
            sampler.write_timeline(d / "memory.csv")

        row = {
            "cell_id": cell.cell_id, "seq": cell.seq, "block": cell.block, "round": cell.round,
            "engine": cell.engine, "mem_level": cell.mem_level, "suite": cell.suite, "query_id": cell.query_id,
            "iteration": cell.iteration, "cold": cell.iteration == 0 and dropped, "events": events,
            "started_at": started_at, "wall_ms": round(res.wall_ms, 3) if res.wall_ms is not None else None,
            "cpu_s": m["engine"].cpu_s, **eng.metrics(stats),
            "peak_mem_bytes": m["engine"].peak_bytes, "peak_mem_source": m["engine"].peak_source,
            "mem_limit_bytes": eng_state.get("mem_limit"), "pool_limit_bytes": eng.pool_limit_bytes(),
            "objstore_peak_mem_bytes": m["objstore"].peak_bytes, "oom_kills": m["engine"].oom_kills,
            "outcome": cls.outcome, "expected_outcome": cell.expect, "error_class": cls.error_class,
            "error_line": cls.error_line, "unrecognised": cls.unrecognised,
            "engine_query_id": res.engine_query_id, "rows": res.row_count, "answer": None,
        }
        key = (cell.engine, cell.suite, cell.query_id)
        if res.completed and key not in self.answered:
            row["answer"] = s.write_answer(row, res.columns, res.rows, int(plan["result_rows_cap"]))
            self.answered.add(key)
        s.append(row)
        return row
