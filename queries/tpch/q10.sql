-- TPC-H Q10. TPC-H spec v3.0.1 section 2.4.10, validation substitution parameters.
-- Source: apache/datafusion@36f393559620:benchmarks/queries/q10.sql (Apache-2.0); tables prefixed with ${tpch}.
select
    c_custkey,
    c_name,
    sum(l_extendedprice * (1 - l_discount)) as revenue,
    c_acctbal,
    n_name,
    c_address,
    c_phone,
    c_comment
from
    ${tpch}.customer,
    ${tpch}.orders,
    ${tpch}.lineitem,
    ${tpch}.nation
where
        c_custkey = o_custkey
  and l_orderkey = o_orderkey
  and o_orderdate >= date '1993-10-01'
  and o_orderdate < date '1993-10-01' + interval '3' month
  and l_returnflag = 'R'
  and c_nationkey = n_nationkey
group by
    c_custkey,
    c_name,
    c_acctbal,
    c_phone,
    n_name,
    c_address,
    c_comment
order by
    revenue desc
limit 20
