-- TPC-DS query 52. Template query52.tpl of the
-- TPC-DS spec v3.2.0 with its qualification substitution parameters. Trino dialect.
-- Source: trinodb/trino@aab079cf8741:testing/trino-benchmark-queries/src/main/resources/sql/trino/tpcds/q52.sql
-- (Apache-2.0); its table prefix (database.schema placeholders) replaced by ${tpcds}.
SELECT
  "dt"."d_year"
, "item"."i_brand_id" "brand_id"
, "item"."i_brand" "brand"
, "sum"("ss_ext_sales_price") "ext_price"
FROM
  ${tpcds}.date_dim dt
, ${tpcds}.store_sales
, ${tpcds}.item
WHERE ("dt"."d_date_sk" = "store_sales"."ss_sold_date_sk")
   AND ("store_sales"."ss_item_sk" = "item"."i_item_sk")
   AND ("item"."i_manager_id" = 1)
   AND ("dt"."d_moy" = 11)
   AND ("dt"."d_year" = 2000)
GROUP BY "dt"."d_year", "item"."i_brand", "item"."i_brand_id"
ORDER BY "dt"."d_year" ASC, "ext_price" DESC, "brand_id" ASC
LIMIT 100
