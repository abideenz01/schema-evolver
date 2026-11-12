# QA REPORT — Day 36

Run date 2026-11-12. Environment Python 3.13.18, Pillow 12.3.0, pytest 9.1.1, Linux.

## Checks run

| Check | Command | Result |
|---|---|---|
| Unit and integration tests | `cd ARTIFACT && python -m pytest -q` | 81 passed |
| Coverage | `coverage run --source=src -m pytest` | 98% line coverage (887 statements, 22 missed; `__main__.py` and `SparkExecutor` excluded by nature) |
| Lint | `ruff check --select E,F,W,B --line-length 120 src tests examples` | clean |
| Example determinism | regenerate `examples/` with `make_examples.py`, rerun tests | identical expected `plan.sql`, tests pass |
| CLI demo | `python -m schema_evolver plan ...` on `batch_v2_drifted.jsonl` | verdict PROCEED; 3 AUTO_APPLY, 1 EXPAND_CONTRACT, 1 FILL_NULL, 1 RESCUE; 2 proposals rejected; 1 schema-invalid model item rejected; reader breakage none |
| CLI quarantine | same on `batch_wrong_source.jsonl` | verdict QUARANTINE_BATCH, exit code 2 |
| Dry-run apply and resume | `python -m schema_evolver apply ...`, tests in `test_runner.py` | 6 non-deferred steps executed, rerun executes 0, resume after failure restarts at failed step |
| Generated SQL parse | `sqlglot.parse_one(sql, read="databricks")` on every step | 10 of 10 forward statements and the view rollback parse. `RESTORE TABLE` is not in sqlglot's grammar; its syntax was checked against the Databricks SQL reference (EVIDENCE S7) instead |
| Eval harness | `python -m schema_evolver eval --cases examples/eval_cases.json` | confidence only, 13 accepted, 6 false; gated, 8 accepted, 2 false (synthetic, scripted confidences) |
| Prompt privacy | `test_prompt_contains_metadata_only` | no raw values in prompt |
| POST length and style | character count, colon scan, non-ASCII scan | 2,958 characters including hashtags, 0 colons, 0 non-ASCII characters, 5 hashtags |
| Post to artifact consistency | manual review | every behaviour claimed in POST.md is implemented and tested; numbers (0.93, 0.81, 81 tests, 6 vs 2 false accepts) match artifact output |

## GIF validation

| Property | Result |
|---|---|
| Format | GIF89a, opened with Pillow |
| Dimensions | 1200 x 675 |
| Frames | 96 rendered, 91 stored after Pillow merges identical consecutive frames; 90 of 90 transitions show visible change |
| Loop | infinite (`loop=0`) |
| Duration | about 16.9 s per loop, 110 ms frames with 1.1 s holds on key states and a 3.6 s final hold |
| File size | 184 KB (LinkedIn limit about 5 MB) |
| Metadata | no XMP, no comment block, no content credentials; only NETSCAPE loop extension |
| Reproducibility | two runs of `generate_diagram.py` produce byte-identical files (same MD5) |
| Visual review | first, middle and last frames inspected; labels readable, no text clipping after layout fixes |
| Palette | oxblood background with mint, apricot and periwinkle; not the Day 22 or Day 24 palettes and not black and white |

## Not validated

* Generated SQL was **not executed** against a live Delta table. PySpark with Delta needs jars from Maven Central, which this environment's network policy blocks. `SparkExecutor` is therefore untested. Recommended first run on a scratch Databricks table: `plan`, then `run_plan(plan, SparkExecutor(spark), journal)`, then `DESCRIBE TABLE` and query `orders_v` with every consumer's column list.
* Type widening and column mapping protocol effects on external readers were taken from documentation, not tested.
* No real LLM endpoint was called. The model response is scripted. Proposal quality of any specific model is unmeasured.
* Eval figures come from 16 synthetic cases built by the author of the tool; they show the mechanism, not a real-world false accept rate.
* Originality check used the ledger as instructed; the ledger in Project Knowledge had no prior rows, so duplication against Days 1 to 35 could not be verified from it.

## Known limitations carried into README

Same-domain columns can pass the evidence gate; free-text renames cannot be verified; nested fields are not modelled; JSON cannot prove decimal precision.
