-- TPC-DS query 3. Template query3.tpl of the TPC-DS spec v3.2.0 with its qualification
-- substitution parameters (Appendix B): MONTH = 11, MANUFACT = 128,
-- AGGC = ss_ext_sales_price. Trino dialect.
SELECT
    dt.d_year,
    item.i_brand_id AS brand_id,
    item.i_brand AS brand,
    sum(ss_ext_sales_price) AS sum_agg
FROM ${tpcds}.date_dim dt, ${tpcds}.store_sales, ${tpcds}.item
WHERE dt.d_date_sk = store_sales.ss_sold_date_sk
  AND store_sales.ss_item_sk = item.i_item_sk
  AND item.i_manufact_id = 128
  AND dt.d_moy = 11
GROUP BY dt.d_year, item.i_brand, item.i_brand_id
ORDER BY dt.d_year, sum_agg DESC, brand_id
LIMIT 100
