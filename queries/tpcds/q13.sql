-- TPC-DS query 13. Template query13.tpl of the
-- TPC-DS spec v3.2.0 with its qualification substitution parameters. Trino dialect.
-- Source: trinodb/trino@aab079cf8741:testing/trino-benchmark-queries/src/main/resources/sql/trino/tpcds/q13.sql
-- (Apache-2.0); its table prefix (database.schema placeholders) replaced by ${tpcds}.
SELECT
  "avg"("ss_quantity")
, "avg"("ss_ext_sales_price")
, "avg"("ss_ext_wholesale_cost")
, "sum"("ss_ext_wholesale_cost")
FROM
  ${tpcds}.store_sales
, ${tpcds}.store
, ${tpcds}.customer_demographics
, ${tpcds}.household_demographics
, ${tpcds}.customer_address
, ${tpcds}.date_dim
WHERE ("s_store_sk" = "ss_store_sk")
   AND ("ss_sold_date_sk" = "d_date_sk")
   AND ("d_year" = 2001)
   AND ((("ss_hdemo_sk" = "hd_demo_sk")
         AND ("cd_demo_sk" = "ss_cdemo_sk")
         AND ("cd_marital_status" = 'M')
         AND ("cd_education_status" = 'Advanced Degree')
         AND ("ss_sales_price" BETWEEN DECIMAL '100.00' AND DECIMAL '150.00')
         AND ("hd_dep_count" = 3))
      OR (("ss_hdemo_sk" = "hd_demo_sk")
         AND ("cd_demo_sk" = "ss_cdemo_sk")
         AND ("cd_marital_status" = 'S')
         AND ("cd_education_status" = 'College')
         AND ("ss_sales_price" BETWEEN DECIMAL '50.00' AND DECIMAL '100.00')
         AND ("hd_dep_count" = 1))
      OR (("ss_hdemo_sk" = "hd_demo_sk")
         AND ("cd_demo_sk" = "ss_cdemo_sk")
         AND ("cd_marital_status" = 'W')
         AND ("cd_education_status" = '2 yr Degree')
         AND ("ss_sales_price" BETWEEN DECIMAL '150.00' AND DECIMAL '200.00')
         AND ("hd_dep_count" = 1)))
   AND ((("ss_addr_sk" = "ca_address_sk")
         AND ("ca_country" = 'United States')
         AND ("ca_state" IN ('TX'      , 'OH'      , 'TX'))
         AND ("ss_net_profit" BETWEEN 100 AND 200))
      OR (("ss_addr_sk" = "ca_address_sk")
         AND ("ca_country" = 'United States')
         AND ("ca_state" IN ('OR'      , 'NM'      , 'KY'))
         AND ("ss_net_profit" BETWEEN 150 AND 300))
      OR (("ss_addr_sk" = "ca_address_sk")
         AND ("ca_country" = 'United States')
         AND ("ca_state" IN ('VA'      , 'TX'      , 'MS'))
         AND ("ss_net_profit" BETWEEN 50 AND 250)))
