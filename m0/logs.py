"""`m0 logs`: the engine container's log lines for one query's cells in a run.

Each round of the query's cells gives one time window (first `started_at` to the last
cell's end, plus a few seconds). `docker logs` only has the current container: the
runner recreates the engine at every cold start, so a window from before the current
container started is gone, and the output says so.
"""

import re
import subprocess
from datetime import UTC, datetime, timedelta

from m0 import env_local
from m0.queries import Query

BEFORE_S, AFTER_S = 1, 2
ANSI = re.compile(r"\x1b\[[0-9;]*m")
LINE_CAP = 300


def parse_time(value: str | None) -> datetime | None:
    """ISO 8601 / Docker RFC 3339 (nanoseconds) to an aware datetime."""
    if not value or value.startswith("0001-"):
        return None
    v = value.replace("Z", "+00:00")
    m = re.match(r"(.*T\d\d:\d\d:\d\d)(\.\d+)?(.*)", v)
    if m:
        frac = (m.group(2) or "")[:7]
        v = m.group(1) + frac + (m.group(3) or "")
    dt = datetime.fromisoformat(v)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def windows(rows: list[dict], engine: str, q: Query) -> list[dict]:
    """One window per round: {round, start, end, cells, query_ids}."""
    out: dict[int, dict] = {}
    for r in rows:
        if r["engine"] != engine or r["suite"] != q.suite or r["query_id"] != q.id or not r.get("started_at"):
            continue
        start = parse_time(r["started_at"])
        end = start + timedelta(milliseconds=r.get("wall_ms") or 0)
        w = out.setdefault(r["round"], {"round": r["round"], "start": start, "end": end, "cells": 0,
                                        "engine_query_ids": []})
        w["start"], w["end"] = min(w["start"], start), max(w["end"], end)
        w["cells"] += 1
        if r.get("engine_query_id"):
            w["engine_query_ids"].append(r["engine_query_id"])
    for w in out.values():
        w["start"] -= timedelta(seconds=BEFORE_S)
        w["end"] += timedelta(seconds=AFTER_S)
    return [out[k] for k in sorted(out)]


def docker_logs(container_id: str, since: datetime, until: datetime) -> list[str]:
    fmt = "%Y-%m-%dT%H:%M:%S.%fZ"
    res = subprocess.run(["docker", "logs", "--timestamps", "--since", since.astimezone(UTC).strftime(fmt),
                          "--until", until.astimezone(UTC).strftime(fmt), container_id],
                         capture_output=True, text=True, timeout=60, check=False)
    lines = ANSI.sub("", res.stdout + res.stderr).splitlines()
    return sorted(lines)  # stdout and stderr interleaved by their timestamp prefix


def collect(rows: list[dict], engine: str, service: str, q: Query, grep: str | None, tail: int) -> dict:
    ws = windows(rows, engine, q)
    state = env_local.container_state(service)
    started = parse_time(state.get("started_at"))
    out = {"engine": engine, "query": q.key, "container": (state.get("id") or "")[:12] or None,
           "container_started_at": state.get("started_at"), "windows": [], "lines": [], "matched": 0}
    pattern = re.compile(grep, re.I) if grep else None
    picked: list[str] = []
    for w in ws:
        info = {"round": w["round"], "start": w["start"].isoformat(), "end": w["end"].isoformat(),
                "cells": w["cells"], "engine_query_ids": w["engine_query_ids"], "status": "ok"}
        if state.get("id") is None:
            info["status"] = "gone: no container"
        elif started and started > w["end"]:
            info["status"] = f"gone: container recreated at {started.isoformat(timespec='seconds')}"
        else:
            if started and started > w["start"]:
                info["status"] = f"partial: container started at {started.isoformat(timespec='seconds')}"
            lines = docker_logs(state["id"], w["start"], w["end"])
            info["lines"] = len(lines)
            if pattern:
                lines = [ln for ln in lines if pattern.search(ln)]
            picked += lines
        out["windows"].append(info)
    out["matched"] = len(picked)
    out["lines"] = [ln[:LINE_CAP] for ln in picked[-tail:]] if tail > 0 else []
    return out


def render(r: dict) -> list[str]:
    lines = [f"{r['engine']} {r['query']}: container {r['container'] or '-'} "
             f"(started {r['container_started_at'] or '-'})"]
    for w in r["windows"]:
        ids = ", ".join(w["engine_query_ids"][:3]) + (" …" if len(w["engine_query_ids"]) > 3 else "")
        lines.append(f"  round {w['round']}: {w['start'][11:19]}-{w['end'][11:19]} UTC, {w['cells']} cells, "
                     f"{w['status']}" + (f", {w['lines']} lines" if "lines" in w else "") + f"; query ids {ids}")
    lines.append(f"-- {r['matched']} lines match, last {len(r['lines'])} shown")
    return lines + r["lines"]
