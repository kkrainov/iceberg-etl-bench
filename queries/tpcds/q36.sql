-- TPC-DS query 36. Template query36.tpl of the
-- TPC-DS spec v3.2.0 with its qualification substitution parameters. Trino dialect.
-- Source: trinodb/trino@aab079cf8741:testing/trino-benchmark-queries/src/main/resources/sql/trino/tpcds/q36.sql
-- (Apache-2.0); its table prefix (database.schema placeholders) replaced by ${tpcds}.
SELECT
  ("sum"("ss_net_profit") / "sum"("ss_ext_sales_price")) "gross_margin"
, "i_category"
, "i_class"
, (GROUPING ("i_category") + GROUPING ("i_class")) "lochierarchy"
, "rank"() OVER (PARTITION BY (GROUPING ("i_category") + GROUPING ("i_class")), (CASE WHEN (GROUPING ("i_class") = 0) THEN "i_category" END) ORDER BY ("sum"("ss_net_profit") / "sum"("ss_ext_sales_price")) ASC) "rank_within_parent"
FROM
  ${tpcds}.store_sales
, ${tpcds}.date_dim d1
, ${tpcds}.item
, ${tpcds}.store
WHERE ("d1"."d_year" = 2001)
   AND ("d1"."d_date_sk" = "ss_sold_date_sk")
   AND ("i_item_sk" = "ss_item_sk")
   AND ("s_store_sk" = "ss_store_sk")
   AND ("s_state" IN (
     'TN'
   , 'TN'
   , 'TN'
   , 'TN'
   , 'TN'
   , 'TN'
   , 'TN'
   , 'TN'))
GROUP BY ROLLUP (i_category, i_class)
ORDER BY "lochierarchy" DESC, (CASE WHEN ("lochierarchy" = 0) THEN "i_category" END) ASC, "rank_within_parent" ASC, "i_category", "i_class"
LIMIT 100
