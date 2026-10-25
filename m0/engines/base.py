"""The engine adapter interface.

Wall time is measured by the harness around `execute` plus fetching every row,
on the same code path for every engine (methodology section 6).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from m0.classify import EngineError

if TYPE_CHECKING:
    import pyarrow as pa


@dataclass
class ExecResult:
    """One query execution. A result is either `rows` (Trino: Python values plus engine
    type names in `columns`) or `table` (DataFusion: the Arrow table as returned)."""

    wall_ms: float | None               # execute + fetch all rows, harness clock
    columns: list[tuple[str, str]] = field(default_factory=list)  # (name, engine type)
    rows: list[list] | None = None      # None when the query failed
    table: "pa.Table | None" = None     # Arrow result (engines that return Arrow)
    engine_query_id: str | None = None
    error: EngineError | None = None    # error reported by the engine
    client_error: str | None = None     # harness-side failure (connection lost, ...)
    timed_out: bool = False
    cancelled: bool = False

    @property
    def completed(self) -> bool:
        return self.rows is not None or self.table is not None

    @property
    def row_count(self) -> int | None:
        if self.table is not None:
            return self.table.num_rows
        return len(self.rows) if self.rows is not None else None

    def as_rows(self) -> list[list] | None:
        """Result as a list of rows of Python values (both kinds of result)."""
        if self.table is None:
            return self.rows
        cols = [c.to_pylist() for c in self.table.columns]
        return [list(r) for r in zip(*cols, strict=True)] if cols else [[] for _ in range(self.table.num_rows)]


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
        """engine_elapsed_ms, engine_cpu_s, engine_peak_mem_bytes from `stats` (None if unknown)."""

    @abstractmethod
    def pool_limit_bytes(self) -> int | None:
        """Per-node query memory limit (Trino query.max-memory-per-node, DataFusion pool)."""

    @abstractmethod
    def config_files(self) -> list:
        """Config files whose hashes go into env.json."""

    @abstractmethod
    def stop(self) -> None:
        """Stop the engine."""

    def busy(self) -> bool:
        """True if the engine still works on or holds memory for a finished/cancelled query."""
        return False

    def describe(self) -> dict:
        """Engine-reported version and configuration, for env.json (best effort)."""
        return {}
