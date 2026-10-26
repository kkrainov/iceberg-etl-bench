-- TPC-H Q11. TPC-H spec v3.0.1 section 2.4.11, validation substitution parameters.
-- Source: apache/datafusion@36f393559620:benchmarks/queries/q11.sql (Apache-2.0); tables prefixed with ${tpch}.
-- FRACTION is 0.0001 / SF (spec 2.4.11.3), filled in by the harness from the plan's scale factor.
select
    ps_partkey,
    sum(ps_supplycost * ps_availqty) as value
from
    ${tpch}.partsupp,
    ${tpch}.supplier,
    ${tpch}.nation
where
    ps_suppkey = s_suppkey
  and s_nationkey = n_nationkey
  and n_name = 'GERMANY'
group by
    ps_partkey having
    sum(ps_supplycost * ps_availqty) > (
    select
    sum(ps_supplycost * ps_availqty) * ${tpch_q11_fraction}
    from
    ${tpch}.partsupp,
    ${tpch}.supplier,
    ${tpch}.nation
    where
    ps_suppkey = s_suppkey
                  and s_nationkey = n_nationkey
                  and n_name = 'GERMANY'
    )
order by
    value desc
