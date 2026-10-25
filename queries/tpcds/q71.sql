-- TPC-DS query 71. Template query71.tpl of the
-- TPC-DS spec v3.2.0 with its qualification substitution parameters. Trino dialect.
-- Source: trinodb/trino@aab079cf8741:testing/trino-benchmark-queries/src/main/resources/sql/trino/tpcds/q71.sql
-- (Apache-2.0); its table prefix (database.schema placeholders) replaced by ${tpcds}.
-- The source adds ORDER BY columns for stable results (a deviation from the spec it marks).
SELECT
  "i_brand_id" "brand_id"
, "i_brand" "brand"
, "t_hour"
, "t_minute"
, "sum"("ext_price") "ext_price"
FROM
  ${tpcds}.item
, (
   SELECT
     "ws_ext_sales_price" "ext_price"
   , "ws_sold_date_sk" "sold_date_sk"
   , "ws_item_sk" "sold_item_sk"
   , "ws_sold_time_sk" "time_sk"
   FROM
     ${tpcds}.web_sales
   , ${tpcds}.date_dim
   WHERE ("d_date_sk" = "ws_sold_date_sk")
      AND ("d_moy" = 11)
      AND ("d_year" = 1999)
UNION ALL    SELECT
     "cs_ext_sales_price" "ext_price"
   , "cs_sold_date_sk" "sold_date_sk"
   , "cs_item_sk" "sold_item_sk"
   , "cs_sold_time_sk" "time_sk"
   FROM
     ${tpcds}.catalog_sales
   , ${tpcds}.date_dim
   WHERE ("d_date_sk" = "cs_sold_date_sk")
      AND ("d_moy" = 11)
      AND ("d_year" = 1999)
UNION ALL    SELECT
     "ss_ext_sales_price" "ext_price"
   , "ss_sold_date_sk" "sold_date_sk"
   , "ss_item_sk" "sold_item_sk"
   , "ss_sold_time_sk" "time_sk"
   FROM
     ${tpcds}.store_sales
   , ${tpcds}.date_dim
   WHERE ("d_date_sk" = "ss_sold_date_sk")
      AND ("d_moy" = 11)
      AND ("d_year" = 1999)
)  tmp
, ${tpcds}.time_dim
WHERE ("sold_item_sk" = "i_item_sk")
   AND ("i_manager_id" = 1)
   AND ("time_sk" = "t_time_sk")
   AND (("t_meal_time" = 'breakfast')
      OR ("t_meal_time" = 'dinner'))
GROUP BY "i_brand", "i_brand_id", "t_hour", "t_minute"
ORDER BY "ext_price" DESC, "i_brand_id" ASC,
   -- additional columns to assure results stability for larger scale factors; this is a deviation from TPC-DS specification
   "t_hour" ASC, "t_minute" ASC
