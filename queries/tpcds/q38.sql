-- TPC-DS query 38. Template query38.tpl of the
-- TPC-DS spec v3.2.0 with its qualification substitution parameters. Trino dialect.
-- Source: trinodb/trino@aab079cf8741:testing/trino-benchmark-queries/src/main/resources/sql/trino/tpcds/q38.sql
-- (Apache-2.0); its table prefix (database.schema placeholders) replaced by ${tpcds}.
SELECT "count"(*)
FROM
  (
   SELECT DISTINCT
     "c_last_name"
   , "c_first_name"
   , "d_date"
   FROM
     ${tpcds}.store_sales
   , ${tpcds}.date_dim
   , ${tpcds}.customer
   WHERE ("store_sales"."ss_sold_date_sk" = "date_dim"."d_date_sk")
      AND ("store_sales"."ss_customer_sk" = "customer"."c_customer_sk")
      AND ("d_month_seq" BETWEEN 1200 AND (1200 + 11))
INTERSECT    SELECT DISTINCT
     "c_last_name"
   , "c_first_name"
   , "d_date"
   FROM
     ${tpcds}.catalog_sales
   , ${tpcds}.date_dim
   , ${tpcds}.customer
   WHERE ("catalog_sales"."cs_sold_date_sk" = "date_dim"."d_date_sk")
      AND ("catalog_sales"."cs_bill_customer_sk" = "customer"."c_customer_sk")
      AND ("d_month_seq" BETWEEN 1200 AND (1200 + 11))
INTERSECT    SELECT DISTINCT
     "c_last_name"
   , "c_first_name"
   , "d_date"
   FROM
     ${tpcds}.web_sales
   , ${tpcds}.date_dim
   , ${tpcds}.customer
   WHERE ("web_sales"."ws_sold_date_sk" = "date_dim"."d_date_sk")
      AND ("web_sales"."ws_bill_customer_sk" = "customer"."c_customer_sk")
      AND ("d_month_seq" BETWEEN 1200 AND (1200 + 11))
)  hot_cust
LIMIT 100
