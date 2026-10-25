-- TPC-DS query 39 (first of its two queries). Template query39.tpl of the
-- TPC-DS spec v3.2.0 with its qualification substitution parameters. Trino dialect.
-- Source: trinodb/trino@aab079cf8741:testing/trino-benchmark-queries/src/main/resources/sql/trino/tpcds/q39a.sql
-- (Apache-2.0); its table prefix (database.schema placeholders) replaced by ${tpcds}.
-- The source casts both cov columns to DECIMAL(30, 10) "to avoid unstable results due to roundings"
-- (a deviation from the spec; its comment moved here from the SELECT list).
WITH
  inv AS (
   SELECT
     "w_warehouse_name"
   , "w_warehouse_sk"
   , "i_item_sk"
   , "d_moy"
   , "stdev"
   , "mean"
   , (CASE "mean" WHEN 0 THEN null ELSE ("stdev" / "mean") END) "cov"
   FROM
     (
      SELECT
        "w_warehouse_name"
      , "w_warehouse_sk"
      , "i_item_sk"
      , "d_moy"
      , "stddev_samp"("inv_quantity_on_hand") "stdev"
      , "avg"("inv_quantity_on_hand") "mean"
      FROM
        ${tpcds}.inventory
      , ${tpcds}.item
      , ${tpcds}.warehouse
      , ${tpcds}.date_dim
      WHERE ("inv_item_sk" = "i_item_sk")
         AND ("inv_warehouse_sk" = "w_warehouse_sk")
         AND ("inv_date_sk" = "d_date_sk")
         AND ("d_year" = 2001)
      GROUP BY "w_warehouse_name", "w_warehouse_sk", "i_item_sk", "d_moy"
   )  foo
   WHERE ((CASE "mean" WHEN 0 THEN 0 ELSE ("stdev" / "mean") END) > 1)
)
SELECT
  "inv1"."w_warehouse_sk"
, "inv1"."i_item_sk"
, "inv1"."d_moy"
, "inv1"."mean"
, CAST("inv1"."cov" AS DECIMAL(30, 10))
, "inv2"."w_warehouse_sk"
, "inv2"."i_item_sk"
, "inv2"."d_moy"
, "inv2"."mean"
, CAST("inv2"."cov" AS DECIMAL(30, 10))
FROM
  inv inv1
, inv inv2
WHERE ("inv1"."i_item_sk" = "inv2"."i_item_sk")
   AND ("inv1"."w_warehouse_sk" = "inv2"."w_warehouse_sk")
   AND ("inv1"."d_moy" = 1)
   AND ("inv2"."d_moy" = (1 + 1))
ORDER BY "inv1"."w_warehouse_sk" ASC, "inv1"."i_item_sk" ASC, "inv1"."d_moy" ASC, "inv1"."mean" ASC, "inv1"."cov" ASC, "inv2"."d_moy" ASC, "inv2"."mean" ASC, "inv2"."cov" ASC
