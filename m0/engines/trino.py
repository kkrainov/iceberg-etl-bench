"""Trino adapter: the `trino` client over HTTP, final stats from `/v1/query/{id}`."""

import json
import re
import threading
import time
import urllib.error
import urllib.request

from m0 import env_local
from m0.classify import EngineError
from m0.engines.base import Engine, ExecResult
from m0.paths import CONFIGS_DIR

HOST, PORT, USER, SOURCE = "127.0.0.1", 8080, "m0", "m0-bench"
BASE_URL = f"http://{HOST}:{PORT}"
CONFIG_DIR = CONFIGS_DIR / "trino" / "local"
READY_TIMEOUT_S = 180
CANCEL_GRACE_S = 30

_DURATION = {"ns": 1e-6, "us": 1e-3, "ms": 1.0, "s": 1e3, "m": 60e3, "h": 3600e3, "d": 86400e3}
_SIZE = {"B": 1, "kB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4, "PB": 1024**5}

# queryStats fields kept in the per-cell artifact (the full query info is ~250 KB).
KEEP_STATS = (
    "createTime", "executionStartTime", "endTime", "elapsedTime", "queuedTime", "resourceWaitingTime",
    "executionTime", "analysisTime", "planningTime", "planningCpuTime", "finishingTime",
    "totalTasks", "failedTasks", "totalDrivers", "cumulativeUserMemory",
    "peakUserMemoryReservation", "peakRevocableMemoryReservation", "peakTotalMemoryReservation",
    "peakTaskUserMemory", "peakTaskTotalMemory", "spilledDataSize",
    "totalScheduledTime", "totalCpuTime", "failedCpuTime", "totalBlockedTime",
    "physicalInputDataSize", "physicalInputPositions", "physicalInputReadTime",
    "internalNetworkInputDataSize", "processedInputDataSize", "processedInputPositions",
    "outputDataSize", "outputPositions", "physicalWrittenDataSize",
)


def parse_duration_ms(value: str | None) -> float | None:
    """Airlift Duration ('1.23s', '250.00ms', '1.00m') to milliseconds."""
    if value is None:
        return None
    m = re.fullmatch(r"([\d.]+)\s*([a-z]+)", str(value).strip())
    return float(m.group(1)) * _DURATION[m.group(2)] if m and m.group(2) in _DURATION else None


def parse_size_bytes(value: str | None) -> int | None:
    """Airlift DataSize ('6535043B', '4.00GB', '13.01kB'; binary units) to bytes."""
    if value is None:
        return None
    m = re.fullmatch(r"([\d.]+)\s*([kMGTP]?B)", str(value).strip())
    return round(float(m.group(1)) * _SIZE[m.group(2)]) if m else None


def read_properties(path) -> dict[str, str]:
    props = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            props[k.strip()] = v.strip()
    return props


def trim_failure(info: dict | None, depth: int = 0) -> dict | None:
    if not info:
        return None
    out = {"type": info.get("type"), "message": (info.get("message") or "")[:2000]}
    if info.get("errorCode"):
        out["errorCode"] = info["errorCode"]
    if info.get("cause") and depth < 5:
        out["cause"] = trim_failure(info["cause"], depth + 1)
    return out


def trim_query_info(info: dict) -> dict:
    stats = info.get("queryStats") or {}
    session = info.get("session") or {}
    return {
        "queryId": info.get("queryId"),
        "state": info.get("state"),
        "errorType": info.get("errorType"),
        "errorCode": info.get("errorCode"),
        "failureInfo": trim_failure(info.get("failureInfo")),
        "queryStats": {k: stats.get(k) for k in KEEP_STATS if k in stats},
        "systemProperties": session.get("systemProperties"),
        "retryPolicy": info.get("retryPolicy"),
        "version": info.get("version"),
    }


class TrinoEngine(Engine):
    name = "trino"
    service = "trino"

    # ---- lifecycle ----

    def start(self) -> None:
        if not env_local.container_state(self.service).get("running"):
            self.restart()
        else:
            self.wait_ready()

    def restart(self) -> float:
        start = time.monotonic()
        env_local.restart_engine(self.service)
        self.wait_ready()
        return time.monotonic() - start

    def stop(self) -> None:
        env_local.stop_engine(self.service)

    def _get(self, path: str, timeout: float = 10) -> tuple[int, dict | None]:
        req = urllib.request.Request(BASE_URL + path, headers={"X-Trino-User": USER})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, None
        except (OSError, ValueError):
            return 0, None

    def health(self) -> bool:
        status, info = self._get("/v1/info", timeout=5)
        if status != 200 or not info or info.get("starting", True):
            return False
        try:
            res = self.execute("SELECT 1", timeout_s=30)
            return res.rows == [[1]]
        except Exception:
            return False

    def wait_ready(self, timeout_s: float = READY_TIMEOUT_S) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.health():
                return
            time.sleep(1)
        raise env_local.EnvError(f"Trino not ready after {timeout_s:.0f}s")

    # ---- queries ----

    def _connect(self, session: dict | None):
        import trino

        return trino.dbapi.connect(host=HOST, port=PORT, user=USER, source=SOURCE, catalog="iceberg",
                                   session_properties=session or {})

    def execute(self, sql: str, timeout_s: float, session: dict | None = None) -> ExecResult:
        import trino

        result = ExecResult(wall_ms=None)
        state: dict = {}

        def work():
            cur, t0 = None, None
            try:
                cur = self._connect(session).cursor()
                state["cursor"] = cur
                t0 = time.perf_counter()
                cur.execute(sql)
                rows = cur.fetchall()
                result.wall_ms = (time.perf_counter() - t0) * 1000
                result.rows = rows
                result.columns = [(d[0], d[1]) for d in (cur.description or [])]
            except trino.exceptions.TrinoQueryError as e:
                result.wall_ms = (time.perf_counter() - t0) * 1000 if t0 else None
                result.error = EngineError(e.error_name, e.error_type, e.error_code, e.message or "",
                                           e.error_exception)
                result.engine_query_id = e.query_id
            except Exception as e:  # connection refused/reset, engine died, client bug
                result.wall_ms = (time.perf_counter() - t0) * 1000 if t0 else None
                result.client_error = f"{type(e).__name__}: {e}"[:500]
            finally:
                if cur is not None and result.engine_query_id is None:
                    result.engine_query_id = getattr(cur, "query_id", None)

        thread = threading.Thread(target=work, name="m0-trino-query", daemon=True)
        thread.start()
        thread.join(timeout_s)
        if thread.is_alive():
            result.timed_out = True
            qid = getattr(state.get("cursor"), "query_id", None)
            if qid:
                self.cancel(qid)
                result.cancelled = True
                result.engine_query_id = qid
            thread.join(CANCEL_GRACE_S)
            result.wall_ms = timeout_s * 1000
            result.rows = None
        return result

    def cancel(self, engine_query_id: str) -> None:
        req = urllib.request.Request(f"{BASE_URL}/v1/query/{engine_query_id}", method="DELETE",
                                     headers={"X-Trino-User": USER})
        try:
            urllib.request.urlopen(req, timeout=10).close()
        except (OSError, urllib.error.HTTPError):
            pass

    def stats(self, engine_query_id: str) -> dict | None:
        # The final info appears shortly after the client sees the end of the result.
        for _ in range(20):
            status, info = self._get(f"/v1/query/{engine_query_id}", timeout=30)
            if status == 200 and info and info.get("state") in ("FINISHED", "FAILED"):
                return trim_query_info(info)
            time.sleep(0.25)
        return trim_query_info(info) if status == 200 and info else None

    def metrics(self, stats: dict | None) -> dict:
        qs = (stats or {}).get("queryStats") or {}
        cpu_ms = parse_duration_ms(qs.get("totalCpuTime"))
        return {
            "engine_elapsed_ms": parse_duration_ms(qs.get("elapsedTime")),
            "engine_cpu_s": cpu_ms / 1000 if cpu_ms is not None else None,
            "engine_peak_mem_bytes": parse_size_bytes(qs.get("peakTotalMemoryReservation")),
        }

    def pool_limit_bytes(self) -> int | None:
        value = read_properties(CONFIG_DIR / "config.properties").get("query.max-memory-per-node")
        return parse_size_bytes(value) if value else None

    def config_files(self) -> list:
        return sorted(p for p in CONFIG_DIR.rglob("*") if p.is_file())

    def describe(self) -> dict:
        status, info = self._get("/v1/info", timeout=5)
        return {"version": (info or {}).get("nodeVersion", {}).get("version")} if status == 200 else {}
