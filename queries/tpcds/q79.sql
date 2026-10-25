-- TPC-DS query 79. Template query79.tpl of the
-- TPC-DS spec v3.2.0 with its qualification substitution parameters. Trino dialect.
-- Source: trinodb/trino@aab079cf8741:testing/trino-benchmark-queries/src/main/resources/sql/trino/tpcds/q79.sql
-- (Apache-2.0); its table prefix (database.schema placeholders) replaced by ${tpcds}.
SELECT
  "c_last_name"
, "c_first_name"
, "substr"("s_city", 1, 30)
, "ss_ticket_number"
, "amt"
, "profit"
FROM
  (
   SELECT
     "ss_ticket_number"
   , "ss_customer_sk"
   , "store"."s_city"
   , "sum"("ss_coupon_amt") "amt"
   , "sum"("ss_net_profit") "profit"
   FROM
     ${tpcds}.store_sales
   , ${tpcds}.date_dim
   , ${tpcds}.store
   , ${tpcds}.household_demographics
   WHERE ("store_sales"."ss_sold_date_sk" = "date_dim"."d_date_sk")
      AND ("store_sales"."ss_store_sk" = "store"."s_store_sk")
      AND ("store_sales"."ss_hdemo_sk" = "household_demographics"."hd_demo_sk")
      AND (("household_demographics"."hd_dep_count" = 6)
         OR ("household_demographics"."hd_vehicle_count" > 2))
      AND ("date_dim"."d_dow" = 1)
      AND ("date_dim"."d_year" IN (1999   , (1999 + 1)   , (1999 + 2)))
      AND ("store"."s_number_employees" BETWEEN 200 AND 295)
   GROUP BY "ss_ticket_number", "ss_customer_sk", "ss_addr_sk", "store"."s_city"
)  ms
, ${tpcds}.customer
WHERE ("ss_customer_sk" = "c_customer_sk")
ORDER BY "c_last_name" ASC, "c_first_name" ASC, "substr"("s_city", 1, 30) ASC, "profit" ASC
LIMIT 100
