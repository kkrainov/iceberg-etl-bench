-- TPC-H Q4. TPC-H spec v3.0.1 section 2.4.4, validation substitution parameters.
-- Source: apache/datafusion@36f393559620:benchmarks/queries/q4.sql (Apache-2.0); tables prefixed with ${tpch}.
-- Changed: the string literal compared with o_orderdate is a DATE literal (Trino has no implicit varchar-to-date cast).
select
    o_orderpriority,
    count(*) as order_count
from
    ${tpch}.orders
where
        o_orderdate >= date '1993-07-01'
  and o_orderdate < date '1993-07-01' + interval '3' month
  and exists (
        select
            *
        from
            ${tpch}.lineitem
        where
                l_orderkey = o_orderkey
          and l_commitdate < l_receiptdate
    )
group by
    o_orderpriority
order by
    o_orderpriority
