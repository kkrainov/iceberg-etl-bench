"""Differ rules (methodology section 8): floats, decimals, CHAR, NULLs, ordering, ties, truncation."""

from m0 import differ
from m0.queries import Query


def answer(engine, types, rows, row_count=None, truncated=False, qid="q01"):
    return {"engine": engine, "suite": "tpch", "query_id": qid,
            "columns": [{"name": f"c{i}", "type": t} for i, t in enumerate(types)],
            "row_count": len(rows) if row_count is None else row_count, "truncated": truncated, "rows": rows}


def cmp(ref_types, ref_rows, got_types, got_rows, **kw):
    return differ.compare(answer("trino", ref_types, ref_rows), answer("df", got_types, got_rows), **kw)


# ---- floats ----

def test_float_within_relative_tolerance_matches():
    d = cmp(["double"], [[1000000.0]], ["Float64"], [[1000000.5]], ordered=False)
    assert d.status == "match" and "float_tolerance" in d.rules


def test_float_within_absolute_tolerance_matches_near_zero():
    assert cmp(["double"], [[0.0]], ["Float64"], [[1e-10]], ordered=False).status == "match"


def test_float_outside_tolerance_mismatches():
    d = cmp(["double"], [[1.0]], ["Float64"], [[1.00001]], ordered=False)
    assert d.status == "mismatch" and "col 1" in d.detail


def test_nan_and_infinity_strings_compare_equal():
    assert cmp(["double"], [["NaN"], ["Infinity"]], ["Float64"], [["NaN"], ["Infinity"]], ordered=False).status == "match"


def test_decimal_against_float_uses_float_tolerance():
    assert cmp(["decimal(12, 2)"], [["25.52"]], ["Float64"], [[25.5200000001]], ordered=False).status == "match"


# ---- decimals ----

def test_higher_scale_is_rounded_half_up_to_the_reference_scale():
    # Trino avg(decimal(12,2)) rounds to scale 2; DataFusion returns scale 6.
    d = cmp(["decimal(12, 2)", "decimal(12, 2)"], [["25.52", "0.05"]],
            ["Decimal128(16, 6)", "Decimal128(16, 6)"], [["25.522005", "0.049985"]], ordered=False)
    assert d.status == "match" and "decimal_round" in d.rules


def test_half_up_not_half_even():
    col = differ.column_rule("decimal(7, 2)", "Decimal128(11, 6)")
    assert str(differ.normalise("0.125000", col, set())) == "0.13"
    assert str(differ.normalise("-0.125000", col, set())) == "-0.13"


def test_same_value_other_scale_matches():
    d = cmp(["decimal(7, 2)"], [["13.15"]], ["Decimal128(11, 6)"], [["13.150000"]], ordered=False)
    assert d.status == "match" and d.rules == ["decimal_scale"]


def test_lower_other_scale_compares_at_that_scale():
    d = cmp(["decimal(35, 20)"], [["0.61244019138755980861"]], ["Decimal128(23, 8)"], [["0.61244019"]], ordered=False)
    assert d.status == "match" and "decimal_lower_scale" in d.rules


def test_one_unit_in_the_last_place_matches_two_do_not():
    # Trino rounds a division half up, DataFusion truncates it.
    d = cmp(["decimal(38, 6)"], [["0.968966"]], ["Decimal128(38, 6)"], [["0.968965"]], ordered=False)
    assert d.status == "match" and "decimal_ulp" in d.rules
    assert cmp(["decimal(38, 6)"], [["0.968966"]], ["Decimal128(38, 6)"], [["0.968964"]], ordered=False).status \
        == "mismatch"


def test_exact_sum_difference_mismatches():
    d = cmp(["decimal(38, 2)"], [["56586554400.73"]], ["Decimal128(22, 2)"], [["56586554390.73"]], ordered=False)
    assert d.status == "mismatch"


def test_integer_reference_against_decimal():
    assert cmp(["bigint"], [[42]], ["Decimal128(20, 0)"], [["42"]], ordered=False).status == "match"


# ---- strings, NULLs ----

def test_char_trailing_spaces_stripped():
    d = cmp(["char(10)"], [["Dr.       "]], ["Utf8"], [["Dr."]], ordered=False)
    assert d.status == "match" and d.rules == ["char_strip"]


def test_varchar_trailing_spaces_matter():
    assert cmp(["varchar"], [["Dr. "]], ["Utf8"], [["Dr."]], ordered=False).status == "mismatch"


def test_null_equals_null_but_not_a_value():
    assert cmp(["varchar", "decimal(7, 2)"], [[None, None]], ["Utf8", "Decimal128(7, 2)"], [[None, None]],
               ordered=False).status == "match"
    d = cmp(["varchar"], [[None]], ["Utf8"], [["Moore, "]], ordered=False)
    assert d.status == "mismatch" and "None" in d.detail


def test_column_and_row_counts():
    assert "columns" in cmp(["bigint"], [[1]], ["Int64", "Int64"], [[1, 2]], ordered=False).detail
    assert "rows" in cmp(["bigint"], [[1], [2]], ["Int64"], [[1]], ordered=False).detail


# ---- ordering ----

def test_unordered_compares_as_multiset():
    assert cmp(["bigint", "varchar"], [[1, "a"], [2, "b"]], ["Int64", "Utf8"], [[2, "b"], [1, "a"]],
               ordered=False).status == "match"


def test_multiset_counts_duplicates():
    assert cmp(["bigint"], [[1], [1], [2]], ["Int64"], [[1], [2], [2]], ordered=False).status == "mismatch"


def test_ordered_wrong_order_mismatches():
    d = cmp(["bigint"], [[1], [2]], ["Int64"], [[2], [1]], ordered=True, sort_keys=(1,))
    assert d.status == "mismatch" and "order differs at row 1" in d.detail


def test_ordered_only_on_sort_keys():
    # ORDER BY c0 only: rows tied on c0 may come in any order.
    ref = [[1, "a"], [1, "b"], [2, "c"]]
    got = [[1, "b"], [1, "a"], [2, "c"]]
    assert cmp(["bigint", "varchar"], ref, ["Int64", "Utf8"], got, ordered=True, sort_keys=(1,)).status == "match"
    assert cmp(["bigint", "varchar"], ref, ["Int64", "Utf8"], got, ordered=True).status == "mismatch"


def test_null_sort_key_placement_is_implementation_defined():
    # ORDER BY c0 DESC: Trino puts NULL last, DataFusion first.
    ref = [[3], [2], [None]]
    got = [[None], [3], [2]]
    d = cmp(["bigint"], ref, ["Int64"], got, ordered=True, sort_keys=(1,))
    assert d.status == "match" and "null_order" in d.rules
    assert cmp(["bigint"], [[3], [2], [None]], ["Int64"], [[None], [2], [3]], ordered=True,
               sort_keys=(1,)).status == "mismatch"


# ---- ties ----

def test_tie_ambiguous_compares_sort_keys_only():
    # LIMIT 2 over ORDER BY c0 with a tie at the cut: engines keep different rows.
    ref = [[1, "a"], [2, "b"]]
    got = [[1, "a"], [2, "z"]]
    assert cmp(["bigint", "varchar"], ref, ["Int64", "Utf8"], got, ordered=True, sort_keys=(1,)).status == "mismatch"
    d = cmp(["bigint", "varchar"], ref, ["Int64", "Utf8"], got, ordered=True, sort_keys=(1,), tie_ambiguous=True)
    assert d.status == "match" and "sort_keys_only" in d.rules
    bad = [[1, "a"], [3, "b"]]
    assert cmp(["bigint", "varchar"], ref, ["Int64", "Utf8"], bad, ordered=True, sort_keys=(1,),
               tie_ambiguous=True).status == "mismatch"


# ---- truncation ----

def test_truncated_unordered_is_unverified_unless_counts_differ():
    ref = answer("trino", ["bigint"], [[1], [2]], row_count=5, truncated=True)
    got = answer("df", ["Int64"], [[3], [4]], row_count=5, truncated=True)
    assert differ.compare(ref, got, ordered=False).status == "unverified"
    got["row_count"] = 6
    assert differ.compare(ref, got, ordered=False).status == "mismatch"


def test_truncated_ordered_compares_the_prefix():
    ref = answer("trino", ["bigint"], [[1], [2]], row_count=5, truncated=True)
    got = answer("df", ["Int64"], [[1], [2], [3]], row_count=5, truncated=True)
    d = differ.compare(ref, got, ordered=True, sort_keys=(1,))
    assert d.status == "match" and "truncated_prefix" in d.rules and "first 2 of 5" in d.detail
    got["rows"] = [[1], [9]]
    assert differ.compare(ref, got, ordered=True, sort_keys=(1,)).status == "mismatch"


# ---- run level ----

def manifest_entry(**kw) -> dict:
    q = Query("tpch", "q01", "tpc", ("tpch",), kw.get("ordered", False), False, None, "test",
              sort_keys=kw.get("sort_keys", ()))
    return {"tpch/q01": q}


def test_diff_answers_reference_and_no_reference():
    answers = [answer("trino", ["bigint"], [[1]]), answer("df", ["Int64"], [[1]])]
    ds = differ.diff_answers(answers, manifest_entry())
    assert [(d.engine, d.status, d.reference) for d in ds] == [("df", "match", "trino")]
    ds = differ.diff_answers([answer("df", ["Int64"], [[1]]), answer("dfd", ["Int64"], [[2]])], manifest_entry())
    assert {d.status for d in ds} == {"no_reference"}
    assert "vs dfd: mismatch" in ds[0].detail
