-- TPC-DS query 73. Template query73.tpl of the
-- TPC-DS spec v3.2.0 with its qualification substitution parameters. Trino dialect.
-- Source: trinodb/trino@aab079cf8741:testing/trino-benchmark-queries/src/main/resources/sql/trino/tpcds/q73.sql
-- (Apache-2.0); its table prefix (database.schema placeholders) replaced by ${tpcds}.
-- The source adds ORDER BY columns for stable results (a deviation from the spec it marks).
SELECT
  "c_last_name"
, "c_first_name"
, "c_salutation"
, "c_preferred_cust_flag"
, "ss_ticket_number"
, "cnt"
FROM
  (
   SELECT
     "ss_ticket_number"
   , "ss_customer_sk"
   , "count"(*) "cnt"
   FROM
     ${tpcds}.store_sales
   , ${tpcds}.date_dim
   , ${tpcds}.store
   , ${tpcds}.household_demographics
   WHERE ("store_sales"."ss_sold_date_sk" = "date_dim"."d_date_sk")
      AND ("store_sales"."ss_store_sk" = "store"."s_store_sk")
      AND ("store_sales"."ss_hdemo_sk" = "household_demographics"."hd_demo_sk")
      AND ("date_dim"."d_dom" BETWEEN 1 AND 2)
      AND (("household_demographics"."hd_buy_potential" = '>10000')
         OR ("household_demographics"."hd_buy_potential" = 'Unknown'))
      AND ("household_demographics"."hd_vehicle_count" > 0)
      AND ((CASE WHEN ("household_demographics"."hd_vehicle_count" > 0) THEN (CAST("household_demographics"."hd_dep_count" AS DECIMAL(7,2)) / "household_demographics"."hd_vehicle_count") ELSE null END) > 1)
      AND ("date_dim"."d_year" IN (1999   , (1999 + 1)   , (1999 + 2)))
      AND ("store"."s_county" IN ('Williamson County'   , 'Franklin Parish'   , 'Bronx County'   , 'Orange County'))
   GROUP BY "ss_ticket_number", "ss_customer_sk"
)  dj
, ${tpcds}.customer
WHERE ("ss_customer_sk" = "c_customer_sk")
   AND ("cnt" BETWEEN 1 AND 5)
ORDER BY "cnt" DESC, "c_last_name" ASC,
   -- additional column to assure results stability for larger scale factors; this is a deviation from TPC-DS specification
   "ss_ticket_number" ASC
