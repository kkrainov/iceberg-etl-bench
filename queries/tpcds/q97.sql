-- TPC-DS query 97. Template query97.tpl of the
-- TPC-DS spec v3.2.0 with its qualification substitution parameters. Trino dialect.
-- Source: trinodb/trino@aab079cf8741:testing/trino-benchmark-queries/src/main/resources/sql/trino/tpcds/q97.sql
-- (Apache-2.0); its table prefix (database.schema placeholders) replaced by ${tpcds}.
WITH
  ssci AS (
   SELECT
     "ss_customer_sk" "customer_sk"
   , "ss_item_sk" "item_sk"
   FROM
     ${tpcds}.store_sales
   , ${tpcds}.date_dim
   WHERE ("ss_sold_date_sk" = "d_date_sk")
      AND ("d_month_seq" BETWEEN 1200 AND (1200 + 11))
   GROUP BY "ss_customer_sk", "ss_item_sk"
)
, csci AS (
   SELECT
     "cs_bill_customer_sk" "customer_sk"
   , "cs_item_sk" "item_sk"
   FROM
     ${tpcds}.catalog_sales
   , ${tpcds}.date_dim
   WHERE ("cs_sold_date_sk" = "d_date_sk")
      AND ("d_month_seq" BETWEEN 1200 AND (1200 + 11))
   GROUP BY "cs_bill_customer_sk", "cs_item_sk"
)
SELECT
  "sum"((CASE WHEN ("ssci"."customer_sk" IS NOT NULL)
   AND ("csci"."customer_sk" IS NULL) THEN 1 ELSE 0 END)) "store_only"
, "sum"((CASE WHEN ("ssci"."customer_sk" IS NULL)
   AND ("csci"."customer_sk" IS NOT NULL) THEN 1 ELSE 0 END)) "catalog_only"
, "sum"((CASE WHEN ("ssci"."customer_sk" IS NOT NULL)
   AND ("csci"."customer_sk" IS NOT NULL) THEN 1 ELSE 0 END)) "store_and_catalog"
FROM
  (ssci
FULL JOIN csci ON ("ssci"."customer_sk" = "csci"."customer_sk")
   AND ("ssci"."item_sk" = "csci"."item_sk"))
LIMIT 100
