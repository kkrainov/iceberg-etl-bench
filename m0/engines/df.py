"""DataFusion adapter (engine `df`): HTTP to dfrunner in `single` mode.

dfrunner API (docs/framework-design.md, "dfrunner API"):
  GET    /health          200 once the catalog is connected
  GET    /status          versions, settings, pool limit / reserved / high-water mark, namespaces
  POST   /query           {"sql", "query_id"} -> 200 Arrow IPC stream, or 4xx/5xx JSON
                          {"query_id", "error": {variant, contexts, root_message, message}}
  GET    /queries/{id}    final stats: elapsed/planning/execution ms, process CPU, pool peak,
                          rows, error, operator metrics and the plan with metrics
  DELETE /queries/{id}    cancel: aborts the running execution
"""

import json
import threading
import time
import urllib.error
import urllib.request
import uuid

from m0 import env_local
from m0.classify import EngineError
from m0.engines.base import Engine, ExecResult
from m0.paths import CONFIGS_DIR, REPO_ROOT

BASE_URL = env_local.DFRUNNER_URL
CONFIG_DIR = CONFIGS_DIR / "datafusion" / "local"
CONFIG_FILE = CONFIG_DIR / "dfrunner.properties"
BUILD_FILES = ("dfrunner/Cargo.toml", "dfrunner/Cargo.lock", "dfrunner/Dockerfile")
READY_TIMEOUT_S = 180
CANCEL_GRACE_S = 30
_UNITS = {"KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4}


def parse_bytes(value: str | None) -> int | None:
    """dfrunner byte sizes: '4GiB', '512MiB' or a plain number."""
    if not value:
        return None
    v = value.strip()
    for suffix, mult in _UNITS.items():
        if v.endswith(suffix):
            return int(float(v[: -len(suffix)]) * mult)
    try:
        return int(v)
    except ValueError:
        return None


def read_properties(path) -> dict[str, str]:
    props = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            props[k.strip()] = v.strip()
    return props


def engine_error(body: dict | None, status: int) -> EngineError:
    """Structured dfrunner error -> EngineError (name = DataFusionError variant)."""
    err = (body or {}).get("error")
    if not isinstance(err, dict):
        return EngineError("HttpError", None, status, str(err or body)[:2000], None)
    contexts = err.get("contexts") or []
    return EngineError(err.get("variant"), contexts[0] if contexts else None, status,
                       (err.get("message") or err.get("root_message") or "")[:4000],
                       f"DataFusionError::{err.get('variant')}")


class DfEngine(Engine):
    name = "df"
    service = "df"

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

    def _request(self, method: str, path: str, body: dict | None = None,
                 timeout: float = 10) -> tuple[int, bytes, dict]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(BASE_URL + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers or {})

    def _get_json(self, path: str, timeout: float = 10) -> tuple[int, dict | None]:
        try:
            status, raw, _ = self._request("GET", path, timeout=timeout)
            return status, json.loads(raw) if raw else None
        except (OSError, ValueError):
            return 0, None

    def status(self) -> dict | None:
        code, body = self._get_json("/status", timeout=30)
        return body if code == 200 else None

    def health(self) -> bool:
        code, _ = self._get_json("/health", timeout=5)
        if code != 200:
            return False
        try:
            res = self.execute("SELECT 1", timeout_s=30)
            return res.as_rows() == [[1]]
        except Exception:
            return False

    def wait_ready(self, timeout_s: float = READY_TIMEOUT_S) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.health():
                return
            time.sleep(0.5)
        raise env_local.EnvError(f"dfrunner not ready after {timeout_s:.0f}s")

    # ---- queries ----

    def execute(self, sql: str, timeout_s: float, session: dict | None = None) -> ExecResult:
        import pyarrow as pa

        qid = f"m0-{uuid.uuid4().hex[:16]}"
        result = ExecResult(wall_ms=None, engine_query_id=qid)

        def work():
            t0 = time.perf_counter()
            try:
                # The socket timeout only guards a hung connection; the harness timeout
                # below cancels the query on the server.
                status, raw, _ = self._request("POST", "/query", {"sql": sql, "query_id": qid},
                                               timeout=timeout_s + CANCEL_GRACE_S + 30)
                if status == 200:
                    table = pa.ipc.open_stream(raw).read_all()
                    result.wall_ms = (time.perf_counter() - t0) * 1000
                    result.table = table
                    result.columns = [(f.name, str(f.type)) for f in table.schema]
                else:
                    result.wall_ms = (time.perf_counter() - t0) * 1000
                    try:
                        body = json.loads(raw)
                    except ValueError:
                        body = {"error": raw.decode(errors="replace")[:2000]}
                    result.error = engine_error(body, status)
            except Exception as e:  # connection refused/reset, engine died
                result.wall_ms = (time.perf_counter() - t0) * 1000
                result.client_error = f"{type(e).__name__}: {e}"[:500]

        thread = threading.Thread(target=work, name="m0-df-query", daemon=True)
        thread.start()
        thread.join(timeout_s)
        if thread.is_alive():
            result.timed_out = True
            self.cancel(qid)
            result.cancelled = True
            thread.join(CANCEL_GRACE_S)
            result.wall_ms = timeout_s * 1000
            result.table = None
        return result

    def cancel(self, engine_query_id: str) -> None:
        try:
            self._request("DELETE", f"/queries/{engine_query_id}", timeout=10)
        except OSError:
            pass

    def stats(self, engine_query_id: str) -> dict | None:
        for _ in range(20):
            code, body = self._get_json(f"/queries/{engine_query_id}", timeout=30)
            if code == 200 and body:
                return body
            if code != 202:
                return None
            time.sleep(0.25)
        return None

    def metrics(self, stats: dict | None) -> dict:
        s = stats or {}
        return {
            "engine_elapsed_ms": s.get("elapsed_ms"),
            "engine_cpu_s": s.get("process_cpu_s"),
            "engine_peak_mem_bytes": (s.get("pool") or {}).get("peak"),
        }

    def pool_limit_bytes(self) -> int | None:
        return parse_bytes(read_properties(CONFIG_FILE).get("dfrunner.memory_pool.limit"))

    def config_files(self) -> list:
        return [CONFIG_FILE, *(REPO_ROOT / f for f in BUILD_FILES)]

    def busy(self) -> bool:
        """After a cancel, the query task is aborted, but an operator inside a long CPU loop
        keeps running (and holding pool memory) until its next await point."""
        for _ in range(10):
            st = self.status()
            if st is None:
                return False
            if not st.get("running") and not (st.get("pool") or {}).get("reserved"):
                return False
            time.sleep(0.5)
        return True

    def describe(self) -> dict:
        st = self.status() or {}
        return {k: st.get(k) for k in ("mode", "versions", "settings")}
