-- TPC-DS query 22. Template query22.tpl of the
-- TPC-DS spec v3.2.0 with its qualification substitution parameters. Trino dialect.
-- Source: trinodb/trino@aab079cf8741:testing/trino-benchmark-queries/src/main/resources/sql/trino/tpcds/q22.sql
-- (Apache-2.0); its table prefix (database.schema placeholders) replaced by ${tpcds}.
SELECT
  "i_product_name"
, "i_brand"
, "i_class"
, "i_category"
, "avg"("inv_quantity_on_hand") "qoh"
FROM
  ${tpcds}.inventory
, ${tpcds}.date_dim
, ${tpcds}.item
WHERE ("inv_date_sk" = "d_date_sk")
   AND ("inv_item_sk" = "i_item_sk")
   AND ("d_month_seq" BETWEEN 1200 AND (1200 + 11))
GROUP BY ROLLUP (i_product_name, i_brand, i_class, i_category)
ORDER BY "qoh" ASC, "i_product_name" ASC, "i_brand" ASC, "i_class" ASC, "i_category" ASC
LIMIT 100
