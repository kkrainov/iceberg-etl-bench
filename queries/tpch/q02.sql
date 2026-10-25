-- TPC-H Q2. TPC-H spec v3.0.1 section 2.4.2, validation substitution parameters.
-- Source: apache/datafusion@36f393559620:benchmarks/queries/q2.sql (Apache-2.0); tables prefixed with ${tpch}.
select
    s_acctbal,
    s_name,
    n_name,
    p_partkey,
    p_mfgr,
    s_address,
    s_phone,
    s_comment
from
    ${tpch}.part,
    ${tpch}.supplier,
    ${tpch}.partsupp,
    ${tpch}.nation,
    ${tpch}.region
where
        p_partkey = ps_partkey
  and s_suppkey = ps_suppkey
  and p_size = 15
  and p_type like '%BRASS'
  and s_nationkey = n_nationkey
  and n_regionkey = r_regionkey
  and r_name = 'EUROPE'
  and ps_supplycost = (
    select
        min(ps_supplycost)
    from
        ${tpch}.partsupp,
        ${tpch}.supplier,
        ${tpch}.nation,
        ${tpch}.region
    where
            p_partkey = ps_partkey
      and s_suppkey = ps_suppkey
      and s_nationkey = n_nationkey
      and n_regionkey = r_regionkey
      and r_name = 'EUROPE'
)
order by
    s_acctbal desc,
    n_name,
    s_name,
    p_partkey
limit 100
