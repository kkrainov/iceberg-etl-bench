-- TPC-DS query 15. Template query15.tpl of the
-- TPC-DS spec v3.2.0 with its qualification substitution parameters. Trino dialect.
-- Source: trinodb/trino@aab079cf8741:testing/trino-benchmark-queries/src/main/resources/sql/trino/tpcds/q15.sql
-- (Apache-2.0); its table prefix (database.schema placeholders) replaced by ${tpcds}.
SELECT
  "ca_zip"
, "sum"("cs_sales_price")
FROM
  ${tpcds}.catalog_sales
, ${tpcds}.customer
, ${tpcds}.customer_address
, ${tpcds}.date_dim
WHERE ("cs_bill_customer_sk" = "c_customer_sk")
   AND ("c_current_addr_sk" = "ca_address_sk")
   AND (("substr"("ca_zip", 1, 5) IN ('85669'   , '86197'   , '88274'   , '83405'   , '86475'   , '85392'   , '85460'   , '80348'   , '81792'))
      OR ("ca_state" IN ('CA'   , 'WA'   , 'GA'))
      OR ("cs_sales_price" > 500))
   AND ("cs_sold_date_sk" = "d_date_sk")
   AND ("d_qoy" = 2)
   AND ("d_year" = 2001)
GROUP BY "ca_zip"
ORDER BY "ca_zip" ASC
LIMIT 100
