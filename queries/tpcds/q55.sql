-- TPC-DS query 55. Template query55.tpl of the TPC-DS spec v3.2.0 with its qualification
-- substitution parameters (Appendix B): MANAGER = 28, MONTH = 11, YEAR = 1999.
-- Trino dialect.
SELECT
    i_brand_id AS brand_id,
    i_brand AS brand,
    sum(ss_ext_sales_price) AS ext_price
FROM ${tpcds}.date_dim, ${tpcds}.store_sales, ${tpcds}.item
WHERE d_date_sk = ss_sold_date_sk
  AND ss_item_sk = i_item_sk
  AND i_manager_id = 28
  AND d_moy = 11
  AND d_year = 1999
GROUP BY i_brand, i_brand_id
ORDER BY ext_price DESC, i_brand_id
LIMIT 100
