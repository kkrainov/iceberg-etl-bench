"""Environment part of `results/<run-id>/run.json`: git, versions, engines, host."""

import hashlib
import json
import os
import platform
import subprocess
from pathlib import Path

from m0 import doctor, env_local, versions
from m0.paths import REPO_ROOT


def _run(cmd: list[str], timeout: float = 20) -> str | None:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=REPO_ROOT, check=False)
        return res.stdout.strip() if res.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def git_info() -> dict:
    status = _run(["git", "status", "--porcelain"])
    return {"sha": _run(["git", "rev-parse", "HEAD"]), "dirty": bool(status) if status is not None else None}


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def image_digests(images: dict[str, str]) -> dict:
    out = {}
    for name, ref in images.items():
        raw = _run(["docker", "image", "inspect", ref, "--format", "{{json .}}"])
        info = json.loads(raw) if raw else {}
        out[name] = {"ref": ref, "id": info.get("Id"), "repo_digests": info.get("RepoDigests")}
    return out


def host_info() -> dict:
    cpu = None
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    mem = doctor.meminfo()
    info = doctor.docker_info() or {}
    return {"cpu_model": cpu, "cpus": os.cpu_count(), "mem_total_bytes": mem.get("MemTotal"),
            "swap_total_bytes": mem.get("SwapTotal"), "kernel": platform.release(),
            "os": _run(["sh", "-c", ". /etc/os-release && echo $PRETTY_NAME"]),
            "docker": info.get("ServerVersion"), "cgroup": f"v{info.get('CgroupVersion')} {info.get('CgroupDriver')}"}


def wsl_info() -> dict | None:
    if not doctor.is_wsl():
        return None
    path = doctor.find_wslconfig()
    return {"path": str(path) if path else None, "text": path.read_text(errors="replace") if path else None}


def _describe(engine) -> dict:
    """Engine-reported versions and settings, if the engine is up (best effort)."""
    try:
        return engine.describe()
    except Exception as e:  # engine not running yet
        return {"error": str(e)[:200]}


def containers(services: list[str]) -> dict:
    out = {}
    for svc in services:
        st = env_local.container_state(svc)
        out[svc] = {k: st.get(k) for k in ("image", "mem_limit", "nano_cpus")}
    return out


def engine_entry(engine) -> dict:
    """Engine-reported version and key settings, plus hashes of its config files."""
    return {"info": _describe(engine),
            "config_hashes": {str(p.relative_to(REPO_ROOT)): file_hash(p) for p in engine.config_files()}}


def collect(engines: list) -> dict:
    lock = versions.load()
    return {
        "git": git_info(),
        "versions_lock_hash": versions.lock_hash(),
        "versions": lock,
        "images": image_digests(lock["images"]),
        "engines": {e.name: engine_entry(e) for e in engines},
        "containers": containers(["objstore", "catalog", *[e.service for e in engines]]),
        "host": host_info(),
        "wsl": wsl_info(),
    }
