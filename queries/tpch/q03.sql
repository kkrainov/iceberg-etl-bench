-- TPC-H Q3, Shipping Priority. TPC-H spec v3.0.1 section 2.4.3, validation
-- substitution parameters SEGMENT = BUILDING, DATE = 1995-03-15. Trino dialect.
SELECT
    l_orderkey,
    sum(l_extendedprice * (1 - l_discount)) AS revenue,
    o_orderdate,
    o_shippriority
FROM ${tpch}.customer, ${tpch}.orders, ${tpch}.lineitem
WHERE c_mktsegment = 'BUILDING'
  AND c_custkey = o_custkey
  AND l_orderkey = o_orderkey
  AND o_orderdate < DATE '1995-03-15'
  AND l_shipdate > DATE '1995-03-15'
GROUP BY l_orderkey, o_orderdate, o_shippriority
ORDER BY revenue DESC, o_orderdate
LIMIT 10
