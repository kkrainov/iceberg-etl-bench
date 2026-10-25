-- TPC-H Q18. TPC-H spec v3.0.1 section 2.4.18, validation substitution parameters.
-- Source: apache/datafusion@36f393559620:benchmarks/queries/q18.sql (Apache-2.0); tables prefixed with ${tpch}.
select
    c_name,
    c_custkey,
    o_orderkey,
    o_orderdate,
    o_totalprice,
    sum(l_quantity)
from
    ${tpch}.customer,
    ${tpch}.orders,
    ${tpch}.lineitem
where
        o_orderkey in (
        select
            l_orderkey
        from
            ${tpch}.lineitem
        group by
            l_orderkey having
                sum(l_quantity) > 300
    )
  and c_custkey = o_custkey
  and o_orderkey = l_orderkey
group by
    c_name,
    c_custkey,
    o_orderkey,
    o_orderdate,
    o_totalprice
order by
    o_totalprice desc,
    o_orderdate
limit 100
