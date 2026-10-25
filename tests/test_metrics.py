import time

from m0.engines.trino import parse_duration_ms, parse_size_bytes, trim_query_info
from m0.metrics import Cgroup, Sampler, cgroup_dir


def fake_cgroup(path, usage=1_000_000, current=100, peak=500, oom=0):
    path.mkdir(parents=True, exist_ok=True)
    (path / "cpu.stat").write_text(f"usage_usec {usage}\nuser_usec 1\nsystem_usec 1\n")
    (path / "memory.current").write_text(f"{current}\n")
    (path / "memory.peak").write_text(f"{peak}\n")
    (path / "memory.events").write_text(f"low 0\nhigh 0\nmax 0\noom 0\noom_kill {oom}\noom_group_kill 0\n")
    return path


def test_cgroup_dir_systemd_layout(tmp_path):
    d = fake_cgroup(tmp_path / "system.slice" / "docker-abc.scope")
    assert cgroup_dir("abc", tmp_path) == d
    assert cgroup_dir("nope", tmp_path) is None


def test_sampler_deltas_and_sampled_peak(tmp_path):
    d = fake_cgroup(tmp_path / "e")
    s = Sampler({"engine": Cgroup(d), "missing": Cgroup(None)}, hz=200)
    s.start()
    fake_cgroup(d, usage=3_500_000, current=400, peak=500)  # below the lifetime peak
    time.sleep(0.05)
    fake_cgroup(d, usage=3_500_000, current=200, peak=500)
    r = s.stop()
    assert r["engine"].cpu_s == 2.5
    assert (r["engine"].peak_bytes, r["engine"].peak_source) == (400, "sampled")
    assert r["engine"].start_bytes == 100 and r["engine"].oom_kills == 0
    assert r["missing"].peak_bytes is None and r["missing"].cpu_s is None
    out = tmp_path / "m.csv"
    s.write_timeline(out)
    assert out.read_text().startswith("t_ms,engine_bytes,missing_bytes\n")


def test_sampler_uses_memory_peak_when_lifetime_peak_rises(tmp_path):
    d = fake_cgroup(tmp_path / "e")
    s = Sampler({"engine": Cgroup(d)}, hz=200)
    s.start()
    fake_cgroup(d, current=150, peak=900, oom=1)  # spike between samples
    r = s.stop()
    assert (r["engine"].peak_bytes, r["engine"].peak_source) == (900, "memory.peak")
    assert r["engine"].oom_kills == 1


def test_sampler_keeps_last_values_when_cgroup_vanishes(tmp_path):
    d = fake_cgroup(tmp_path / "e")
    s = Sampler({"engine": Cgroup(d)}, hz=200)
    s.start()
    fake_cgroup(d, usage=2_000_000, oom=1)
    time.sleep(0.05)
    for f in d.iterdir():
        f.unlink()
    r = s.stop()
    assert r["engine"].oom_kills == 1 and r["engine"].cpu_s == 1.0


def test_trino_units():
    assert parse_duration_ms("1.50s") == 1500
    assert parse_duration_ms("250.00ms") == 250
    assert parse_duration_ms("2.00m") == 120000
    assert parse_duration_ms("156.62us") == 0.15662
    assert parse_size_bytes("4.00GB") == 4 * 1024**3
    assert parse_size_bytes("6535043B") == 6535043
    assert parse_size_bytes("13.01kB") == round(13.01 * 1024)
    assert parse_size_bytes(None) is None


def test_trim_query_info_drops_stacks():
    info = {"queryId": "q", "state": "FAILED", "errorCode": {"name": "X"},
            "failureInfo": {"type": "T", "message": "m", "stack": ["a"] * 100, "cause": {"type": "C", "message": "c"}},
            "queryStats": {"elapsedTime": "1.00s", "operatorSummaries": [{"operatorType": "X"}] * 1000}, "stages": {"big": 1}}
    t = trim_query_info(info)
    assert "stack" not in t["failureInfo"] and t["failureInfo"]["cause"]["type"] == "C"
    assert t["queryStats"] == {"elapsedTime": "1.00s"} and "stages" not in t


def test_trino_operator_time_breakdown():
    from m0.engines.trino import TrinoEngine, operator_kind

    ops = [{"operatorType": "TableScanOperator", "getOutputWall": "2.00s"},
           {"operatorType": "ScanFilterAndProjectOperator", "getOutputWall": "500.00ms"},
           {"operatorType": "ExchangeOperator", "getOutputWall": "10.00ms"},
           {"operatorType": "PartitionedOutputOperator", "addInputWall": "5.00ms", "finishWall": "1.00ms"},
           {"operatorType": "LocalMergeSourceOperator", "getOutputWall": "4.00ms"},
           {"operatorType": "HashAggregationOperator", "addInputWall": "1.00s", "getOutputWall": "20.00ms"}]
    info = trim_query_info({"queryStats": {"operatorSummaries": ops, "planningTime": "100.00ms"}})
    assert info["operatorTime"] == {"scan_ms": 2500.0, "exchange_ms": 20.0, "compute_ms": 1020.0}
    m = TrinoEngine().metrics(info)
    assert (m["planning_ms"], m["scan_ms"], m["compute_ms"]) == (100.0, 2500.0, 1020.0)
    assert operator_kind("LookupJoinOperator") == "compute"
    assert "operatorSummaries" not in info["queryStats"]
