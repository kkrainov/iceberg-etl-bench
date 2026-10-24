-- Deliberate memory failure for the smoke plan (not a B1 query). Every row of
-- store_sales_dups is repeated 200 times into array_agg state held per item:
-- about 576M bigints (4.6 GB raw) at SF1, well above the 4 GiB per-node limit
-- (Trino: EXCEEDED_LOCAL_MEMORY_LIMIT after ~8 s). Same limit for DataFusion's pool.
SELECT count(*) AS groups, sum(cardinality(tickets)) AS elements
FROM (
    SELECT ss_item_sk, array_agg(ss_ticket_number) AS tickets
    FROM ${etl}.store_sales_dups
    CROSS JOIN UNNEST(sequence(1, 200)) AS r(n)
    GROUP BY ss_item_sk
) t
