# Infrastructure

## Phase L: one workstation (local, $0)

### Reference machine

| Item | Value |
|---|---|
| CPU | AMD Ryzen 5 7600X, 6 cores / 12 threads (all cores identical) |
| RAM | 16 GB physical; WSL2 limited to ~11.7 GB |
| Disk | NVMe (Samsung 990 PRO); data on WSL's ext4 virtual disk, never under `/mnt/c` |
| OS | Windows host, WSL2 Ubuntu 26.04, kernel 6.18, systemd, cgroup v2 |
| Containers | Docker Engine 29.x inside WSL |

`m0 doctor` records all of this in `env.json`.

### WSL and host settings

`%UserProfile%\.wslconfig`:

```ini
[wsl2]
memory=12GB
processors=12
swap=0              # benchmark runs only; restore afterwards if you rely on swap
[experimental]
autoMemoryReclaim=disabled
```

Apply with `wsl --shutdown`.

On the Windows host during runs:
- High Performance power plan.
- Other apps closed.
- Windows Update paused.
- Defender scheduled scans paused.

`m0 doctor` warns when WSL settings differ from these.

### Local stack (`infra/local/compose.yaml`)

| Service | Image (pinned) | Role | Memory |
|---|---|---|---|
| `objstore` | SeaweedFS (S3 API) | S3-compatible storage. MinIO's repository is archived, so it isn't used. | ~0.5 GiB |
| `catalog` | Lakekeeper 0.14.x + Postgres | Iceberg REST catalog shared by both engines | ~0.5 GiB |
| `trino` | `trinodb/trino:484` | Single node (coordinator schedules work on itself) | Engine budget |
| `dfrunner` | Built locally | `single` mode, or `node` × N for `dfd` | Engine budget |

**Rules**
- Only one engine runs at a time.
- The engine budget at the 100% memory level is **8 GiB** per engine: one 8 GiB
  node, or 2 × 4 GiB nodes for `dfd`/Trino in suite A3.
- **Trino settings:**
  - JVM `-Xmx` at about 80% of the container limit, per the docs.
  - `query.max-memory-per-node` and `memory.heap-headroom-per-node` per the docs, with
    values listed in `configs/trino/local-sf10/`.
  - `retry-policy=NONE`.
- **DataFusion settings:** memory pool limit equals Trino's `query.max-memory-per-node`;
  `target_partitions` equals the container's CPU count.
- **Containers:**
  - Swap disabled per container (`memory-swap` equal to `memory`).
  - CPUs pinned with `cpuset` when several nodes share the host.
- **Shared overhead:** the object store and catalog take CPU and memory from the same
  host. That affects both engines equally and is documented.

### Data volumes (estimates)

| Dataset | Iceberg/Parquet size | Disk |
|---|---|---|
| TPC-H + TPC-DS SF1 | ~1 GB | Trivial |
| TPC-H + TPC-DS SF10 | ~8–10 GB | Fine |
| `etl.*` at SF10 | ~5 GB | Fine |

## Phase C: AWS (Spot, no Kubernetes)

Phase C details are finalised after Phase L. This section fixes the decisions already
made.

### Decisions

| Item | Choice | Why |
|---|---|---|
| Region | `eu-north-1` (Stockholm) | Among the cheapest Graviton Spot prices, the lowest interruption band (<5%), no opt-in needed |
| Architecture | arm64 (Graviton 4, r8g family) | Cheaper Spot. Phase L builds must therefore be multi-arch. |
| Catalog | AWS Glue | No catalog service to run; both engines support it |
| Provisioning | Bash + AWS CLI, everything tagged `m0-bench=<run-id>` | No state file. Teardown finds resources by tag. |
| Network | One VPC, public subnet, S3 gateway endpoint, no NAT gateway | Avoids NAT charges; S3 traffic stays inside the region and free |
| Nodes | Amazon Linux 2023 arm64 with kernel ≥ 6.12 (needed for per-query `memory.peak` reset), Docker, the same images as Phase L | One code path |

### Cluster tiers

Spot prices below are snapshot values from 2026-10-23, $/h in `eu-north-1`. **Re-check them before every run.**

| Tier | Data | Nodes | Est. cost of one full run |
|---|---|---|---|
| Smoke | SF10 | 1 coordinator + 2 × r8g.xlarge ($0.029) | < $0.50 |
| Primary | SF100 | 1 coordinator + 4 × r8g.2xlarge ($0.067) | ~$3–5 |
| Scaling (A4) | SF100 | 1/2/4/8 × r8g.2xlarge | ~$3–5 |
| Scale check (B4, conditional) | SF1000 | 1 coordinator + 8 × r8g.4xlarge ($0.100) | ~$10–15 |

**Estimate for all of Phase C: ~$100–200.** That covers:
- debugging runs,
- two independent primary runs,
- the scaling curve,
- one or two SF1000 runs,
- S3 storage and requests,
- public IPv4 addresses.

It's an estimate; check it against current Spot prices before running.

### Cost guardrails (enforced by `m0 aws …` scripts)

1. **Confirmation.** No billable action runs without `--yes`. The agent must ask the
   user before passing it.
2. **TTL on every instance.**
   - Instances launch with `InstanceInitiatedShutdownBehavior=terminate`.
   - A boot-time `shutdown -h +<ttl>` timer is set (default 4 h).
   - `m0 aws ttl` shows the remaining time.
3. **Budget.** An AWS Budget is set to $250 with alerts at 50/80/100%.
   `m0 aws cost` shows month-to-date spend, and refuses to launch above a configured
   ceiling (exit code 4).
4. **Teardown is always verified.**
   - `m0 aws teardown` terminates everything tagged for the run and empties the run's
     S3 prefix when asked.
   - It then lists any remaining `m0-bench` resources in every region and fails if
     there are any.
5. **Start-of-session check.** Every agent session starts with `m0 aws status`, to
   catch forgotten resources.

### Account bootstrap (one-time, manual; listed in the prerequisites runbook)

1. Create the AWS account, enable MFA on the root user, and stop using root for
   anything else.
2. If the account starts on a restricted free plan, upgrade it.
3. Create an admin identity (IAM Identity Center or IAM user) and configure an AWS
   CLI profile.
4. Create the AWS Budget and its alerts.
5. Request a Spot vCPU quota increase in `eu-north-1`:
   - ≥ 64 vCPUs for the primary tier.
   - ≥ 136 vCPUs for the SF1000 tier.
   - Approval can take days, so do it early.
6. Create the S3 bucket and Glue database in `eu-north-1`.
