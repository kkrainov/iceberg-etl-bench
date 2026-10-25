"""Result differ (methodology section 8): each engine's answer against Trino's.

Answers come from `answers.jsonl` (canonical JSON values plus each engine's type
names). Diffs are computed on demand, never stored. Rules:

- Columns by position; names are ignored. Column counts must match.
- `CHAR` values: trailing spaces stripped.
- Decimals: compared at the reference column's scale, or at the other column's scale
  if that is lower. The more precise value is rounded half up to that scale first
  (DataFusion's `avg` returns scale 6 where Trino rounds to the input scale; DataFusion's
  decimal division keeps scale 8 where Trino keeps more). The two may then differ by
  one unit in the last place: Trino rounds division results half up, DataFusion
  truncates them (the SQL standard leaves both the scale of a division and rounding
  versus truncation to the implementation).
- Floats (`double`, `real`, or either side a float type): equal if |a-b| <= 1e-9 or
  |a-b| / max(|a|,|b|) <= 1e-6. A decimal compared with a float uses this rule.
- NULL equals NULL. Timestamps are already UTC in the canonical encoding.
- Rows are compared as multisets. Ordered queries must also give the same sequence of
  sort-key values (manifest `sort_keys`, all columns if none are given). Where NULLs
  sort is implementation-defined (Trino puts them last, DataFusion first in DESC), so
  if the sequences differ only by rows with a NULL sort key, the order still matches.
- `tie_ambiguous` queries (LIMIT with possible ties at the cut) are compared on their
  sort-key columns only.
- Truncated answers (`result_rows_cap`): different row counts are a mismatch. With
  equal counts, an ordered query compares the kept prefix; an unordered one is
  `unverified` (the kept rows are an arbitrary subset).
"""

import math
import re
from dataclasses import asdict, dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

REFERENCE = "trino"
FLOAT_ABS, FLOAT_REL = 1e-9, 1e-6
# Statuses, in report order.
MATCH, MISMATCH, UNVERIFIED, NO_REFERENCE = "match", "mismatch", "unverified", "no_reference"
STATUSES = (MATCH, MISMATCH, UNVERIFIED, NO_REFERENCE)
DETAIL_CAP = 200

_DECIMAL_TYPE = re.compile(r"decimal(?:128|256)?\s*\(\s*\d+\s*,\s*(\d+)\s*\)", re.I)
_FLOAT_TYPES = ("double", "real", "float")
_INT_TYPES = ("tinyint", "smallint", "integer", "bigint", "int8", "int16", "int32", "int64", "uint")


@dataclass
class Diff:
    engine: str
    suite: str
    query_id: str
    status: str
    reference: str | None = None   # engine compared against
    detail: str = ""               # first difference, or why not compared
    rules: list[str] = field(default_factory=list)  # normalisations that changed a value

    def to_dict(self) -> dict:
        return asdict(self)


# ---- column kinds ----

def decimal_scale(type_name: str | None) -> int | None:
    m = _DECIMAL_TYPE.search(type_name or "")
    return int(m.group(1)) if m else None


def is_float_type(type_name: str | None) -> bool:
    t = (type_name or "").lower()
    return t.startswith(_FLOAT_TYPES)


def is_char_type(type_name: str | None) -> bool:
    return (type_name or "").lower().startswith("char")


def is_int_type(type_name: str | None) -> bool:
    return (type_name or "").lower().startswith(_INT_TYPES)


@dataclass
class Column:
    """How one column is compared: `float`, `decimal` (at `scale`), `char` or `exact`."""

    kind: str
    scale: int | None = None


def column_rule(ref_type: str | None, got_type: str | None) -> Column:
    if is_float_type(ref_type) or is_float_type(got_type):
        return Column("float")
    scale, other = decimal_scale(ref_type), decimal_scale(got_type)
    if scale is not None:
        return Column("decimal", min(scale, other) if other is not None else scale)
    if is_int_type(ref_type) and other is not None:
        return Column("decimal", 0)
    if is_char_type(ref_type) or is_char_type(got_type):
        return Column("char")
    return Column("exact")


# ---- values ----

def _freeze(v):
    return tuple(_freeze(x) for x in v) if isinstance(v, list) else v


def _to_decimal(v) -> Decimal | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, str)):
        try:
            return Decimal(v)
        except InvalidOperation:
            return None
    if isinstance(v, float) and math.isfinite(v):
        return Decimal(repr(v))
    return None


def normalise(v, col: Column, rules: set[str], ref: bool = False):
    """A comparable, hashable form of one value (`rules` collects what changed it;
    `ref` marks the reference side)."""
    if v is None:
        return None
    if col.kind == "float":
        if isinstance(v, str):
            try:
                return float(v)  # decimal string, or "NaN"/"Infinity"
            except ValueError:
                return v
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else _freeze(v)
    if col.kind == "decimal":
        d = _to_decimal(v)
        if d is None:
            return _freeze(v)
        q = d.quantize(Decimal(1).scaleb(-col.scale), rounding=ROUND_HALF_UP)
        if q != d:
            rules.add("decimal_lower_scale" if ref else "decimal_round")
        elif not ref and isinstance(v, str) and -d.as_tuple().exponent != col.scale:
            rules.add("decimal_scale")
        return q
    if col.kind == "char" and isinstance(v, str):
        s = v.rstrip(" ")
        if s != v:
            rules.add("char_strip")
        return s
    return _freeze(v)


def values_equal(a, b, col: Column, rules: set[str] | None = None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if col.kind == "float" and isinstance(a, float) and isinstance(b, float):
        if a == b or (math.isnan(a) and math.isnan(b)):
            return True
        diff = abs(a - b)
        ok = diff <= FLOAT_ABS or diff / max(abs(a), abs(b)) <= FLOAT_REL
        if ok and rules is not None:
            rules.add("float_tolerance")
        return ok
    if col.kind == "decimal" and isinstance(a, Decimal) and isinstance(b, Decimal) and a != b:
        ok = abs(a - b) == Decimal(1).scaleb(-col.scale)
        if ok and rules is not None:
            rules.add("decimal_ulp")
        return ok
    return a == b


def _sort_key(row: tuple) -> tuple:
    """Total order over mixed values (None first, then numbers, then strings, then the rest)."""
    out = []
    for v in row:
        if v is None:
            out.append((0, 0))
        elif isinstance(v, bool):
            out.append((1, int(v)))
        elif isinstance(v, (int, float, Decimal)):
            out.append((1, v if not (isinstance(v, float) and math.isnan(v)) else math.inf))
        elif isinstance(v, str):
            out.append((2, v))
        else:
            out.append((3, repr(v)))
    return tuple(out)


def _show(v) -> str:
    if isinstance(v, Decimal):
        return str(v)
    return repr(v)


def _row_text(row) -> str:
    text = "[" + ", ".join(_show(v) for v in row) + "]"
    return text if len(text) <= 70 else text[:67] + "..."


# ---- comparison ----

def rows_equal(a: tuple, b: tuple, cols: list[Column], rules: set[str]) -> int | None:
    """None if equal, else the index of the first differing column."""
    for i, (x, y, c) in enumerate(zip(a, b, cols, strict=True)):
        if not values_equal(x, y, c, rules):
            return i
    return None


def order_difference(ka: list[tuple], kb: list[tuple], cols: list[Column], rules: set[str]) -> int | None:
    """Index of the first position where two sort-key sequences differ, or None."""
    for i, (a, b) in enumerate(zip(ka, kb, strict=True)):
        if rows_equal(a, b, cols, rules) is not None:
            return i
    return None


def compare(ref: dict, got: dict, ordered: bool, sort_keys: tuple[int, ...] = (),
            tie_ambiguous: bool = False) -> Diff:
    """Compare two answers.jsonl records; `ref` is the reference."""
    out = Diff(got["engine"], got["suite"], got["query_id"], MATCH, ref["engine"])
    rname, gname = ref["engine"], got["engine"]
    rcols, gcols = ref["columns"], got["columns"]
    if len(rcols) != len(gcols):
        out.status, out.detail = MISMATCH, f"{len(rcols)} columns in {rname}, {len(gcols)} in {gname}"
        return out
    if ref["row_count"] != got["row_count"]:
        out.status, out.detail = MISMATCH, f"{ref['row_count']} rows in {rname}, {got['row_count']} in {gname}"
        return out
    rules: set[str] = set()
    cols = [column_rule(r.get("type"), g.get("type")) for r, g in zip(rcols, gcols, strict=True)]
    names = [c.get("name") for c in rcols]
    keys = [k - 1 for k in sort_keys if k <= len(cols)] or list(range(len(cols)))
    rrows = [tuple(normalise(v, c, rules, ref=True) for v, c in zip(r, cols, strict=True)) for r in ref["rows"]]
    grows = [tuple(normalise(v, c, rules) for v, c in zip(r, cols, strict=True)) for r in got["rows"]]

    truncated = ref.get("truncated") or got.get("truncated")
    if truncated:
        if not ordered:
            out.status = UNVERIFIED
            out.detail = (f"answer truncated ({len(rrows)} of {ref['row_count']} rows kept in {rname}, "
                          f"{len(grows)} in {gname}); row counts equal, unordered rows not comparable")
            return out
        n = min(len(rrows), len(grows))
        rrows, grows = rrows[:n], grows[:n]
        rules.add("truncated_prefix")

    if tie_ambiguous:
        rrows = [tuple(r[k] for k in keys) for r in rrows]
        grows = [tuple(r[k] for k in keys) for r in grows]
        cols = [cols[k] for k in keys]
        names = [names[k] for k in keys]
        keys = list(range(len(keys)))
        rules.add("sort_keys_only")

    # Multiset: sort both sides by a total order and compare pairwise.
    rs, gs = sorted(rrows, key=_sort_key), sorted(grows, key=_sort_key)
    for i, (a, b) in enumerate(zip(rs, gs, strict=True)):
        col = rows_equal(a, b, cols, rules)
        if col is not None:
            out.status = MISMATCH
            out.detail = (f"rows differ (multiset, sorted row {i + 1}); col {col + 1} {names[col]}: "
                          f"{rname} {_show(a[col])} vs {gname} {_show(b[col])}; "
                          f"{rname} {_row_text(a)}")
            break
    if out.status == MATCH and ordered:
        kc = [cols[k] for k in keys]
        ka = [tuple(r[k] for k in keys) for r in rrows]
        kb = [tuple(r[k] for k in keys) for r in grows]
        bad = order_difference(ka, kb, kc, rules)
        if bad is not None:
            # NULL placement is implementation-defined: compare the rows without NULL keys.
            nn_a = [k for k in ka if None not in k]
            nn_b = [k for k in kb if None not in k]
            if len(nn_a) < len(ka) and order_difference(nn_a, nn_b, kc, rules) is None:
                rules.add("null_order")
            else:
                out.status = MISMATCH
                out.detail = (f"same rows, order differs at row {bad + 1}: sort keys {rname} {_row_text(ka[bad])} "
                              f"vs {gname} {_row_text(kb[bad])}")
    out.detail = out.detail[:DETAIL_CAP]
    out.rules = sorted(rules)
    if out.status == MATCH and truncated:
        out.detail = f"compared the first {len(rrows)} of {ref['row_count']} rows (truncated, ordered)"
    return out


def diff_answers(answers: list[dict], manifest: dict) -> list[Diff]:
    """One Diff per non-reference engine x query with an answer, in a stable order.

    The reference is Trino's answer. Without one, the engine is `no_reference`; if
    another non-reference engine answered too, the detail says whether they agree.
    """
    by_query: dict[tuple[str, str], dict[str, dict]] = {}
    for a in answers:
        by_query.setdefault((a["suite"], a["query_id"]), {})[a["engine"]] = a
    out = []
    for (suite, qid), engines in sorted(by_query.items()):
        q = manifest.get(f"{suite}/{qid}")
        ordered = bool(q and q.ordered)
        sort_keys = q.sort_keys if q else ()
        tie = bool(q and q.tie_ambiguous)
        ref = engines.get(REFERENCE)
        others = [e for e in sorted(engines) if e != REFERENCE]
        for engine in others:
            if ref is not None:
                out.append(compare(ref, engines[engine], ordered, sort_keys, tie))
                continue
            d = Diff(engine, suite, qid, NO_REFERENCE, None, f"{REFERENCE} has no answer")
            peer = next((e for e in others if e != engine), None)
            if peer is not None:
                vs = compare(engines[peer], engines[engine], ordered, sort_keys, tie)
                d.detail += f"; vs {peer}: {vs.status}" + (f" ({vs.detail})" if vs.status != MATCH else "")
                d.detail = d.detail[:DETAIL_CAP]
            out.append(d)
    return out
