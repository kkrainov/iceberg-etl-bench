-- TPC-DS query 91. Template query91.tpl of the
-- TPC-DS spec v3.2.0 with its qualification substitution parameters. Trino dialect.
-- Source: trinodb/trino@aab079cf8741:testing/trino-benchmark-queries/src/main/resources/sql/trino/tpcds/q91.sql
-- (Apache-2.0); its table prefix (database.schema placeholders) replaced by ${tpcds}.
SELECT
  "cc_call_center_id" "Call_Center"
, "cc_name" "Call_Center_Name"
, "cc_manager" "Manager"
, "sum"("cr_net_loss") "Returns_Loss"
FROM
  ${tpcds}.call_center
, ${tpcds}.catalog_returns
, ${tpcds}.date_dim
, ${tpcds}.customer
, ${tpcds}.customer_address
, ${tpcds}.customer_demographics
, ${tpcds}.household_demographics
WHERE ("cr_call_center_sk" = "cc_call_center_sk")
   AND ("cr_returned_date_sk" = "d_date_sk")
   AND ("cr_returning_customer_sk" = "c_customer_sk")
   AND ("cd_demo_sk" = "c_current_cdemo_sk")
   AND ("hd_demo_sk" = "c_current_hdemo_sk")
   AND ("ca_address_sk" = "c_current_addr_sk")
   AND ("d_year" = 1998)
   AND ("d_moy" = 11)
   AND ((("cd_marital_status" = 'M')
         AND ("cd_education_status" = 'Unknown'))
      OR (("cd_marital_status" = 'W')
         AND ("cd_education_status" = 'Advanced Degree')))
   AND ("hd_buy_potential" LIKE 'Unknown%')
   AND ("ca_gmt_offset" = -7)
GROUP BY "cc_call_center_id", "cc_name", "cc_manager", "cd_marital_status", "cd_education_status"
ORDER BY "sum"("cr_net_loss") DESC
