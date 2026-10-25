"""Result store: `results/<run-id>/` (docs/framework-design.md, "Results").

  run.json       what and how: plan (file, hash, resolved settings), git, pinned versions,
                 image digests, engine settings and config hashes, host, start/end, resumes
  cells.jsonl    one JSON line per finished cell, appended (the only measurements file)
  answers.jsonl  result rows of the first completed run per engine x query (capped)
  diag/<cell_id>/  query.sql, stats.json, memory.csv; only for cells the plan's `diag` selects

Planned cells aren't stored: `m0.plan.expand(run.json["plan"])` gives them again.
"""

import fnmatch
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from m0.paths import RESULTS_DIR

ANSWER_BYTES_CAP = 64 * 1024**2
# Directories under results/ that hold other artifacts; never listed or pruned as runs.
NOT_RUNS = ("datagen", "verify")
KEEP_FILE = "keep.txt"

# One cells.jsonl line (docs/framework-design.md, "Results" has the meaning of each).
FIELDS = (
    # identity
    "cell_id", "seq", "block", "round", "engine", "mem_level", "suite", "query_id", "iteration",
    # cold protocol and events before the cell (restart, cache_drop)
    "cold", "events",
    # time (harness wall clock, cgroup CPU, engine-reported numbers and time breakdown)
    "started_at", "wall_ms", "cpu_s", "engine_elapsed_ms", "engine_cpu_s", "planning_ms", "scan_ms",
    "exchange_ms", "compute_ms",
    # memory
    "peak_mem_bytes", "peak_mem_source", "engine_peak_mem_bytes", "mem_limit_bytes", "pool_limit_bytes",
    "objstore_peak_mem_bytes", "oom_kills",
    # outcome
    "outcome", "expected_outcome", "error_class", "error_line", "unrecognised", "engine_query_id",
    # result
    "rows", "answer",
)


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text().splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:  # torn last line after a hard kill
                continue
    return out


def append_jsonl(path: Path, record: dict) -> None:
    with path.open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")
        f.flush()
        os.fsync(f.fileno())


class RunStore:
    def __init__(self, run_id: str, root: Path = RESULTS_DIR):
        self.run_id = run_id
        self.dir = root / run_id

    # ---- paths ----
    @property
    def jsonl(self) -> Path:
        return self.dir / "cells.jsonl"

    @property
    def answers_path(self) -> Path:
        return self.dir / "answers.jsonl"

    def diag_dir(self, cell_id: str) -> Path:
        d = self.dir / "diag" / cell_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def exists(self) -> bool:
        return (self.dir / "run.json").is_file()

    # ---- run.json ----
    def read_run(self) -> dict:
        path = self.dir / "run.json"
        return json.loads(path.read_text()) if path.is_file() else {}

    def write_run(self, data: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / ".run.json.tmp"
        tmp.write_text(json.dumps(data, indent=2, default=str))
        tmp.replace(self.dir / "run.json")

    # ---- cells ----
    def rows(self) -> list[dict]:
        return read_jsonl(self.jsonl)

    def recorded(self) -> set[str]:
        return {r["cell_id"] for r in self.rows()}

    def append(self, row: dict) -> None:
        if unknown := set(row) - set(FIELDS):
            raise ValueError(f"fields not in FIELDS: {sorted(unknown)}")
        append_jsonl(self.jsonl, {k: row.get(k) for k in FIELDS})

    # ---- answers ----
    def answers(self) -> list[dict]:
        return read_jsonl(self.answers_path)

    def answered(self) -> set[tuple[str, str, str]]:
        return {(a["engine"], a["suite"], a["query_id"]) for a in self.answers()}

    def write_answer(self, cell: dict, columns: list[tuple[str, str]], rows: list[list], max_rows: int) -> str:
        """Append one answer (rows already canonical JSON); returns 'full' or 'truncated'."""
        kept, size = [], 0
        for r in rows[:max_rows]:
            size += len(json.dumps(r, default=str))
            if size > ANSWER_BYTES_CAP:
                break
            kept.append(r)
        status = "full" if len(kept) == len(rows) else "truncated"
        append_jsonl(self.answers_path, {
            "cell_id": cell["cell_id"], "engine": cell["engine"], "suite": cell["suite"],
            "query_id": cell["query_id"], "columns": [{"name": n, "type": t} for n, t in columns],
            "row_count": len(rows), "truncated": status == "truncated", "rows": kept})
        return status


# ---- runs under results/ ----

def run_dirs(root: Path = RESULTS_DIR) -> list[Path]:
    """Run directories: anything with run.json, cells.jsonl or an old-format plan.yaml."""
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and p.name not in NOT_RUNS
                  and any((p / f).is_file() for f in ("run.json", "cells.jsonl", "plan.yaml")))


def list_runs(root: Path = RESULTS_DIR) -> list[RunStore]:
    """Runs in the current format, ordered by start time (oldest first)."""
    runs = [RunStore(p.name, root) for p in run_dirs(root) if (p / "run.json").is_file()]
    return sorted(runs, key=lambda r: (r.read_run().get("started_at") or "", r.run_id))


def keep_patterns(root: Path = RESULTS_DIR) -> list[str]:
    """Run IDs or glob patterns in results/keep.txt (one per line, `#` comments)."""
    path = root / KEEP_FILE
    if not path.is_file():
        return []
    lines = (line.split("#", 1)[0].strip() for line in path.read_text().splitlines())
    return [line for line in lines if line]


def is_kept(run_id: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(run_id, p) for p in patterns)


def dir_bytes(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def delete_run(path: Path, root: Path = RESULTS_DIR) -> None:
    if path.parent != root or path.name in NOT_RUNS:
        raise ValueError(f"not a run directory: {path}")
    shutil.rmtree(path)
