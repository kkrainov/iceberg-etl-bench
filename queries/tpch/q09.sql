-- TPC-H Q9. TPC-H spec v3.0.1 section 2.4.9, validation substitution parameters.
-- Source: apache/datafusion@36f393559620:benchmarks/queries/q9.sql (Apache-2.0); tables prefixed with ${tpch}.
select
    nation,
    o_year,
    sum(amount) as sum_profit
from
    (
        select
            n_name as nation,
            extract(year from o_orderdate) as o_year,
            l_extendedprice * (1 - l_discount) - ps_supplycost * l_quantity as amount
        from
            ${tpch}.part,
            ${tpch}.supplier,
            ${tpch}.lineitem,
            ${tpch}.partsupp,
            ${tpch}.orders,
            ${tpch}.nation
        where
                s_suppkey = l_suppkey
          and ps_suppkey = l_suppkey
          and ps_partkey = l_partkey
          and p_partkey = l_partkey
          and o_orderkey = l_orderkey
          and s_nationkey = n_nationkey
          and p_name like '%green%'
    ) as profit
group by
    nation,
    o_year
order by
    nation,
    o_year desc
