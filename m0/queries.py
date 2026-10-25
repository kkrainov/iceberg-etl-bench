"""Query catalogue: `queries/manifest.yaml` plus canonical Trino SQL per query.

A DataFusion override, `queries/overrides/datafusion/<suite>/<id>.sql`, replaces the
canonical text for the DataFusion engines (`df`, `dfd`, `dfd-jo`). It is allowed only
for a mechanical rewrite (function name, syntax) that keeps the semantics, and the
manifest must give the reason (`datafusion_override`). A file without a reason, or a
reason without a file, is an error.
"""

import re
import string
from dataclasses import dataclass

import yaml

from m0.paths import QUERIES_DIR

MANIFEST = QUERIES_DIR / "manifest.yaml"
CATALOG = "iceberg"
NAMESPACES = ("tpch", "tpcds", "etl")
OVERRIDES_DIR = QUERIES_DIR / "overrides"
# SQL dialect family per engine; None = canonical (Trino) text.
DIALECT = {"trino": None, "df": "datafusion", "dfd": "datafusion", "dfd-jo": "datafusion"}
ID_RE = re.compile(r"[A-Za-z0-9_]+")


class QueryError(Exception):
    pass


@dataclass(frozen=True)
class Query:
    suite: str
    id: str
    cls: str
    namespaces: tuple[str, ...]
    ordered: bool
    tie_ambiguous: bool
    rewrite: str | None
    source: str
    datafusion_override: str | None = None  # reason for queries/overrides/datafusion/<suite>/<id>.sql
    sort_keys: tuple[int, ...] = ()  # result columns (1-based) of the final ORDER BY, in order

    @property
    def key(self) -> str:
        return f"{self.suite}/{self.id}"

    @property
    def path(self):
        return QUERIES_DIR / self.suite / f"{self.id}.sql"

    def override_path(self, dialect: str):
        return OVERRIDES_DIR / dialect / self.suite / f"{self.id}.sql"

    def path_for(self, engine: str | None):
        """SQL file an engine runs: its dialect's override if the manifest declares one."""
        dialect = DIALECT.get(engine) if engine else None
        if dialect == "datafusion" and self.datafusion_override:
            return self.override_path(dialect)
        return self.path


def load_manifest(path=MANIFEST) -> dict[str, Query]:
    with open(path) as f:
        entries = yaml.safe_load(f)["queries"]
    out = {}
    for e in entries:
        q = Query(e["suite"], str(e["id"]), e["class"], tuple(e["namespaces"]), bool(e["ordered"]),
                  bool(e["tie_ambiguous"]), e.get("rewrite"), e["source"], e.get("datafusion_override"),
                  tuple(int(k) for k in e.get("sort_keys") or ()))
        if not ID_RE.fullmatch(q.id) or q.suite not in NAMESPACES:
            raise QueryError(f"bad query id or suite: {q.key}")
        if q.sort_keys and (not q.ordered or min(q.sort_keys) < 1 or len(set(q.sort_keys)) != len(q.sort_keys)):
            raise QueryError(f"{q.key}: sort_keys need ordered: true and distinct 1-based column positions")
        if q.tie_ambiguous and not q.sort_keys:
            raise QueryError(f"{q.key}: tie_ambiguous needs sort_keys (the columns compared)")
        has_file = q.override_path("datafusion").is_file()
        if bool(q.datafusion_override) != has_file:
            raise QueryError(f"{q.key}: datafusion override file {'exists' if has_file else 'missing'} but the "
                             f"manifest {'gives no' if has_file else 'gives a'} datafusion_override reason")
        if q.key in out:
            raise QueryError(f"duplicate query {q.key}")
        out[q.key] = q
    if OVERRIDES_DIR.is_dir():
        for f in OVERRIDES_DIR.rglob("*.sql"):
            key = f"{f.parent.name}/{f.stem}"
            if key not in out:
                raise QueryError(f"override {f.relative_to(QUERIES_DIR)} has no manifest entry")
    return out


def placeholders(sql: str) -> set[str]:
    return {m.group("named") or m.group("braced") for m in string.Template.pattern.finditer(sql)
            if m.group("named") or m.group("braced")}


def render(q: Query, sf: int, engine: str | None = None) -> str:
    """SQL text for `engine` (canonical when None) with namespaces for scale factor `sf`."""
    path = q.path_for(engine)
    if not path.is_file():
        raise QueryError(f"missing SQL file {path}")
    text = path.read_text()
    used = placeholders(text)
    if used != set(q.namespaces):
        raise QueryError(f"{q.key}: SQL uses {sorted(used)}, manifest says {sorted(q.namespaces)}")
    sql = string.Template(text).substitute({ns: f"{CATALOG}.{ns}_sf{sf}" for ns in NAMESPACES})
    # Strip comment-only lines and the trailing semicolon: one statement per file.
    lines = [line for line in sql.splitlines() if not line.lstrip().startswith("--")]
    return "\n".join(lines).strip().rstrip(";")


def render_for(q: Query, sf: int, engine: str) -> str:
    return render(q, sf, engine)
