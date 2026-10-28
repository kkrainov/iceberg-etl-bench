"""`m0 doctor`: prerequisite and machine-hygiene checks for Phase L."""

import configparser
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

from m0 import versions
from m0.output import FAIL, INFO, OK, WARN, Check

GIB = 1024**3
MIN_KERNEL_FOR_PEAK_RESET = (6, 12)
MIN_DISK_GIB, WARN_DISK_GIB = 20, 50
MIN_MEM_GIB, WARN_MEM_GIB = 6, 10
STACK_PORTS = {8080: "trino", 8181: "catalog", 8333: "objstore", 9000: "df and its variants"}

# Recommended .wslconfig values for benchmark runs (docs/infrastructure.md).
WSL_MIN_MEMORY_GIB = 11


def run(cmd: list[str], timeout: float = 15) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def parse_size(value: str) -> int | None:
    """Parse a .wslconfig size ('12GB', '512MB', '12582912000') into bytes."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([KMGT]?B?)?\s*", value, re.IGNORECASE)
    if not m:
        return None
    number, unit = float(m.group(1)), (m.group(2) or "").upper().rstrip("B")
    return int(number * {"": 1, "K": 1024, "M": 1024**2, "G": GIB, "T": 1024**4}[unit])


def kernel_version(release: str) -> tuple[int, int]:
    m = re.match(r"(\d+)\.(\d+)", release)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def meminfo() -> dict[str, int]:
    out = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, _, rest = line.partition(":")
        out[key] = int(rest.split()[0]) * 1024
    return out


def is_wsl() -> bool:
    return "microsoft" in platform.release().lower()


def find_wslconfig() -> Path | None:
    try:
        profile = run(["cmd.exe", "/c", "echo %UserProfile%"], timeout=10).stdout.strip()
        path = Path(run(["wslpath", "-u", profile], timeout=5).stdout.strip()) / ".wslconfig"
        if path.is_file():
            return path
    except (OSError, subprocess.SubprocessError):
        pass
    candidates = list(Path("/mnt/c/Users").glob("*/.wslconfig"))
    return candidates[0] if len(candidates) == 1 else None


def wslconfig_checks(text: str) -> list[Check]:
    cfg = configparser.ConfigParser(strict=False, inline_comment_prefixes=("#", ";"))
    cfg.read_string(text)
    wsl = cfg["wsl2"] if cfg.has_section("wsl2") else {}
    exp = cfg["experimental"] if cfg.has_section("experimental") else {}
    checks = []

    mem = parse_size(wsl.get("memory", "")) if wsl.get("memory") else None
    if mem is None:
        checks.append(Check("wsl.memory", WARN, "not set; set memory=12GB in .wslconfig"))
    else:
        status = OK if mem >= WSL_MIN_MEMORY_GIB * GIB else WARN
        checks.append(Check("wsl.memory", status, f"{mem / GIB:.1f} GiB"))

    procs = wsl.get("processors")
    if procs is None:
        checks.append(Check("wsl.processors", WARN, "not set; pin it so runs are comparable"))
    else:
        checks.append(Check("wsl.processors", OK, procs))

    swap = parse_size(wsl.get("swap", "")) if wsl.get("swap") else None
    if swap == 0:
        checks.append(Check("wsl.swap", OK, "0"))
    else:
        detail = "default (25% of RAM)" if swap is None else f"{swap / GIB:.1f} GiB"
        checks.append(Check("wsl.swap", WARN, f"{detail}; set swap=0 for benchmark runs"))

    reclaim = exp.get("autoMemoryReclaim", "").lower()
    status = OK if reclaim == "disabled" else WARN
    detail = reclaim or "default"
    checks.append(Check("wsl.autoMemoryReclaim", status, detail if status == OK else f"{detail}; set disabled"))
    return checks


def check_python() -> Check:
    v = sys.version_info
    status = OK if v >= (3, 12) else FAIL
    return Check("python", status, f"{v.major}.{v.minor}.{v.micro}")


def check_tool(name: str, args: list[str], required: bool, why: str = "") -> Check:
    if not shutil.which(name):
        return Check(name, FAIL if required else WARN, f"not found{'; ' + why if why else ''}")
    try:
        out = run([name, *args]).stdout.strip().splitlines()
        return Check(name, OK, out[0] if out else "")
    except (OSError, subprocess.SubprocessError) as e:
        return Check(name, FAIL if required else WARN, str(e))


def docker_info() -> dict | None:
    try:
        res = run(["docker", "info", "--format", "{{json .}}"], timeout=20)
        return json.loads(res.stdout) if res.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None


def check_docker(info: dict | None) -> list[Check]:
    if info is None:
        return [Check("docker", FAIL, "daemon not reachable (is Docker running in WSL?)")]
    checks = [Check("docker", OK, f"server {info.get('ServerVersion')}")]
    cg = str(info.get("CgroupVersion"))
    checks.append(Check("docker.cgroup", OK if cg == "2" else FAIL, f"cgroup v{cg}, driver {info.get('CgroupDriver')}"))
    try:
        res = run(["docker", "compose", "version", "--short"])
        checks.append(Check("docker.compose", OK if res.returncode == 0 else FAIL, res.stdout.strip() or res.stderr.strip()))
    except (OSError, subprocess.SubprocessError) as e:
        checks.append(Check("docker.compose", FAIL, str(e)))
    return checks


def check_cgroup() -> Check:
    path = Path("/sys/fs/cgroup/cgroup.controllers")
    if not path.exists():
        return Check("cgroup.v2", FAIL, "cgroup v2 not mounted")
    controllers = set(path.read_text().split())
    missing = {"cpu", "memory"} - controllers
    return Check("cgroup.v2", FAIL if missing else OK, f"missing {sorted(missing)}" if missing else "cpu, memory")


def check_kernel() -> Check:
    release = platform.release()
    ok = kernel_version(release) >= MIN_KERNEL_FOR_PEAK_RESET
    detail = release if ok else f"{release}; <6.12, per-query memory.peak reset unavailable (sampling fallback)"
    return Check("kernel", OK if ok else WARN, detail)


def check_memory(mem: dict[str, int]) -> list[Check]:
    total = mem.get("MemTotal", 0)
    status = FAIL if total < MIN_MEM_GIB * GIB else WARN if total < WARN_MEM_GIB * GIB else OK
    checks = [Check("memory", status, f"{total / GIB:.1f} GiB visible to Linux")]
    swap = mem.get("SwapTotal", 0)
    checks.append(Check("swap", OK if swap == 0 else WARN,
                        "off" if swap == 0 else f"{swap / GIB:.1f} GiB on; benchmark runs expect swap off"))
    return checks


def check_disk(info: dict | None) -> Check:
    root = (info or {}).get("DockerRootDir") or "/"
    try:
        free = shutil.disk_usage(root).free
    except OSError:
        root, free = "/", shutil.disk_usage("/").free
    gib = free / GIB
    status = FAIL if gib < MIN_DISK_GIB else WARN if gib < WARN_DISK_GIB else OK
    return Check("disk", status, f"{gib:.0f} GiB free at {root}")


def port_owner(port: int) -> str | None:
    """Name of the m0 container publishing a port, if any."""
    try:
        res = run(["docker", "ps", "--filter", "label=com.docker.compose.project=m0",
                   "--filter", f"publish={port}", "--format", "{{.Names}}"])
        return res.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def check_ports() -> list[Check]:
    checks = []
    for port, service in STACK_PORTS.items():
        with socket.socket() as s:
            free = s.connect_ex(("127.0.0.1", port)) != 0
        if free:
            checks.append(Check(f"port.{port}", OK, f"free ({service})"))
        elif owner := port_owner(port):
            checks.append(Check(f"port.{port}", OK, f"used by {owner}"))
        else:
            checks.append(Check(f"port.{port}", FAIL, f"in use by something else; needed by {service}"))
    return checks


def check_versions() -> Check:
    try:
        data = versions.load()
        return Check("versions.lock", OK, f"{len(data['images'])} images, hash {versions.lock_hash()}")
    except (OSError, ValueError) as e:
        return Check("versions.lock", FAIL, str(e))


def run_checks() -> list[Check]:
    info = docker_info()
    checks = [
        check_python(),
        check_tool("uv", ["--version"], required=True),
        check_tool("git", ["--version"], required=True),
        check_tool("cargo", ["--version"], required=False, why="needed from step 3 (dfrunner)"),
        check_versions(),
        *check_docker(info),
        check_cgroup(),
        check_kernel(),
        Check("cpus", INFO, str(os.cpu_count())),
        *check_memory(meminfo()),
        check_disk(info),
        *check_ports(),
    ]
    if is_wsl():
        path = find_wslconfig()
        if path is None:
            checks.append(Check("wsl.config", WARN, ".wslconfig not found; see docs/infrastructure.md"))
        else:
            checks.append(Check("wsl.config", INFO, str(path)))
            checks.extend(wslconfig_checks(path.read_text(errors="replace")))
    return checks
