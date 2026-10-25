-- TPC-DS query 94. Template query94.tpl of the
-- TPC-DS spec v3.2.0 with its qualification substitution parameters. Trino dialect.
-- Source: trinodb/trino@aab079cf8741:testing/trino-benchmark-queries/src/main/resources/sql/trino/tpcds/q94.sql
-- (Apache-2.0); its table prefix (database.schema placeholders) replaced by ${tpcds}.
SELECT
  "count"(DISTINCT "ws_order_number") "order count"
, "sum"("ws_ext_ship_cost") "total shipping cost"
, "sum"("ws_net_profit") "total net profit"
FROM
  ${tpcds}.web_sales ws1
, ${tpcds}.date_dim
, ${tpcds}.customer_address
, ${tpcds}.web_site
WHERE ("d_date" BETWEEN CAST('1999-2-01' AS DATE) AND (CAST('1999-2-01' AS DATE) + INTERVAL  '60' DAY))
   AND ("ws1"."ws_ship_date_sk" = "d_date_sk")
   AND ("ws1"."ws_ship_addr_sk" = "ca_address_sk")
   AND ("ca_state" = 'IL')
   AND ("ws1"."ws_web_site_sk" = "web_site_sk")
   AND ("web_company_name" = 'pri')
   AND (EXISTS (
   SELECT *
   FROM
     ${tpcds}.web_sales ws2
   WHERE ("ws1"."ws_order_number" = "ws2"."ws_order_number")
      AND ("ws1"."ws_warehouse_sk" <> "ws2"."ws_warehouse_sk")
))
   AND (NOT (EXISTS (
   SELECT *
   FROM
     ${tpcds}.web_returns wr1
   WHERE ("ws1"."ws_order_number" = "wr1"."wr_order_number")
)))
ORDER BY "count"(DISTINCT "ws_order_number") ASC
LIMIT 100
