-- TPC-DS query 63. Template query63.tpl of the
-- TPC-DS spec v3.2.0 with its qualification substitution parameters. Trino dialect.
-- Source: trinodb/trino@aab079cf8741:testing/trino-benchmark-queries/src/main/resources/sql/trino/tpcds/q63.sql
-- (Apache-2.0); its table prefix (database.schema placeholders) replaced by ${tpcds}.
SELECT *
FROM
  (
   SELECT
     "i_manager_id"
   , "sum"("ss_sales_price") "sum_sales"
   , "avg"("sum"("ss_sales_price")) OVER (PARTITION BY "i_manager_id") "avg_monthly_sales"
   FROM
     ${tpcds}.item
   , ${tpcds}.store_sales
   , ${tpcds}.date_dim
   , ${tpcds}.store
   WHERE ("ss_item_sk" = "i_item_sk")
      AND ("ss_sold_date_sk" = "d_date_sk")
      AND ("ss_store_sk" = "s_store_sk")
      AND ("d_month_seq" IN (1200   , (1200 + 1)   , (1200 + 2)   , (1200 + 3)   , (1200 + 4)   , (1200 + 5)   , (1200 + 6)   , (1200 + 7)   , (1200 + 8)   , (1200 + 9)   , (1200 + 10)   , (1200 + 11)))
      AND ((("i_category" IN ('Books'         , 'Children'         , 'Electronics'))
            AND ("i_class" IN ('personal'         , 'portable'         , 'refernece'         , 'self-help'))
            AND ("i_brand" IN ('scholaramalgamalg #14'         , 'scholaramalgamalg #7'         , 'exportiunivamalg #9'         , 'scholaramalgamalg #9')))
         OR (("i_category" IN ('Women'         , 'Music'         , 'Men'))
            AND ("i_class" IN ('accessories'         , 'classical'         , 'fragrances'         , 'pants'))
            AND ("i_brand" IN ('amalgimporto #1'         , 'edu packscholar #1'         , 'exportiimporto #1'         , 'importoamalg #1'))))
   GROUP BY "i_manager_id", "d_moy"
)  tmp1
WHERE ((CASE WHEN ("avg_monthly_sales" > 0) THEN ("abs"(("sum_sales" - "avg_monthly_sales")) / "avg_monthly_sales") ELSE null END) > DECIMAL '0.1')
ORDER BY "i_manager_id" ASC, "avg_monthly_sales" ASC, "sum_sales" ASC
LIMIT 100
