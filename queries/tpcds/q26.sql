-- TPC-DS query 26. Template query26.tpl of the
-- TPC-DS spec v3.2.0 with its qualification substitution parameters. Trino dialect.
-- Source: trinodb/trino@aab079cf8741:testing/trino-benchmark-queries/src/main/resources/sql/trino/tpcds/q26.sql
-- (Apache-2.0); its table prefix (database.schema placeholders) replaced by ${tpcds}.
SELECT
  "i_item_id"
, "avg"("cs_quantity") "agg1"
, "avg"("cs_list_price") "agg2"
, "avg"("cs_coupon_amt") "agg3"
, "avg"("cs_sales_price") "agg4"
FROM
  ${tpcds}.catalog_sales
, ${tpcds}.customer_demographics
, ${tpcds}.date_dim
, ${tpcds}.item
, ${tpcds}.promotion
WHERE ("cs_sold_date_sk" = "d_date_sk")
   AND ("cs_item_sk" = "i_item_sk")
   AND ("cs_bill_cdemo_sk" = "cd_demo_sk")
   AND ("cs_promo_sk" = "p_promo_sk")
   AND ("cd_gender" = 'M')
   AND ("cd_marital_status" = 'S')
   AND ("cd_education_status" = 'College')
   AND (("p_channel_email" = 'N')
      OR ("p_channel_event" = 'N'))
   AND ("d_year" = 2000)
GROUP BY "i_item_id"
ORDER BY "i_item_id" ASC
LIMIT 100
