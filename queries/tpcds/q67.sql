-- TPC-DS query 67. Template query67.tpl of the
-- TPC-DS spec v3.2.0 with its qualification substitution parameters. Trino dialect.
-- Source: trinodb/trino@aab079cf8741:testing/trino-benchmark-queries/src/main/resources/sql/trino/tpcds/q67.sql
-- (Apache-2.0); its table prefix (database.schema placeholders) replaced by ${tpcds}.
SELECT *
FROM
  (
   SELECT
     "i_category"
   , "i_class"
   , "i_brand"
   , "i_product_name"
   , "d_year"
   , "d_qoy"
   , "d_moy"
   , "s_store_id"
   , "sumsales"
   , "rank"() OVER (PARTITION BY "i_category" ORDER BY "sumsales" DESC) "rk"
   FROM
     (
      SELECT
        "i_category"
      , "i_class"
      , "i_brand"
      , "i_product_name"
      , "d_year"
      , "d_qoy"
      , "d_moy"
      , "s_store_id"
      , "sum"(COALESCE(("ss_sales_price" * "ss_quantity"), 0)) "sumsales"
      FROM
        ${tpcds}.store_sales
      , ${tpcds}.date_dim
      , ${tpcds}.store
      , ${tpcds}.item
      WHERE ("ss_sold_date_sk" = "d_date_sk")
         AND ("ss_item_sk" = "i_item_sk")
         AND ("ss_store_sk" = "s_store_sk")
         AND ("d_month_seq" BETWEEN 1200 AND (1200 + 11))
      GROUP BY ROLLUP (i_category, i_class, i_brand, i_product_name, d_year, d_qoy, d_moy, s_store_id)
   )  dw1
)  dw2
WHERE ("rk" <= 100)
ORDER BY "i_category" ASC, "i_class" ASC, "i_brand" ASC, "i_product_name" ASC, "d_year" ASC, "d_qoy" ASC, "d_moy" ASC, "s_store_id" ASC, "sumsales" ASC, "rk" ASC
LIMIT 100
