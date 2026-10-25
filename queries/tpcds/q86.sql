-- TPC-DS query 86. Template query86.tpl of the
-- TPC-DS spec v3.2.0 with its qualification substitution parameters. Trino dialect.
-- Source: trinodb/trino@aab079cf8741:testing/trino-benchmark-queries/src/main/resources/sql/trino/tpcds/q86.sql
-- (Apache-2.0); its table prefix (database.schema placeholders) replaced by ${tpcds}.
SELECT
  "sum"("ws_net_paid") "total_sum"
, "i_category"
, "i_class"
, (GROUPING ("i_category") + GROUPING ("i_class")) "lochierarchy"
, "rank"() OVER (PARTITION BY (GROUPING ("i_category") + GROUPING ("i_class")), (CASE WHEN (GROUPING ("i_class") = 0) THEN "i_category" END) ORDER BY "sum"("ws_net_paid") DESC) "rank_within_parent"
FROM
  ${tpcds}.web_sales
, ${tpcds}.date_dim d1
, ${tpcds}.item
WHERE ("d1"."d_month_seq" BETWEEN 1200 AND (1200 + 11))
   AND ("d1"."d_date_sk" = "ws_sold_date_sk")
   AND ("i_item_sk" = "ws_item_sk")
GROUP BY ROLLUP (i_category, i_class)
ORDER BY "lochierarchy" DESC, (CASE WHEN ("lochierarchy" = 0) THEN "i_category" END) ASC, "rank_within_parent" ASC
LIMIT 100
