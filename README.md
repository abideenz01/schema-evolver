# schema_evolver

Evidence-gated schema evolution for Delta Lake tables. An LLM (or a deterministic heuristic) may *propose* what an upstream schema change means. A deterministic policy gate decides what is allowed to happen, and every accepted change is executed as an expand, backfill, switch, contract plan so existing readers never see a broken schema.

Reference implementation for Day 36 of the AI Data Engineering series. Production-oriented, not production-proven.

## Problem

Upstream teams rename, drop, add, and retype columns without warning. Common responses are both bad:

* `mergeSchema` or Auto Loader `addNewColumns` adds `customer_id` as a new column, `cust_id` silently goes null for new rows, and every dashboard joining on `cust_id` degrades quietly.
* Failing the pipeline on any drift turns a two-minute additive change into an on-call page.

Letting an LLM rewrite the table is worse. A model will happily map `region` to `promo_code` with 0.81 confidence. The value of this tool is the boundary between those two worlds.

## What it does

```
batch JSONL ──► profile (hashed values, shapes, numeric stats)
             └► drift detection vs contract (deterministic)
                   └► rename proposals (heuristic + optional LLM, metadata only)
                         └► policy gate (data evidence, guardrails)
                               └► plan: preflight → expand → backfill → switch view → [contract, deferred]
                                     └► idempotent runner with journal, retries, rollback
```

| Drift | Gate action | What happens |
|---|---|---|
| New nullable column | `AUTO_APPLY` | `ADD COLUMNS`, exposed in contract view |
| New column matching a PII pattern | `RESCUE` | packed into `_rescued_data`, not promoted |
| Supported widening (int→long, float→double, decimal precision, date→timestamp_ntz) | `AUTO_APPLY` | `ALTER COLUMN TYPE` after enabling type widening |
| Integer to double/decimal | `BLOCK` by default | semantic change; Delta also requires this to be manual |
| Missing nullable column | `FILL_NULL` | column kept, never physically dropped |
| Missing required column, verified rename | `EXPAND_CONTRACT` | add new column, dual write, backfill, view keeps old name as alias |
| Missing required column, no verified rename | `BLOCK` | batch quarantined |
| Incompatible type, nulls in required | `BLOCK` | batch quarantined |
| More than 50% of contract missing | `BLOCK` | suspected wrong source |
| Automatic changes over budget (10) | `BLOCK` | bounded autonomy |

A rename is accepted only if the new column's data looks like the old column's reference data: value-fingerprint containment, format-shape match, or numeric range and mean agreement, plus a stable null rate. Model confidence is recorded but never sufficient. Ambiguous mappings are rejected, not guessed.

## Quick start

```bash
cd ARTIFACT
pip install -r requirements.txt          # pytest only; runtime is stdlib
export PYTHONPATH=src

python -m schema_evolver plan \
  --contract examples/contract_v1.json \
  --reference examples/batch_v1_reference.jsonl \
  --batch examples/batch_v2_drifted.jsonl \
  --consumers examples/consumers.json \
  --llm-response examples/llm_response.json \
  --out out/

python -m schema_evolver apply --plan out/plan.json --journal out/journal.json   # dry run
python -m schema_evolver eval  --cases examples/eval_cases.json
python -m pytest -q
```

`plan` writes `decision_report.json`, `plan.json`, `plan.sql`, and `contract_v2.json`. Exit codes: `0` proceed, `2` quarantine, `3` a consumer would lose a column, `1` bad input.

Expected output for the demo is committed in `examples/expected_output/` and checked by the test suite.

## Demo result (synthetic data)

Upstream release renames `cust_id`, drops `region`, adds `promo_code` and `customer_email`, overflows `loyalty_points`, sends `amount` as double. The scripted model response contains one correct rename, one hallucinated rename (`region` → `promo_code`), and one schema-invalid item.

* `cust_id` → `customer_id` accepted (value containment and shape match)
* `region` → `promo_code` rejected (containment 0.0) so `region` is kept and filled with nulls
* heuristic proposal `cust_id` → `customer_email` rejected
* `customer_email` rescued as PII, `promo_code` added, two widenings applied
* Every existing consumer still resolves its columns through `orders_v`; `finance_dashboard` and `churn_features` block the contract phase until they move to `customer_id`

## Using a real model

`LLMProposer` takes any `complete_fn(prompt: str) -> str`. Wrap your SDK of choice:

```python
from schema_evolver import LLMProposer

def complete_fn(prompt: str) -> str:
    return my_client.generate(prompt, temperature=0)   # your endpoint, your credentials from env

proposals = LLMProposer(complete_fn, max_attempts=2).propose(contract, changes, batch_profile)
```

The prompt contains column names, types, null rates, distinct estimates and numeric summaries. It never contains raw values (tested). Unparseable output, transport errors, or schema-invalid items fail closed: the deterministic path continues with zero model proposals.

## Wiring into a Databricks pipeline

1. Store `contract_vN.json` and a reference profile next to the table (or in a Unity Catalog volume).
2. Before each merge, profile the micro-batch (sampled) and call `plan`.
3. `PROCEED`: run `run_plan(plan, SparkExecutor(spark), journal_path)`, then write the batch with `apply_writer_mapping` (dual write and rescue). In PySpark the mapping is `df.withColumn(old, F.col(new))` per rename plus `F.to_json(F.struct(*rescued))` into `_rescued_data`.
4. `QUARANTINE_BATCH`: write the batch to a quarantine table and alert with `decision_report.json`.
5. Point all readers at `<table>_v`, never at the base table. That view is the zero-downtime lever.
6. Run contract steps (`--include-deferred`) only when `contract_phase_blocked_by` is empty.

## Rollback

* Switch step: restore the previous view definition. Instant and lossless.
* Expand steps: `RESTORE TABLE ... TO VERSION AS OF <pre_version>` captured in the journal. This discards writes made after that version, so pause writers first.
* Contract steps are not reversible in place (column drop). They are deferred for that reason.
* Enabling type widening or column mapping upgrades table protocol. Check every external reader supports the feature before enabling.

## Impact model

The tool ships no savings number. Estimate yours:

```
hours_saved_per_month = Σ_class  events_per_month[class] × auto_fraction[class] × manual_minutes[class] / 60
risk_per_month        = renames_per_month × false_accept_rate × cost_of_a_wrong_rename
```

`estimate_impact()` in `evaluate.py` computes the first. Measure `false_accept_rate` by labeling your own past drift events in the `eval_cases.json` format and running `eval`.

On the 16 bundled **synthetic** cases (model confidences are scripted, not from a real model): confidence-only acceptance at 0.7 accepted 13 renames with 6 wrong; gated acceptance accepted 8 with 2 wrong. The 2 remaining false accepts are columns that share a value domain (`billing_country` vs `shipping_country`, `discount_pct` vs `tax_pct`). Data evidence cannot separate those. This is why accepted renames still go through expand and contract, where a wrong decision is reversible.

## Limits and non-goals

* Profiles come from JSON values. Typed sources (Parquet, Avro) should pass their declared types; JSON cannot prove decimal precision, so decimal targets are treated as drift.
* Top-level columns only. Nested struct evolution is not modelled.
* Free-text columns cannot be verified by value evidence and will be blocked when renamed.
* Same-domain columns can pass the gate (see above). Keep a human approval step for renames on critical tables if that risk is unacceptable.
* SQL targets Delta Lake 4.0 semantics and Databricks SQL. `runtime="delta_3_2"` restricts widenings to byte/short.
* `SparkExecutor` is provided but not exercised by tests here (no Spark runtime in the test environment).
* Column mapping can break streaming reads and change data feed consumers. The plan defers it to the contract phase for that reason.

## Production adoption checklist

- [ ] Readers query the contract view, not the base table
- [ ] Reference profile refreshed on each contract version
- [ ] PII patterns extended for your domain
- [ ] Quarantine table and alert route in place
- [ ] Journal path on durable storage, one journal per plan
- [ ] Type widening and column mapping protocol impact reviewed for all external readers
- [ ] Labeled drift history evaluated with `eval` before enabling auto-apply on renames

## Layout

```
src/schema_evolver/   models, profiler, drift, proposers, policy, planner, runner, evaluate, cli
tests/                81 tests
examples/             synthetic data, scripted model response, eval cases, expected output
```
