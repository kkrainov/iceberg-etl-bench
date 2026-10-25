-- TPC-H Q14. TPC-H spec v3.0.1 section 2.4.14, validation substitution parameters.
-- Source: apache/datafusion@36f393559620:benchmarks/queries/q14.sql (Apache-2.0); tables prefixed with ${tpch}.
select
            100.00 * sum(case
                             when p_type like 'PROMO%'
                                 then l_extendedprice * (1 - l_discount)
                             else 0
            end) / sum(l_extendedprice * (1 - l_discount)) as promo_revenue
from
    ${tpch}.lineitem,
    ${tpch}.part
where
        l_partkey = p_partkey
  and l_shipdate >= date '1995-09-01'
  and l_shipdate < date '1995-09-01' + interval '1' month
