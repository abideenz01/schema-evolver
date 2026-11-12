# EVIDENCE — Day 36

Access date for all sources: 2026-11-12.

## Sources

| # | Title | Publisher | URL |
|---|---|---|---|
| S1 | Delta type widening | Delta Lake (Linux Foundation) | https://docs.delta.io/delta-type-widening/ |
| S2 | Type widening | Databricks | https://docs.databricks.com/aws/en/tables/features/type-widening |
| S3 | Rename and drop columns with Delta Lake column mapping | Databricks | https://docs.databricks.com/aws/en/delta/column-mapping |
| S4 | Delta column mapping | Delta Lake | https://docs.delta.io/delta-column-mapping |
| S5 | Configure schema inference and evolution in Auto Loader | Databricks | https://docs.databricks.com/gcp/en/ingestion/cloud-object-storage/auto-loader/schema |
| S6 | Parallel Change | Martin Fowler | https://www.martinfowler.com/bliki/ParallelChange.html |
| S7 | RESTORE | Databricks SQL language manual | https://docs.databricks.com/aws/sql/language-manual/delta-restore |

## Claim to source map

| Claim (post or artifact) | Source | Type |
|---|---|---|
| Type widening changes a column to a wider type without rewriting data files | S1, S2 | Documented fact |
| Enabled with `delta.enableTypeWidening = true`; sets a reader/writer protocol table feature that may limit external clients | S1, S2 | Documented fact |
| Supported widenings in Delta 3.2 are byte to short/int and short to int; Delta 4.0 adds int to long/decimal/double, long to decimal, float to double, decimal precision increases, date to timestampNTZ (encoded in `models.widening_kind`) | S1 | Documented fact |
| Integer to decimal or double must be applied manually; automatic schema evolution does not apply them (post: "Delta will not apply that one through automatic schema evolution") | S1 | Documented fact |
| Decimal rule `decimal(p,s)` to `decimal(p+k1, s+k2)` only if `k1 >= k2 >= 0`; minimum decimal(10,0) for byte/short/int and decimal(20,0) for long | S1 | Documented fact |
| `ALTER TABLE ... ALTER COLUMN ... TYPE` syntax | S1 | Documented fact |
| `RENAME COLUMN` and `DROP COLUMNS` require column mapping (`delta.columnMapping.mode = 'name'`) | S3, S4 | Documented fact |
| Enabling column mapping might break streaming reads and change data feed consumers (post: "can affect streaming reads and change data feed") | S3 | Documented fact |
| Auto Loader `addNewColumns` adds new columns; `rescue` records unexpected columns in a rescued data column; `failOnNewColumns` fails the stream | S5 | Documented fact |
| `RESTORE [TABLE] name [TO] VERSION AS OF version` syntax used for expand-phase rollback; version comes from DESCRIBE HISTORY | S7 | Documented fact |
| Expand / migrate / contract (parallel change) is a pattern for backward-incompatible interface changes | S6 | Documented fact |
| mergeSchema adds `customer_id` and `cust_id` goes null for new rows (post opening) | S5 plus Delta schema evolution behaviour | Illustrative scenario, not a reported incident |
| LLM proposals with confidences 0.93 and 0.81 | `examples/llm_response.json` | Scripted, not output of a real model |
| 16 cases, confidence-only 6 false accepts, gated 2 false accepts | `python -m schema_evolver eval` on `examples/eval_cases.json` | Measured on synthetic data with scripted confidences |
| 81 tests | `pytest` run, see QA_REPORT.md | Measured |
| Model sees metadata only, never values | `tests/test_proposers.py::test_prompt_contains_metadata_only` | Tested property of the artifact |

## Engineering recommendations (not vendor claims)

* Bind readers to a contract view and treat the base table as an implementation detail.
* Never accept a rename on model confidence alone; require data evidence and keep the change reversible.
* Defer destructive contract steps until consumers have migrated.
* Hold PII-named columns in a rescued column until classified.

## Version assumptions

* Generated SQL targets Delta Lake 4.0 semantics and Databricks SQL syntax. `RESTORE TABLE`, `CREATE OR REPLACE VIEW`, and `UPDATE` are standard Delta/Databricks SQL but were not executed against a live Delta table in this run (see QA_REPORT.md).
* `runtime="delta_3_2"` restricts widening to the Delta 3.2 preview table.
