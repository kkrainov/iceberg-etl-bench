"""Run plans (`plans/*.yaml`) and the deterministic cell expander.

A cell is one execution of one query on one engine. The expander turns a plan
into an ordered list of cells; the same plan always gives the same list.

Order: rounds -> engine blocks (ABBA: engines reversed on odd rounds) -> memory
levels -> queries (shuffled with a seed per round, the same order for every
engine in that round) -> iteration 0 (first run) then `iterations` hot runs.
"""

import hashlib
import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from m0.queries import Query, load_manifest

COLD_POLICIES = ("per_query", "per_block")
ENGINES = ("trino", "df", "df-cs", "df-jo", "df-jo-cs", "df-sail", "df-sail-cs", "dfd", "dfd-jo")
DEFAULTS = {"phase": "L", "rounds": None, "mem_levels": [100], "iterations": 5,
            "cold": "per_query", "timeout_s": 600, "result_rows_cap": 100_000, "diag": False}


class PlanError(Exception):
    pass


@dataclass(frozen=True)
class Cell:
    cell_id: str
    seq: int
    block: int
    round: int
    engine: str
    mem_level: int
    suite: str
    query_id: str
    iteration: int        # 0 = first run of the query in the block; 1.. = hot
    cold: bool            # iteration 0 directly after an engine restart and page-cache drop
    restart_before: bool  # the runner restarts the engine and drops caches before this cell
    expect: str | None    # outcome the plan expects (deliberate failures); None = ok
    timeout_s: float | None = None  # per-entry timeout; None = the plan's timeout_s

    def to_dict(self) -> dict:
        return asdict(self)


def make_cell_id(engine: str, mem_level: int, rnd: int, suite: str, query_id: str, iteration: int) -> str:
    return f"{engine}.m{mem_level}.r{rnd}.{suite}.{query_id}.i{iteration}"


def load(path: str | Path) -> dict:
    path = Path(path)
    if not path.is_file():
        raise PlanError(f"plan not found: {path}")
    with path.open() as f:
        raw = yaml.safe_load(f) or {}
    plan = {**DEFAULTS, **raw}
    plan.setdefault("name", path.stem)
    validate(plan)
    if plan["rounds"] is None:
        plan["rounds"] = 2 if len(plan["engines"]) > 1 else 1
    return plan


def validate(plan: dict, manifest: dict[str, Query] | None = None) -> None:
    manifest = load_manifest() if manifest is None else manifest
    for key in ("name", "sf", "seed", "engines", "queries"):
        if plan.get(key) in (None, [], ""):
            raise PlanError(f"plan needs `{key}`")
    if unknown := [e for e in plan["engines"] if e not in ENGINES]:
        raise PlanError(f"unknown engines {unknown}; known: {list(ENGINES)}")
    if len(set(plan["engines"])) != len(plan["engines"]):
        raise PlanError("engines listed twice")
    if plan["cold"] not in COLD_POLICIES:
        raise PlanError(f"cold must be one of {COLD_POLICIES}")
    if plan["rounds"] is not None and int(plan["rounds"]) < 1:
        raise PlanError("rounds must be >= 1")
    if int(plan["iterations"]) < 0:
        raise PlanError("iterations must be >= 0")
    if any(not 0 < int(m) <= 100 for m in plan["mem_levels"]):
        raise PlanError("mem_levels are percentages in (0, 100]")
    diag = plan.get("diag")
    if diag is not None and not isinstance(diag, bool) and not (
            isinstance(diag, list) and all(isinstance(q, str) for q in diag)):
        raise PlanError("diag is true, false or a list of queries (suite/id)")
    for entry in plan["queries"]:
        for qid in entry.get("ids", []):
            if f"{entry['suite']}/{qid}" not in manifest:
                raise PlanError(f"query {entry['suite']}/{qid} is not in queries/manifest.yaml")


def query_list(plan: dict) -> list[tuple[str, str, int, str | None, float | None]]:
    """(suite, id, hot iterations, expected outcome, timeout override) in plan order."""
    out = []
    for entry in plan["queries"]:
        timeout = entry.get("timeout_s")
        for qid in entry["ids"]:
            out.append((entry["suite"], str(qid), int(entry.get("iterations", plan["iterations"])),
                        entry.get("expect"), float(timeout) if timeout is not None else None))
    if len({(q[0], q[1]) for q in out}) != len(out):
        raise PlanError("a query is listed twice")
    return out


def round_order(plan: dict, rnd: int) -> list[tuple[str, str, int, str | None, float | None]]:
    queries = sorted(query_list(plan), key=lambda q: (q[0], q[1]))
    random.Random(f"{plan['seed']}/{rnd}").shuffle(queries)
    return queries


def expand(plan: dict) -> list[Cell]:
    cells: list[Cell] = []
    block = 0
    for rnd in range(int(plan["rounds"])):
        engines = list(plan["engines"]) if rnd % 2 == 0 else list(reversed(plan["engines"]))
        order = round_order(plan, rnd)
        for engine in engines:
            for mem in plan["mem_levels"]:
                first_in_block = True
                for suite, qid, hot, expect, timeout in order:
                    for it in range(hot + 1):
                        restart = it == 0 and (plan["cold"] == "per_query" or first_in_block)
                        cells.append(Cell(make_cell_id(engine, int(mem), rnd, suite, qid, it), len(cells), block,
                                          rnd, engine, int(mem), suite, qid, it, restart, restart, expect,
                                          timeout))
                        first_in_block = False
                block += 1
    ids = [c.cell_id for c in cells]
    assert len(set(ids)) == len(ids), "cell ids must be unique"
    return cells


def plan_hash(plan: dict) -> str:
    return hashlib.sha256(json.dumps(plan, sort_keys=True, default=str).encode()).hexdigest()[:16]


def wants_diag(plan: dict, cell: Cell) -> bool:
    """Diagnostics (engine stats, memory timeline) for this cell: `diag: true` or a query list."""
    diag = plan.get("diag")
    return diag is True or (isinstance(diag, list) and matches(cell, None, diag))


def matches(cell: Cell, engines: list[str] | None, queries: list[str] | None) -> bool:
    """`--engine` / `--query` filters. A query filter is `suite/id`, `suite.id` or a bare id."""
    if engines and cell.engine not in engines:
        return False
    if queries:
        keys = {f"{cell.suite}/{cell.query_id}", f"{cell.suite}.{cell.query_id}", cell.query_id}
        return any(q in keys for q in queries)
    return True
