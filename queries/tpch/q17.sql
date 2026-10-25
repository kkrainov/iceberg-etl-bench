-- TPC-H Q17. TPC-H spec v3.0.1 section 2.4.17, validation substitution parameters.
-- Source: apache/datafusion@36f393559620:benchmarks/queries/q17.sql (Apache-2.0); tables prefixed with ${tpch}.
select
        sum(l_extendedprice) / 7.0 as avg_yearly
from
    ${tpch}.lineitem,
    ${tpch}.part
where
        p_partkey = l_partkey
  and p_brand = 'Brand#23'
  and p_container = 'MED BOX'
  and l_quantity < (
    select
            0.2 * avg(l_quantity)
    from
        ${tpch}.lineitem
    where
            l_partkey = p_partkey
)
