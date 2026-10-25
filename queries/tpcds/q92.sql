-- TPC-DS query 92. Template query92.tpl of the
-- TPC-DS spec v3.2.0 with its qualification substitution parameters. Trino dialect.
-- Source: trinodb/trino@aab079cf8741:testing/trino-benchmark-queries/src/main/resources/sql/trino/tpcds/q92.sql
-- (Apache-2.0); its table prefix (database.schema placeholders) replaced by ${tpcds}.
SELECT "sum"("ws_ext_discount_amt") "Excess Discount Amount"
FROM
  ${tpcds}.web_sales
, ${tpcds}.item
, ${tpcds}.date_dim
WHERE ("i_manufact_id" = 350)
   AND ("i_item_sk" = "ws_item_sk")
   AND ("d_date" BETWEEN CAST('2000-01-27' AS DATE) AND (CAST('2000-01-27' AS DATE) + INTERVAL  '90' DAY))
   AND ("d_date_sk" = "ws_sold_date_sk")
   AND ("ws_ext_discount_amt" > (
      SELECT (DECIMAL '1.3' * "avg"("ws_ext_discount_amt"))
      FROM
        ${tpcds}.web_sales
      , ${tpcds}.date_dim
      WHERE ("ws_item_sk" = "i_item_sk")
         AND ("d_date" BETWEEN CAST('2000-01-27' AS DATE) AND (CAST('2000-01-27' AS DATE) + INTERVAL  '90' DAY))
         AND ("d_date_sk" = "ws_sold_date_sk")
   ))
ORDER BY "sum"("ws_ext_discount_amt") ASC
LIMIT 100
