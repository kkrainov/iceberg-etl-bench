-- store_sales plus a Zipf-like skewed customer key (B1 J3 skewed join, W1 dedup).
-- Deterministic: the key is a pure function of the row's primary key
-- (ss_ticket_number, ss_item_sk) through xxhash64, so every run produces the same
-- table. u is the top 53 bits of the hash scaled to [0, 1); inverse-CDF sampling of
-- density ∝ x^-s on [1, N+1) then floors to a rank in 1..N.
CREATE TABLE ${target}
WITH (${with})
AS
SELECT
    s.*,
    CAST(least(c.n, floor(power(
        1e0 - (bitwise_right_shift(from_big_endian_64(xxhash64(
                   to_big_endian_64(${seed}) ||
                   to_big_endian_64(s.ss_ticket_number) ||
                   to_big_endian_64(s.ss_item_sk))), 11) / 9007199254740992e0)
              * (1e0 - power(c.n + 1e0, 1e0 - ${zipf_s})),
        1e0 / (1e0 - ${zipf_s})))) AS integer) AS skew_customer_sk
FROM ${src}.store_sales s
CROSS JOIN (SELECT count(*) AS n FROM ${src}.customer) c
