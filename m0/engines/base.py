"""The engine adapter interface.

Wall time is measured by the harness around `execute` plus fetching every row,
on the same code path for every engine (methodology section 6).
"""

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from decimal import Decimal

from m0.classify import EngineError


def canonical(value):
    """One JSON encoding per type, so both engines' result rows compare as JSON.

    dfrunner applies the same rules in Rust (dfrunner/src/query.rs `to_json`): integers
    and floats as numbers (NaN and infinities as strings), decimals as strings with
    their scale ("1.50"), dates "YYYY-MM-DD", timestamps "YYYY-MM-DDTHH:MM:SS.ffffff"
    (UTC with a "Z" suffix when the value has a time zone), binary as lowercase hex,
    arrays and rows as lists. Anything else becomes its string form.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        return value if math.isfinite(value) else ("Infinity" if value > 0 else "-Infinity")
    if isinstance(value, Decimal):
        return format(abs(value) if value.is_zero() else value, "f")
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        return value.strftime("%Y-%m-%dT%H:%M:%S.%f")
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    if isinstance(value, (list, tuple)):
        return [canonical(v) for v in value]
    return str(value)


@dataclass
class ExecResult:
    """One query execution. `rows` hold canonical JSON values (`canonical`), `columns`
    each engine's own type names."""

    wall_ms: float | None               # execute + fetch all rows, harness clock
    columns: list[tuple[str, str]] = field(default_factory=list)  # (name, engine type)
    rows: list[list] | None = None      # None when the query failed
    engine_query_id: str | None = None
    error: EngineError | None = None    # error reported by the engine
    client_error: str | None = None     # harness-side failure (connection lost, ...)
    timed_out: bool = False
    cancelled: bool = False

    @property
    def completed(self) -> bool:
        return self.rows is not None

    @property
    def row_count(self) -> int | None:
        return len(self.rows) if self.rows is not None else None


# Engine-reported numbers per cell (docs/framework-design.md, "Results"). The time
# breakdown is operator time summed over threads: Trino operator wall time
# (addInput + getOutput + finish), DataFusion `elapsed_compute` (plus repartition and
# send time for exchanges). None where the engine doesn't report it.
METRICS = ("engine_elapsed_ms", "engine_cpu_s", "engine_peak_mem_bytes", "planning_ms", "scan_ms",
           "exchange_ms", "compute_ms")


class Engine(ABC):
    name: str
    service: str  # compose service whose container is sampled

    @abstractmethod
    def start(self) -> None:
        """Make sure the engine is running (start it if needed)."""

    @abstractmethod
    def restart(self) -> float:
        """Recreate the engine cold and wait until it accepts queries. Returns seconds."""

    @abstractmethod
    def health(self) -> bool:
        """True if the engine answers its health endpoint and accepts queries."""

    @abstractmethod
    def execute(self, sql: str, timeout_s: float, session: dict | None = None) -> ExecResult:
        """Run one query, fetch all rows; cancel and return `timed_out` after `timeout_s`."""

    @abstractmethod
    def cancel(self, engine_query_id: str) -> None:
        """Cancel a running query."""

    @abstractmethod
    def stats(self, engine_query_id: str) -> dict | None:
        """Final engine-side stats for a query (trimmed), or None if unavailable."""

    @abstractmethod
    def metrics(self, stats: dict | None) -> dict:
        """The METRICS fields from `stats` (None where unknown)."""

    @abstractmethod
    def pool_limit_bytes(self) -> int | None:
        """Per-node query memory limit (Trino query.max-memory-per-node, DataFusion pool)."""

    @abstractmethod
    def config_files(self) -> list:
        """Config files whose hashes go into run.json."""

    @abstractmethod
    def stop(self) -> None:
        """Stop the engine."""

    def busy(self) -> bool:
        """True if the engine still works on or holds memory for a finished/cancelled query."""
        return False

    def describe(self) -> dict:
        """Engine-reported version and key settings, for run.json (best effort)."""
        return {}
