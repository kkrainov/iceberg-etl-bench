-- TPC-H Q13. TPC-H spec v3.0.1 section 2.4.13, validation substitution parameters.
-- Source: apache/datafusion@36f393559620:benchmarks/queries/q13.sql (Apache-2.0); tables prefixed with ${tpch}.
select
    c_count,
    count(*) as custdist
from
    (
        select
            c_custkey,
            count(o_orderkey)
        from
            ${tpch}.customer left outer join ${tpch}.orders on
                        c_custkey = o_custkey
                    and o_comment not like '%special%requests%'
        group by
            c_custkey
    ) as c_orders (c_custkey, c_count)
group by
    c_count
order by
    custdist desc,
    c_count desc
