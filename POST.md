An upstream team renames cust_id to customer_id. mergeSchema adds the new column, cust_id goes null for every new row, and nothing fails. The dashboards just get quietly wrong.

The tempting fix is to let an LLM read the drift and adapt the pipeline. It sees cust_id disappear and customer_id appear and says rename, confidence 0.93. It also sees region disappear and promo_code appear and says rename, confidence 0.81. One of those would corrupt a table.

The real design question is where its authority ends.

For Day 36 the split is simple. The model proposes, data decides, and a view keeps readers alive.

Drift detection stays deterministic. New nullable columns and supported widenings like int to long or float to double apply automatically, since Delta Lake type widening changes the type without rewriting data files. Integer to double is held back, because Delta will not apply that one through automatic schema evolution either. A nullable column that stops arriving is kept and filled with nulls, never dropped. Anything that looks like PII lands in a rescued column, not the table.

Renames are where the model helps and where it gets fenced. It only sees metadata, names, types, null rates and distinct counts, never values. Each proposal then has to pass an evidence check against a reference profile of the old column, using hashed value overlap, format shape, numeric range and null rate. Confidence is logged but never counts as evidence. region to promo_code had zero value overlap, so it was rejected and region stayed.

An accepted rename still does not touch readers. The plan adds the new column, dual writes both names, backfills history with an idempotent update, and repoints a contract view that serves both cust_id and customer_id. Dropping the old column is deferred until every consumer has moved, because that is the one step you cannot undo, and the column mapping it needs can affect streaming reads and change data feed.

The companion artifact is schema_evolver, a small Python tool with no runtime dependencies. It profiles a batch, classifies drift, takes proposals from a heuristic or any LLM callable, runs the gate, and writes Delta SQL for expand, backfill, switch and contract with rollback statements and a resumable journal. 81 tests cover malformed model output, retries, ambiguous mappings, wrong-source batches and reader compatibility.

I also ran an honest check on 16 synthetic labeled cases with scripted model confidences. Accepting on confidence alone let 6 wrong renames through. The evidence gate let 2 through, billing_country vs shipping_country and discount vs tax, columns that share a value domain. Data cannot tell those apart either, which is exactly why accepted renames go through a reversible path instead of a direct ALTER.

