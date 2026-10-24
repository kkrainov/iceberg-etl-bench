-- TPC-H Q6, Forecasting Revenue Change. TPC-H spec v3.0.1 section 2.4.6, validation
-- substitution parameters DATE = 1994-01-01, DISCOUNT = 0.06, QUANTITY = 24. Trino dialect.
SELECT sum(l_extendedprice * l_discount) AS revenue
FROM ${tpch}.lineitem
WHERE l_shipdate >= DATE '1994-01-01'
  AND l_shipdate < DATE '1994-01-01' + INTERVAL '1' YEAR
  AND l_discount BETWEEN 0.06 - 0.01 AND 0.06 + 0.01
  AND l_quantity < 24
