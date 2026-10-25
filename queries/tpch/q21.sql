-- TPC-H Q21. TPC-H spec v3.0.1 section 2.4.21, validation substitution parameters.
-- Source: apache/datafusion@36f393559620:benchmarks/queries/q21.sql (Apache-2.0); tables prefixed with ${tpch}.
select
    s_name,
    count(*) as numwait
from
    ${tpch}.supplier,
    ${tpch}.lineitem l1,
    ${tpch}.orders,
    ${tpch}.nation
where
        s_suppkey = l1.l_suppkey
  and o_orderkey = l1.l_orderkey
  and o_orderstatus = 'F'
  and l1.l_receiptdate > l1.l_commitdate
  and exists (
        select
            *
        from
            ${tpch}.lineitem l2
        where
                l2.l_orderkey = l1.l_orderkey
          and l2.l_suppkey <> l1.l_suppkey
    )
  and not exists (
        select
            *
        from
            ${tpch}.lineitem l3
        where
                l3.l_orderkey = l1.l_orderkey
          and l3.l_suppkey <> l1.l_suppkey
          and l3.l_receiptdate > l3.l_commitdate
    )
  and s_nationkey = n_nationkey
  and n_name = 'SAUDI ARABIA'
group by
    s_name
order by
    numwait desc,
    s_name
limit 100
