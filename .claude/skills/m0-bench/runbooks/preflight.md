# Preflight

Run `m0 doctor`. `FAIL` lines block work (exit code 2). `WARN` lines make
measurements less trustworthy, but you can still develop.

| Check | Fails or warns when | Fix |
|---|---|---|
| `python`, `uv`, `git` | Missing or too old | uv: `curl -LsSf https://astral.sh/uv/<version>/install.sh \| sh`, with the version from `versions.lock.yaml` (ask the user before installing anything) |
| `cargo` | Missing (warning) | Needed from build step 3 (`dfrunner`). Install rustup, after asking the user. |
| `versions.lock` | Unreadable or missing images | Restore the file from git |
| `docker`, `docker.compose` | Daemon not reachable | Docker Engine runs inside WSL: `sudo systemctl start docker` (the user may need to run it) |
| `docker.cgroup`, `cgroup.v2` | Not cgroup v2 or missing `cpu`/`memory` | Required for CPU and memory metrics; WSL2 kernels ≥ 5.15 with systemd provide it |
| `kernel` | < 6.12 (warning) | Per-query `memory.peak` reset unavailable; the sampler falls back. Acceptable. |
| `memory` | < 10 GiB visible (warning), < 6 GiB (fail) | Raise `memory=` in `.wslconfig`, then `wsl --shutdown` (user action on Windows) |
| `swap` | Swap on (warning) | Benchmark runs expect `swap=0`, so OOM behaves like on Spot nodes |
| `disk` | < 50 GiB free (warning), < 20 GiB (fail) | Free space on the WSL virtual disk, or prune old Docker images |
| `port.<n>` | Port taken by something other than the m0 stack | Find it: `ss -ltnp \| grep :<n>` |
| `wsl.*` | `.wslconfig` differs from the recommended values | See `docs/infrastructure.md`. The user edits the file on Windows and runs `wsl --shutdown`. |

Machine hygiene for measurement runs (the user does these on Windows):
- High Performance power plan.
- Other apps closed.
- Windows Update paused.
- Defender scheduled scans paused.

Doctor can't check these, so ask the user before a measurement run.
