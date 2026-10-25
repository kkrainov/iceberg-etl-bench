-- DataFusion override: Trino's concat() is NULL when an argument is NULL; DataFusion's concat()
-- skips NULL arguments (PostgreSQL semantics). DataFusion's || operator has Trino's concat()
-- semantics, so concat(concat(a, ', '), b) is written ((a || ', ') || b). Nothing else differs.
-- TPC-DS query 84. Template query84.tpl of the
-- TPC-DS spec v3.2.0 with its qualification substitution parameters. Trino dialect.
-- Source: trinodb/trino@aab079cf8741:testing/trino-benchmark-queries/src/main/resources/sql/trino/tpcds/q84.sql
-- (Apache-2.0); its table prefix (database.schema placeholders) replaced by ${tpcds}.
-- Fixed: the source prefixes one column alias with its database.schema placeholders (a slip
-- upstream); the alias is the plain name, as in the spec.
SELECT
  "c_customer_id" "customer_id"
, (("c_last_name" || ', ') || "c_first_name") "customername"
FROM
  ${tpcds}.customer
, ${tpcds}.customer_address
, ${tpcds}.customer_demographics
, ${tpcds}.household_demographics
, ${tpcds}.income_band
, ${tpcds}.store_returns
WHERE ("ca_city" = 'Edgewood')
   AND ("c_current_addr_sk" = "ca_address_sk")
   AND ("ib_lower_bound" >= 38128)
   AND ("ib_upper_bound" <= (38128 + 50000))
   AND ("ib_income_band_sk" = "hd_income_band_sk")
   AND ("cd_demo_sk" = "c_current_cdemo_sk")
   AND ("hd_demo_sk" = "c_current_hdemo_sk")
   AND ("sr_cdemo_sk" = "cd_demo_sk")
ORDER BY "c_customer_id" ASC
LIMIT 100
