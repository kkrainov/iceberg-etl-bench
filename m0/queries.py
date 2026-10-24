"""Query catalogue: `queries/manifest.yaml` plus canonical Trino SQL per query."""

import re
import string
from dataclasses import dataclass

import yaml

from m0.paths import QUERIES_DIR

MANIFEST = QUERIES_DIR / "manifest.yaml"
CATALOG = "iceberg"
NAMESPACES = ("tpch", "tpcds", "etl")
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

    @property
    def key(self) -> str:
        return f"{self.suite}/{self.id}"

    @property
    def path(self):
        return QUERIES_DIR / self.suite / f"{self.id}.sql"


def load_manifest(path=MANIFEST) -> dict[str, Query]:
    with open(path) as f:
        entries = yaml.safe_load(f)["queries"]
    out = {}
    for e in entries:
        q = Query(e["suite"], str(e["id"]), e["class"], tuple(e["namespaces"]), bool(e["ordered"]),
                  bool(e["tie_ambiguous"]), e.get("rewrite"), e["source"])
        if not ID_RE.fullmatch(q.id) or q.suite not in NAMESPACES:
            raise QueryError(f"bad query id or suite: {q.key}")
        if q.key in out:
            raise QueryError(f"duplicate query {q.key}")
        out[q.key] = q
    return out


def placeholders(sql: str) -> set[str]:
    return {m.group("named") or m.group("braced") for m in string.Template.pattern.finditer(sql)
            if m.group("named") or m.group("braced")}


def render(q: Query, sf: int) -> str:
    """SQL text with namespace placeholders replaced for scale factor `sf`."""
    if not q.path.is_file():
        raise QueryError(f"missing SQL file {q.path}")
    text = q.path.read_text()
    used = placeholders(text)
    if used != set(q.namespaces):
        raise QueryError(f"{q.key}: SQL uses {sorted(used)}, manifest says {sorted(q.namespaces)}")
    sql = string.Template(text).substitute({ns: f"{CATALOG}.{ns}_sf{sf}" for ns in NAMESPACES})
    # Strip comment-only lines and the trailing semicolon: one statement per file.
    lines = [line for line in sql.splitlines() if not line.lstrip().startswith("--")]
    return "\n".join(lines).strip().rstrip(";")
