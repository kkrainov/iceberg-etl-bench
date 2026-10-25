-- TPC-H Q20. TPC-H spec v3.0.1 section 2.4.20, validation substitution parameters.
-- Source: apache/datafusion@36f393559620:benchmarks/queries/q20.sql (Apache-2.0); tables prefixed with ${tpch}.
select
    s_name,
    s_address
from
    ${tpch}.supplier,
    ${tpch}.nation
where
        s_suppkey in (
        select
            ps_suppkey
        from
            ${tpch}.partsupp
        where
                ps_partkey in (
                select
                    p_partkey
                from
                    ${tpch}.part
                where
                        p_name like 'forest%'
            )
          and ps_availqty > (
            select
                    0.5 * sum(l_quantity)
            from
                ${tpch}.lineitem
            where
                    l_partkey = ps_partkey
              and l_suppkey = ps_suppkey
              and l_shipdate >= date '1994-01-01'
              and l_shipdate < date '1994-01-01' + interval '1' year
        )
    )
  and s_nationkey = n_nationkey
  and n_name = 'CANADA'
order by
    s_name
