-- TPC-H Q15. TPC-H spec v3.0.1 section 2.4.15, validation substitution parameters.
-- Source: apache/datafusion@36f393559620:benchmarks/queries/q15.sql (Apache-2.0); tables prefixed with ${tpch}.
-- The spec's CREATE VIEW revenue0 / SELECT / DROP VIEW is written as one WITH query
-- (the same view text), as trinodb/trino's benchmark q15 does: one read-only statement per file.
with revenue0 (supplier_no, total_revenue) as (
	select
		l_suppkey,
		sum(l_extendedprice * (1 - l_discount))
	from
		${tpch}.lineitem
	where
		l_shipdate >= date '1996-01-01'
		and l_shipdate < date '1996-01-01' + interval '3' month
	group by
		l_suppkey
)
select
	s_suppkey,
	s_name,
	s_address,
	s_phone,
	total_revenue
from
	${tpch}.supplier,
	revenue0
where
	s_suppkey = supplier_no
	and total_revenue = (
		select
			max(total_revenue)
		from
			revenue0
	)
order by
	s_suppkey
