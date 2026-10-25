-- TPC-H Q22. TPC-H spec v3.0.1 section 2.4.22, validation substitution parameters.
-- Source: apache/datafusion@36f393559620:benchmarks/queries/q22.sql (Apache-2.0); tables prefixed with ${tpch}.
select
    cntrycode,
    count(*) as numcust,
    sum(c_acctbal) as totacctbal
from
    (
        select
            substring(c_phone from 1 for 2) as cntrycode,
            c_acctbal
        from
            ${tpch}.customer
        where
                substring(c_phone from 1 for 2) in
                ('13', '31', '23', '29', '30', '18', '17')
          and c_acctbal > (
            select
                avg(c_acctbal)
            from
                ${tpch}.customer
            where
                    c_acctbal > 0.00
              and substring(c_phone from 1 for 2) in
                  ('13', '31', '23', '29', '30', '18', '17')
        )
          and not exists (
                select
                    *
                from
                    ${tpch}.orders
                where
                        o_custkey = c_custkey
            )
    ) as custsale
group by
    cntrycode
order by
    cntrycode
