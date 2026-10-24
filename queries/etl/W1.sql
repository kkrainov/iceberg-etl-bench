-- B1 W1: ROW_NUMBER() dedup over the skewed key: keep each customer's latest row.
-- Partitioning by skew_customer_sk puts ~14% of all rows into one hot partition
-- (the top key), which is what W1 stresses (methodology section 4, "buffering one
-- hot partition"). The ORDER BY is total (tie-breakers on the row's unique key), so
-- the kept rows, and therefore the result, are deterministic. Aggregated to one row
-- so result transfer doesn't dominate. Written for this benchmark.
SELECT
    count(*) AS customers_kept,
    sum(version) AS sum_version,
    sum(ss_net_paid) AS sum_net_paid
FROM (
    SELECT
        version,
        ss_net_paid,
        ROW_NUMBER() OVER (
            PARTITION BY skew_customer_sk
            ORDER BY updated_at DESC, version DESC, ss_ticket_number, ss_item_sk) AS rn
    FROM ${etl}.store_sales_dups
) t
WHERE rn = 1
