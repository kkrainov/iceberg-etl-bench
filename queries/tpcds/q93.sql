-- TPC-DS query 93. Template query93.tpl of the
-- TPC-DS spec v3.2.0 with its qualification substitution parameters. Trino dialect.
-- Source: trinodb/trino@aab079cf8741:testing/trino-benchmark-queries/src/main/resources/sql/trino/tpcds/q93.sql
-- (Apache-2.0); its table prefix (database.schema placeholders) replaced by ${tpcds}.
SELECT
  "ss_customer_sk"
, "sum"("act_sales") "sumsales"
FROM
  (
   SELECT
     "ss_item_sk"
   , "ss_ticket_number"
   , "ss_customer_sk"
   , (CASE WHEN ("sr_return_quantity" IS NOT NULL) THEN (("ss_quantity" - "sr_return_quantity") * "ss_sales_price") ELSE ("ss_quantity" * "ss_sales_price") END) "act_sales"
   FROM
     (${tpcds}.store_sales
   LEFT JOIN ${tpcds}.store_returns ON ("sr_item_sk" = "ss_item_sk")
      AND ("sr_ticket_number" = "ss_ticket_number"))
   , ${tpcds}.reason
   WHERE ("sr_reason_sk" = "r_reason_sk")
      AND ("r_reason_desc" = 'reason 28')
)  t
GROUP BY "ss_customer_sk"
ORDER BY "sumsales" ASC, "ss_customer_sk" ASC
LIMIT 100
