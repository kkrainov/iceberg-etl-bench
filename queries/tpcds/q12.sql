-- TPC-DS query 12. Template query12.tpl of the
-- TPC-DS spec v3.2.0 with its qualification substitution parameters. Trino dialect.
-- Source: trinodb/trino@aab079cf8741:testing/trino-benchmark-queries/src/main/resources/sql/trino/tpcds/q12.sql
-- (Apache-2.0); its table prefix (database.schema placeholders) replaced by ${tpcds}.
-- Fixed: the source prefixes one column alias with its database.schema placeholders (a slip
-- upstream); the alias is the plain name, as in the spec.
SELECT
  "i_item_id"
, "i_item_desc"
, "i_category"
, "i_class"
, "i_current_price"
, "sum"("ws_ext_sales_price") "itemrevenue"
, (("sum"("ws_ext_sales_price") * 100) / "sum"("sum"("ws_ext_sales_price")) OVER (PARTITION BY "i_class")) "revenueratio"
FROM
  ${tpcds}.web_sales
, ${tpcds}.item
, ${tpcds}.date_dim
WHERE ("ws_item_sk" = "i_item_sk")
   AND ("i_category" IN ('Sports', 'Books', 'Home'))
   AND ("ws_sold_date_sk" = "d_date_sk")
   AND (CAST("d_date" AS DATE) BETWEEN CAST('1999-02-22' AS DATE) AND (CAST('1999-02-22' AS DATE) + INTERVAL  '30' DAY))
GROUP BY "i_item_id", "i_item_desc", "i_category", "i_class", "i_current_price"
ORDER BY "i_category" ASC, "i_class" ASC, "i_item_id" ASC, "i_item_desc" ASC, "revenueratio" ASC
LIMIT 100
