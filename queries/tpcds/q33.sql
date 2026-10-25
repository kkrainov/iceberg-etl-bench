-- TPC-DS query 33. Template query33.tpl of the
-- TPC-DS spec v3.2.0 with its qualification substitution parameters. Trino dialect.
-- Source: trinodb/trino@aab079cf8741:testing/trino-benchmark-queries/src/main/resources/sql/trino/tpcds/q33.sql
-- (Apache-2.0); its table prefix (database.schema placeholders) replaced by ${tpcds}.
WITH
  ss AS (
   SELECT
     "i_manufact_id"
   , "sum"("ss_ext_sales_price") "total_sales"
   FROM
     ${tpcds}.store_sales
   , ${tpcds}.date_dim
   , ${tpcds}.customer_address
   , ${tpcds}.item
   WHERE ("i_manufact_id" IN (
      SELECT "i_manufact_id"
      FROM
        ${tpcds}.item
      WHERE ("i_category" IN ('Electronics'))
   ))
      AND ("ss_item_sk" = "i_item_sk")
      AND ("ss_sold_date_sk" = "d_date_sk")
      AND ("d_year" = 1998)
      AND ("d_moy" = 5)
      AND ("ss_addr_sk" = "ca_address_sk")
      AND ("ca_gmt_offset" = -5)
   GROUP BY "i_manufact_id"
)
, cs AS (
   SELECT
     "i_manufact_id"
   , "sum"("cs_ext_sales_price") "total_sales"
   FROM
     ${tpcds}.catalog_sales
   , ${tpcds}.date_dim
   , ${tpcds}.customer_address
   , ${tpcds}.item
   WHERE ("i_manufact_id" IN (
      SELECT "i_manufact_id"
      FROM
        ${tpcds}.item
      WHERE ("i_category" IN ('Electronics'))
   ))
      AND ("cs_item_sk" = "i_item_sk")
      AND ("cs_sold_date_sk" = "d_date_sk")
      AND ("d_year" = 1998)
      AND ("d_moy" = 5)
      AND ("cs_bill_addr_sk" = "ca_address_sk")
      AND ("ca_gmt_offset" = -5)
   GROUP BY "i_manufact_id"
)
, ws AS (
   SELECT
     "i_manufact_id"
   , "sum"("ws_ext_sales_price") "total_sales"
   FROM
     ${tpcds}.web_sales
   , ${tpcds}.date_dim
   , ${tpcds}.customer_address
   , ${tpcds}.item
   WHERE ("i_manufact_id" IN (
      SELECT "i_manufact_id"
      FROM
        ${tpcds}.item
      WHERE ("i_category" IN ('Electronics'))
   ))
      AND ("ws_item_sk" = "i_item_sk")
      AND ("ws_sold_date_sk" = "d_date_sk")
      AND ("d_year" = 1998)
      AND ("d_moy" = 5)
      AND ("ws_bill_addr_sk" = "ca_address_sk")
      AND ("ca_gmt_offset" = -5)
   GROUP BY "i_manufact_id"
)
SELECT
  "i_manufact_id"
, "sum"("total_sales") "total_sales"
FROM
  (
   SELECT *
   FROM
     ss
UNION ALL    SELECT *
   FROM
     cs
UNION ALL    SELECT *
   FROM
     ws
)  tmp1
GROUP BY "i_manufact_id"
ORDER BY "total_sales" ASC
LIMIT 100
