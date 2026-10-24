"""cgroup v2 metrics for engine and infra containers, read without root.

Readable unprivileged (verified on WSL2 6.18, Docker systemd driver): `cpu.stat`,
`memory.current`, `memory.events` and `memory.peak` (lifetime high-water mark).
A per-query `memory.peak` reset needs a write to the root-owned file, and the
kernel (>= 6.12) only resets it for the writing file descriptor, so it isn't
possible unprivileged.

Per-cell peak memory is therefore:
- `memory.peak` (exact) when the container's lifetime peak rose during the cell:
  the new lifetime peak was reached inside the cell window;
- otherwise the maximum of `memory.current` sampled at SAMPLE_HZ. This can miss
  spikes shorter than the sample interval (50 ms), so it's a lower bound.
"""

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

CGROUP_ROOT = Path("/sys/fs/cgroup")
SAMPLE_HZ = 20


def cgroup_dir(container_id: str, root: Path = CGROUP_ROOT) -> Path | None:
    """cgroup v2 directory of a Docker container (systemd or cgroupfs driver)."""
    for candidate in (root / "system.slice" / f"docker-{container_id}.scope", root / "docker" / container_id):
        if candidate.is_dir():
            return candidate
    return None


def _read(path: Path) -> str | None:
    try:
        return path.read_text()
    except OSError:  # container gone, or file unreadable
        return None


def _keyed(text: str | None, key: str) -> int | None:
    if text is None:
        return None
    for line in text.splitlines():
        k, _, v = line.partition(" ")
        if k == key:
            return int(v)
    return None


@dataclass
class Snapshot:
    cpu_usec: int | None
    mem_current: int | None
    mem_peak: int | None  # lifetime
    oom_kill: int | None


class Cgroup:
    def __init__(self, path: Path | None):
        self.path = path

    def snapshot(self) -> Snapshot:
        if self.path is None:
            return Snapshot(None, None, None, None)
        return Snapshot(_keyed(_read(self.path / "cpu.stat"), "usage_usec"), self.current(),
                        _int(_read(self.path / "memory.peak")),
                        _keyed(_read(self.path / "memory.events"), "oom_kill"))

    def current(self) -> int | None:
        return None if self.path is None else _int(_read(self.path / "memory.current"))

    def cpu_usec(self) -> int | None:
        return None if self.path is None else _keyed(_read(self.path / "cpu.stat"), "usage_usec")

    def oom_kill(self) -> int | None:
        return None if self.path is None else _keyed(_read(self.path / "memory.events"), "oom_kill")


def _int(text: str | None) -> int | None:
    try:
        return int(text.strip()) if text is not None else None
    except ValueError:
        return None


@dataclass
class TargetResult:
    cpu_s: float | None
    peak_bytes: int | None
    peak_source: str | None  # "memory.peak" (exact) | "sampled" (lower bound) | None
    start_bytes: int | None
    oom_kills: int
    samples: int


@dataclass
class Sampler:
    """Samples `memory.current` (and `oom_kill`) of several cgroups in a thread."""

    targets: dict[str, Cgroup]
    hz: int = SAMPLE_HZ
    timeline: list[tuple] = field(default_factory=list)

    def __post_init__(self):
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._before: dict[str, Snapshot] = {}
        self._max: dict[str, int | None] = {}
        self._oom_last: dict[str, int | None] = {}
        self._t0 = 0.0

    def start(self) -> None:
        self._before = {n: c.snapshot() for n, c in self.targets.items()}
        self._max = {n: s.mem_current for n, s in self._before.items()}
        self._oom_last = {n: s.oom_kill for n, s in self._before.items()}
        self._cpu_last = {n: s.cpu_usec for n, s in self._before.items()}
        self._t0 = time.monotonic()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="m0-sampler", daemon=True)
        self._thread.start()

    def _sample(self) -> None:
        row = [round((time.monotonic() - self._t0) * 1000)]
        for name, cg in self.targets.items():
            cur = cg.current()
            row.append(cur)
            if cur is not None and (self._max[name] is None or cur > self._max[name]):
                self._max[name] = cur
            oom = cg.oom_kill()
            if oom is not None:
                self._oom_last[name] = oom  # keep the last values seen before a cgroup vanishes
            cpu = cg.cpu_usec()
            if cpu is not None:
                self._cpu_last[name] = cpu
        self.timeline.append(tuple(row))

    def _loop(self) -> None:
        interval = 1.0 / self.hz
        while not self._stop.is_set():
            self._sample()
            self._stop.wait(interval)

    def stop(self) -> dict[str, TargetResult]:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._sample()
        out = {}
        for name, cg in self.targets.items():
            before, after = self._before[name], cg.snapshot()
            cpu = None
            cpu_after = after.cpu_usec if after.cpu_usec is not None else self._cpu_last[name]
            if before.cpu_usec is not None and cpu_after is not None:
                cpu = (cpu_after - before.cpu_usec) / 1e6
            peak, source = self._max[name], "sampled" if self._max[name] is not None else None
            if after.mem_peak is not None and before.mem_peak is not None and after.mem_peak > before.mem_peak:
                peak, source = after.mem_peak, "memory.peak"
            oom_after = after.oom_kill if after.oom_kill is not None else self._oom_last[name]
            ooms = (oom_after - before.oom_kill) if None not in (oom_after, before.oom_kill) else 0
            out[name] = TargetResult(cpu, peak, source, before.mem_current, ooms, len(self.timeline))
        return out

    def write_timeline(self, path: Path) -> None:
        with path.open("w") as f:
            f.write("t_ms," + ",".join(f"{n}_bytes" for n in self.targets) + "\n")
            for row in self.timeline:
                f.write(",".join("" if v is None else str(v) for v in row) + "\n")
