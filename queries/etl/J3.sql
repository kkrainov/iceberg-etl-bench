-- B1 J3: skewed join, store_sales_skewed (Zipf-like skew_customer_sk) with the
-- customer dimension, aggregated to one row per birth country. Written for this
-- benchmark (methodology section 4, B1 catalogue).
SELECT
    c.c_birth_country,
    count(*) AS sales,
    sum(s.ss_net_paid) AS net_paid
FROM ${etl}.store_sales_skewed s
JOIN ${tpcds}.customer c ON s.skew_customer_sk = c.c_customer_sk
GROUP BY c.c_birth_country
ORDER BY c.c_birth_country
