"""Read-only access to the Iceberg REST catalog (Lakekeeper), independent of any engine.

Used for checks that must not depend on the engine under test: which namespaces
and tables exist, and each table's current Iceberg schema.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from functools import cache

CATALOG_URL = "http://127.0.0.1:8181/catalog"
WAREHOUSE = "m0"


class CatalogError(Exception):
    pass


def _get(path: str, timeout: float = 30) -> dict:
    try:
        with urllib.request.urlopen(CATALOG_URL + path, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise CatalogError(f"catalog GET {path}: HTTP {e.code} {e.read()[:300]!r}") from e
    except (OSError, ValueError) as e:
        raise CatalogError(f"catalog GET {path}: {e}") from e


@cache
def prefix(warehouse: str = WAREHOUSE) -> str:
    cfg = _get(f"/v1/config?warehouse={urllib.parse.quote(warehouse)}")
    merged = {**cfg.get("defaults", {}), **cfg.get("overrides", {})}
    if "prefix" not in merged:
        raise CatalogError(f"catalog config for warehouse {warehouse!r} has no prefix: {cfg}")
    return merged["prefix"]


def namespaces() -> set[str]:
    body = _get(f"/v1/{prefix()}/namespaces")
    return {".".join(ns) for ns in body.get("namespaces", [])}


def tables(namespace: str) -> list[str]:
    out, token = [], None
    while True:
        query = f"?pageToken={urllib.parse.quote(token)}" if token else ""
        body = _get(f"/v1/{prefix()}/namespaces/{urllib.parse.quote(namespace)}/tables{query}")
        out += [i["name"] for i in body.get("identifiers", [])]
        token = body.get("next-page-token")
        if not token:
            return sorted(out)


def table_schema(namespace: str, table: str) -> list[tuple[str, str]]:
    """(column name, Iceberg type) of the table's current schema, top-level fields only."""
    body = _get(f"/v1/{prefix()}/namespaces/{urllib.parse.quote(namespace)}/tables/{urllib.parse.quote(table)}")
    meta = body["metadata"]
    schema = next(s for s in meta["schemas"] if s["schema-id"] == meta["current-schema-id"])
    return [(f["name"], f["type"] if isinstance(f["type"], str) else f["type"].get("type", "nested"))
            for f in schema["fields"]]
