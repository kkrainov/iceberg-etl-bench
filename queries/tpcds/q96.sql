-- TPC-DS query 96. Template query96.tpl of the
-- TPC-DS spec v3.2.0 with its qualification substitution parameters. Trino dialect.
-- Source: trinodb/trino@aab079cf8741:testing/trino-benchmark-queries/src/main/resources/sql/trino/tpcds/q96.sql
-- (Apache-2.0); its table prefix (database.schema placeholders) replaced by ${tpcds}.
SELECT "count"(*)
FROM
  ${tpcds}.store_sales
, ${tpcds}.household_demographics
, ${tpcds}.time_dim
, ${tpcds}.store
WHERE ("ss_sold_time_sk" = "time_dim"."t_time_sk")
   AND ("ss_hdemo_sk" = "household_demographics"."hd_demo_sk")
   AND ("ss_store_sk" = "s_store_sk")
   AND ("time_dim"."t_hour" = 20)
   AND ("time_dim"."t_minute" >= 30)
   AND ("household_demographics"."hd_dep_count" = 7)
   AND ("store"."s_store_name" = 'ese')
ORDER BY "count"(*) ASC
LIMIT 100
