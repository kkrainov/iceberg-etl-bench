"""DataFusion overrides: used only by DataFusion engines, only with a manifest reason."""

import pytest

from m0 import queries

ENTRY = ("queries:\n  - {suite: tpch, id: q99, class: tpc, namespaces: [tpch], ordered: false, "
         "tie_ambiguous: false, rewrite: null, source: test%s}\n")


@pytest.fixture
def qdir(tmp_path, monkeypatch):
    monkeypatch.setattr(queries, "QUERIES_DIR", tmp_path)
    monkeypatch.setattr(queries, "OVERRIDES_DIR", tmp_path / "overrides")
    (tmp_path / "tpch").mkdir()
    (tmp_path / "tpch" / "q99.sql").write_text("SELECT sequence(1, 3) FROM ${tpch}.region\n")
    return tmp_path


def override(qdir, text="SELECT generate_series(1, 3) FROM ${tpch}.region\n"):
    d = qdir / "overrides" / "datafusion" / "tpch"
    d.mkdir(parents=True)
    (d / "q99.sql").write_text(text)


def manifest(qdir, reason: str | None) -> dict:
    extra = f", datafusion_override: '{reason}'" if reason else ""
    path = qdir / "manifest.yaml"
    path.write_text(ENTRY % extra)
    return queries.load_manifest(path)


def test_override_used_by_datafusion_engines_only(qdir):
    override(qdir)
    q = manifest(qdir, "sequence -> generate_series (same inclusive bounds)")["tpch/q99"]
    assert "sequence(1, 3)" in queries.render(q, 1, "trino")
    assert "sequence(1, 3)" in queries.render(q, 1)
    for engine in ("df", "dfd", "dfd-jo"):
        assert queries.render(q, 1, engine) == "SELECT generate_series(1, 3) FROM iceberg.tpch_sf1.region"


def test_override_without_reason_is_rejected(qdir):
    override(qdir)
    with pytest.raises(queries.QueryError, match="gives no datafusion_override reason"):
        manifest(qdir, None)


def test_reason_without_override_file_is_rejected(qdir):
    with pytest.raises(queries.QueryError, match="override file missing"):
        manifest(qdir, "some reason")


def test_override_must_use_the_same_namespaces(qdir):
    override(qdir, "SELECT 1 FROM ${tpcds}.item\n")
    q = manifest(qdir, "reason")["tpch/q99"]
    with pytest.raises(queries.QueryError, match="manifest says"):
        queries.render(q, 1, "df")


def test_orphan_override_is_rejected(qdir):
    d = qdir / "overrides" / "datafusion" / "tpch"
    d.mkdir(parents=True)
    (d / "q42.sql").write_text("SELECT 1\n")
    with pytest.raises(queries.QueryError, match="no manifest entry"):
        manifest(qdir, None)


def test_real_manifest_overrides_have_reasons():
    for q in queries.load_manifest().values():
        if q.datafusion_override:
            assert len(q.datafusion_override) > 20, q.key
            assert q.override_path("datafusion").is_file()
