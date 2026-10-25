import json

import pytest

from m0 import cli, store
from m0.store import FIELDS, RunStore, delete_run, is_kept, keep_patterns, list_runs, run_dirs


def row(cell_id: str, outcome: str = "ok") -> dict:
    return {"cell_id": cell_id, "engine": "trino", "suite": "tpch", "query_id": "q01", "outcome": outcome,
            "wall_ms": 12.5, "peak_mem_bytes": 2**33, "cold": True, "iteration": 0}


def test_append_and_recorded(tmp_path):
    s = RunStore("r1", tmp_path)
    s.write_run({"run_id": "r1"})
    s.append(row("a"))
    s.append(row("b", "err_mem"))
    assert s.recorded() == {"a", "b"}
    rows = s.rows()
    assert [r["outcome"] for r in rows] == ["ok", "err_mem"]
    assert list(rows[0]) == list(FIELDS)  # every line has every field, in order
    assert rows[0]["peak_mem_bytes"] == 2**33 and rows[0]["answer"] is None


def test_torn_last_line_is_ignored(tmp_path):
    s = RunStore("r1", tmp_path)
    s.write_run({})
    s.append(row("a"))
    with s.jsonl.open("a") as f:
        f.write('{"cell_id": "b", "eng')
    assert s.recorded() == {"a"}


def test_unknown_fields_are_rejected(tmp_path):
    s = RunStore("r1", tmp_path)
    s.write_run({})
    with pytest.raises(ValueError):
        s.append({**row("a"), "made_up": 1})


def test_answers_capped_and_keyed(tmp_path):
    s = RunStore("r1", tmp_path)
    s.write_run({})
    cols = [("k", "varchar"), ("v", "decimal(12,2)")]
    rows = [["x", "1.50"], [None, None]] * 3
    assert s.write_answer(row("c1"), cols, rows, max_rows=100) == "full"
    assert s.write_answer({**row("c2"), "engine": "df"}, cols, rows, max_rows=4) == "truncated"
    a, b = s.answers()
    assert a["columns"] == [{"name": "k", "type": "varchar"}, {"name": "v", "type": "decimal(12,2)"}]
    assert a["rows"] == rows and a["row_count"] == 6 and not a["truncated"]
    assert len(b["rows"]) == 4 and b["row_count"] == 6 and b["truncated"]
    assert s.answered() == {("trino", "tpch", "q01"), ("df", "tpch", "q01")}


def test_answer_byte_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "ANSWER_BYTES_CAP", 30)
    s = RunStore("r1", tmp_path)
    s.write_run({})
    assert s.write_answer(row("c1"), [("s", "varchar")], [["abcdefghij"]] * 5, max_rows=100) == "truncated"
    assert len(s.answers()[0]["rows"]) == 2


def test_list_runs_by_start_time(tmp_path):
    for run_id, started in (("b", "2026-01-02"), ("a", "2026-01-03")):
        RunStore(run_id, tmp_path).write_run({"started_at": started})
    assert [r.run_id for r in list_runs(tmp_path)] == ["b", "a"]


def make_results(root):
    """Two current runs, one old-format run, and the datagen/verify artifact dirs."""
    for run_id in ("smoke-1", "ref-1"):
        RunStore(run_id, root).write_run({"started_at": "2026-01-01", "plan": None})
    (root / "old").mkdir()
    (root / "old" / "plan.yaml").write_text("name: x\n")
    for d in ("datagen", "verify"):
        (root / d).mkdir()
        (root / d / "cells.jsonl").write_text("{}\n")  # even run-like files don't make them runs
    (root / "keep.txt").write_text("# reference runs\nref-*\n")


def test_run_dirs_skip_other_artifacts(tmp_path):
    make_results(tmp_path)
    assert [p.name for p in run_dirs(tmp_path)] == ["old", "ref-1", "smoke-1"]
    assert keep_patterns(tmp_path) == ["ref-*"]
    assert is_kept("ref-1", ["ref-*"]) and not is_kept("smoke-1", ["ref-*"])
    with pytest.raises(ValueError):
        delete_run(tmp_path / "datagen", tmp_path)


def test_results_prune_is_a_dry_run_without_yes(tmp_path, monkeypatch, capsys):
    make_results(tmp_path)
    monkeypatch.setattr(store, "RESULTS_DIR", tmp_path)
    assert cli.main(["results", "prune", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert sorted(out["would_delete"]) == ["old", "smoke-1"] and out["kept"] == ["ref-1"]
    assert (tmp_path / "old").is_dir() and (tmp_path / "smoke-1").is_dir()


def test_results_prune_yes_deletes_unkept_runs_only(tmp_path, monkeypatch, capsys):
    make_results(tmp_path)
    monkeypatch.setattr(store, "RESULTS_DIR", tmp_path)
    assert cli.main(["results", "prune", "--keep", "old", "--yes"]) == 0
    assert "DELETED  smoke-1" in capsys.readouterr().out
    assert sorted(p.name for p in tmp_path.iterdir()) == ["datagen", "keep.txt", "old", "ref-1", "verify"]


def test_results_list(tmp_path, monkeypatch, capsys):
    make_results(tmp_path)
    monkeypatch.setattr(store, "RESULTS_DIR", tmp_path)
    assert cli.main(["results", "list", "--json"]) == 0
    runs = {r["run_id"]: r for r in json.loads(capsys.readouterr().out)["runs"]}
    assert set(runs) == {"old", "ref-1", "smoke-1"}
    assert runs["ref-1"]["keep"] and not runs["smoke-1"]["keep"]
    assert runs["old"]["state"] == "old format"
