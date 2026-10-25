-- TPC-DS query 89. Template query89.tpl of the
-- TPC-DS spec v3.2.0 with its qualification substitution parameters. Trino dialect.
-- Source: trinodb/trino@aab079cf8741:testing/trino-benchmark-queries/src/main/resources/sql/trino/tpcds/q89.sql
-- (Apache-2.0); its table prefix (database.schema placeholders) replaced by ${tpcds}.
SELECT *
FROM
  (
   SELECT
     "i_category"
   , "i_class"
   , "i_brand"
   , "s_store_name"
   , "s_company_name"
   , "d_moy"
   , "sum"("ss_sales_price") "sum_sales"
   , "avg"("sum"("ss_sales_price")) OVER (PARTITION BY "i_category", "i_brand", "s_store_name", "s_company_name") "avg_monthly_sales"
   FROM
     ${tpcds}.item
   , ${tpcds}.store_sales
   , ${tpcds}.date_dim
   , ${tpcds}.store
   WHERE ("ss_item_sk" = "i_item_sk")
      AND ("ss_sold_date_sk" = "d_date_sk")
      AND ("ss_store_sk" = "s_store_sk")
      AND ("d_year" IN (1999))
      AND ((("i_category" IN ('Books'         , 'Electronics'         , 'Sports'))
            AND ("i_class" IN ('computers'         , 'stereo'         , 'football')))
         OR (("i_category" IN ('Men'         , 'Jewelry'         , 'Women'))
            AND ("i_class" IN ('shirts'         , 'birdal'         , 'dresses'))))
   GROUP BY "i_category", "i_class", "i_brand", "s_store_name", "s_company_name", "d_moy"
)  tmp1
WHERE ((CASE WHEN ("avg_monthly_sales" <> 0) THEN ("abs"(("sum_sales" - "avg_monthly_sales")) / "avg_monthly_sales") ELSE null END) > DECIMAL '0.1')
ORDER BY ("sum_sales" - "avg_monthly_sales") ASC, "s_store_name" ASC
LIMIT 100
