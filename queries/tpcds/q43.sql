-- TPC-DS query 43. Template query43.tpl of the
-- TPC-DS spec v3.2.0 with its qualification substitution parameters. Trino dialect.
-- Source: trinodb/trino@aab079cf8741:testing/trino-benchmark-queries/src/main/resources/sql/trino/tpcds/q43.sql
-- (Apache-2.0); its table prefix (database.schema placeholders) replaced by ${tpcds}.
SELECT
  "s_store_name"
, "s_store_id"
, "sum"((CASE WHEN ("d_day_name" = 'Sunday') THEN "ss_sales_price" ELSE null END)) "sun_sales"
, "sum"((CASE WHEN ("d_day_name" = 'Monday') THEN "ss_sales_price" ELSE null END)) "mon_sales"
, "sum"((CASE WHEN ("d_day_name" = 'Tuesday') THEN "ss_sales_price" ELSE null END)) "tue_sales"
, "sum"((CASE WHEN ("d_day_name" = 'Wednesday') THEN "ss_sales_price" ELSE null END)) "wed_sales"
, "sum"((CASE WHEN ("d_day_name" = 'Thursday') THEN "ss_sales_price" ELSE null END)) "thu_sales"
, "sum"((CASE WHEN ("d_day_name" = 'Friday') THEN "ss_sales_price" ELSE null END)) "fri_sales"
, "sum"((CASE WHEN ("d_day_name" = 'Saturday') THEN "ss_sales_price" ELSE null END)) "sat_sales"
FROM
  ${tpcds}.date_dim
, ${tpcds}.store_sales
, ${tpcds}.store
WHERE ("d_date_sk" = "ss_sold_date_sk")
   AND ("s_store_sk" = "ss_store_sk")
   AND ("s_gmt_offset" = -5)
   AND ("d_year" = 2000)
GROUP BY "s_store_name", "s_store_id"
ORDER BY "s_store_name" ASC, "s_store_id" ASC, "sun_sales" ASC, "mon_sales" ASC, "tue_sales" ASC, "wed_sales" ASC, "thu_sales" ASC, "fri_sales" ASC, "sat_sales" ASC
LIMIT 100
