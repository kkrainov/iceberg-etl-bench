-- TPC-DS query 9. Template query9.tpl of the
-- TPC-DS spec v3.2.0 with its qualification substitution parameters. Trino dialect.
-- Source: trinodb/trino@aab079cf8741:testing/trino-benchmark-queries/src/main/resources/sql/trino/tpcds/q09.sql
-- (Apache-2.0); its table prefix (database.schema placeholders) replaced by ${tpcds}.
SELECT
  (CASE WHEN ((
      SELECT "count"(*)
      FROM
        ${tpcds}.store_sales
      WHERE ("ss_quantity" BETWEEN 1 AND 20)
   ) > 74129) THEN (
   SELECT "avg"("ss_ext_discount_amt")
   FROM
     ${tpcds}.store_sales
   WHERE ("ss_quantity" BETWEEN 1 AND 20)
) ELSE (
   SELECT "avg"("ss_net_paid")
   FROM
     ${tpcds}.store_sales
   WHERE ("ss_quantity" BETWEEN 1 AND 20)
) END) "bucket1"
, (CASE WHEN ((
      SELECT "count"(*)
      FROM
        ${tpcds}.store_sales
      WHERE ("ss_quantity" BETWEEN 21 AND 40)
   ) > 122840) THEN (
   SELECT "avg"("ss_ext_discount_amt")
   FROM
     ${tpcds}.store_sales
   WHERE ("ss_quantity" BETWEEN 21 AND 40)
) ELSE (
   SELECT "avg"("ss_net_paid")
   FROM
     ${tpcds}.store_sales
   WHERE ("ss_quantity" BETWEEN 21 AND 40)
) END) "bucket2"
, (CASE WHEN ((
      SELECT "count"(*)
      FROM
        ${tpcds}.store_sales
      WHERE ("ss_quantity" BETWEEN 41 AND 60)
   ) > 56580) THEN (
   SELECT "avg"("ss_ext_discount_amt")
   FROM
     ${tpcds}.store_sales
   WHERE ("ss_quantity" BETWEEN 41 AND 60)
) ELSE (
   SELECT "avg"("ss_net_paid")
   FROM
     ${tpcds}.store_sales
   WHERE ("ss_quantity" BETWEEN 41 AND 60)
) END) "bucket3"
, (CASE WHEN ((
      SELECT "count"(*)
      FROM
        ${tpcds}.store_sales
      WHERE ("ss_quantity" BETWEEN 61 AND 80)
   ) > 10097) THEN (
   SELECT "avg"("ss_ext_discount_amt")
   FROM
     ${tpcds}.store_sales
   WHERE ("ss_quantity" BETWEEN 61 AND 80)
) ELSE (
   SELECT "avg"("ss_net_paid")
   FROM
     ${tpcds}.store_sales
   WHERE ("ss_quantity" BETWEEN 61 AND 80)
) END) "bucket4"
, (CASE WHEN ((
      SELECT "count"(*)
      FROM
        ${tpcds}.store_sales
      WHERE ("ss_quantity" BETWEEN 81 AND 100)
   ) > 165306) THEN (
   SELECT "avg"("ss_ext_discount_amt")
   FROM
     ${tpcds}.store_sales
   WHERE ("ss_quantity" BETWEEN 81 AND 100)
) ELSE (
   SELECT "avg"("ss_net_paid")
   FROM
     ${tpcds}.store_sales
   WHERE ("ss_quantity" BETWEEN 81 AND 100)
) END) "bucket5"
FROM
  ${tpcds}.reason
WHERE ("r_reason_sk" = 1)
