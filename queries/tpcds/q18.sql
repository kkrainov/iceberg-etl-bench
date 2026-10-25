-- TPC-DS query 18. Template query18.tpl of the
-- TPC-DS spec v3.2.0 with its qualification substitution parameters. Trino dialect.
-- Source: trinodb/trino@aab079cf8741:testing/trino-benchmark-queries/src/main/resources/sql/trino/tpcds/q18.sql
-- (Apache-2.0); its table prefix (database.schema placeholders) replaced by ${tpcds}.
SELECT
  "i_item_id"
, "ca_country"
, "ca_state"
, "ca_county"
, "avg"(CAST("cs_quantity" AS DECIMAL(12,2))) "agg1"
, "avg"(CAST("cs_list_price" AS DECIMAL(12,2))) "agg2"
, "avg"(CAST("cs_coupon_amt" AS DECIMAL(12,2))) "agg3"
, "avg"(CAST("cs_sales_price" AS DECIMAL(12,2))) "agg4"
, "avg"(CAST("cs_net_profit" AS DECIMAL(12,2))) "agg5"
, "avg"(CAST("c_birth_year" AS DECIMAL(12,2))) "agg6"
, "avg"(CAST("cd1"."cd_dep_count" AS DECIMAL(12,2))) "agg7"
FROM
  ${tpcds}.catalog_sales
, ${tpcds}.customer_demographics cd1
, ${tpcds}.customer_demographics cd2
, ${tpcds}.customer
, ${tpcds}.customer_address
, ${tpcds}.date_dim
, ${tpcds}.item
WHERE ("cs_sold_date_sk" = "d_date_sk")
   AND ("cs_item_sk" = "i_item_sk")
   AND ("cs_bill_cdemo_sk" = "cd1"."cd_demo_sk")
   AND ("cs_bill_customer_sk" = "c_customer_sk")
   AND ("cd1"."cd_gender" = 'F')
   AND ("cd1"."cd_education_status" = 'Unknown')
   AND ("c_current_cdemo_sk" = "cd2"."cd_demo_sk")
   AND ("c_current_addr_sk" = "ca_address_sk")
   AND ("c_birth_month" IN (1, 6, 8, 9, 12, 2))
   AND ("d_year" = 1998)
   AND ("ca_state" IN ('MS', 'IN', 'ND', 'OK', 'NM', 'VA', 'MS'))
GROUP BY ROLLUP (i_item_id, ca_country, ca_state, ca_county)
ORDER BY "ca_country" ASC, "ca_state" ASC, "ca_county" ASC, "i_item_id" ASC
LIMIT 100
