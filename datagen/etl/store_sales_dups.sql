-- Duplicated rows for ROW_NUMBER() dedup (B1 W1) over the skewed key.
-- A hash-sampled ${dup_sample_pct}% of store_sales_skewed rows, each emitted 1..${dup_max_copies}
-- times with version = 1..copies and an updated_at that grows with version.
-- Unique key: (ss_ticket_number, ss_item_sk, version). Deterministic (xxhash64 of
-- the primary key with a different salt than store_sales_skewed).
CREATE TABLE ${target}
WITH (${with})
AS
SELECT
    k.*,
    v.version,
    date_add('second', x.h % 31536000 + v.version * 3600,
             TIMESTAMP '2003-01-01 00:00:00.000000') AS updated_at
FROM ${target_ns}.store_sales_skewed k
CROSS JOIN LATERAL (
    SELECT bitwise_right_shift(from_big_endian_64(xxhash64(
        to_big_endian_64(${seed} + 1) ||
        to_big_endian_64(k.ss_ticket_number) ||
        to_big_endian_64(k.ss_item_sk))), 11) AS h
) x
CROSS JOIN UNNEST(sequence(1, 1 + (x.h / 100) % ${dup_max_copies})) AS v(version)
WHERE x.h % 100 < ${dup_sample_pct}
