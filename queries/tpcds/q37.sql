-- TPC-DS query 37. Template query37.tpl of the
-- TPC-DS spec v3.2.0 with its qualification substitution parameters. Trino dialect.
-- Source: trinodb/trino@aab079cf8741:testing/trino-benchmark-queries/src/main/resources/sql/trino/tpcds/q37.sql
-- (Apache-2.0); its table prefix (database.schema placeholders) replaced by ${tpcds}.
SELECT
  "i_item_id"
, "i_item_desc"
, "i_current_price"
FROM
  ${tpcds}.item
, ${tpcds}.inventory
, ${tpcds}.date_dim
, ${tpcds}.catalog_sales
WHERE ("i_current_price" BETWEEN 68 AND (68 + 30))
   AND ("inv_item_sk" = "i_item_sk")
   AND ("d_date_sk" = "inv_date_sk")
   AND (CAST("d_date" AS DATE) BETWEEN CAST('2000-02-01' AS DATE) AND (CAST('2000-02-01' AS DATE) + INTERVAL  '60' DAY))
   AND ("i_manufact_id" IN (677, 940, 694, 808))
   AND ("inv_quantity_on_hand" BETWEEN 100 AND 500)
   AND ("cs_item_sk" = "i_item_sk")
GROUP BY "i_item_id", "i_item_desc", "i_current_price"
ORDER BY "i_item_id" ASC
LIMIT 100
