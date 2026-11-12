"""Regenerate the synthetic example files deterministically.

    python examples/make_examples.py

All data is synthetic. Emails use the reserved example.com domain.
"""
import json
import random
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).parent
rng = random.Random(36)
REGIONS = ["north", "south", "east", "west"]
STATUS = ["placed", "shipped", "delivered", "returned"]
T0 = datetime(2026, 10, 1)


def ts(i):
    return (T0 + timedelta(minutes=37 * i)).strftime("%Y-%m-%dT%H:%M:%S")


def write_jsonl(name, rows):
    (HERE / name).write_text("\n".join(json.dumps(r) for r in rows) + "\n")


contract = {
    "table": "main.sales.orders", "version": 1,
    "columns": [
        {"name": "order_id", "type": "string", "nullable": False},
        {"name": "cust_id", "type": "string", "nullable": False},
        {"name": "order_ts", "type": "timestamp", "nullable": False},
        {"name": "qty", "type": "int"},
        {"name": "amount", "type": "float"},
        {"name": "status", "type": "string"},
        {"name": "region", "type": "string"},
        {"name": "loyalty_points", "type": "int"},
    ],
}
(HERE / "contract_v1.json").write_text(json.dumps(contract, indent=2) + "\n")

v1 = [{"order_id": f"O{i:05d}", "cust_id": f"C{rng.randint(1, 120):04d}", "order_ts": ts(i),
       "qty": rng.randint(1, 20), "amount": round(rng.uniform(5, 500), 2),
       "status": rng.choice(STATUS), "region": rng.choice(REGIONS),
       "loyalty_points": rng.randint(0, 50_000)} for i in range(200)]
write_jsonl("batch_v1_reference.jsonl", v1)

# Upstream release: cust_id renamed, region dropped, two columns added,
# loyalty_points overflows INT, amount arrives as double.
v2 = []
for i in range(200, 400):
    cid = f"C{rng.randint(1, 130):04d}"
    v2.append({"order_id": f"O{i:05d}", "customer_id": cid, "order_ts": ts(i),
               "qty": rng.randint(1, 20), "amount": round(rng.uniform(5, 520), 2),
               "status": rng.choice(STATUS),
               "loyalty_points": rng.choice([rng.randint(0, 50_000), 3_000_000_000 + i]),
               "promo_code": rng.choice([None, "WINTER10", "VIP5", "FREESHIP"]),
               "customer_email": f"{cid.lower()}@example.com"})
write_jsonl("batch_v2_drifted.jsonl", v2)

# A misrouted feed: almost nothing matches the contract.
wrong = [{"sku": f"S{i}", "warehouse": rng.choice(REGIONS), "on_hand": rng.randint(0, 99)}
         for i in range(50)]
write_jsonl("batch_wrong_source.jsonl", wrong)

consumers = {
    "finance_dashboard": ["order_id", "cust_id", "amount", "order_ts"],
    "churn_features": ["cust_id", "qty", "loyalty_points"],
    "ops_report": ["order_id", "status", "region"],
}
(HERE / "consumers.json").write_text(json.dumps(consumers, indent=2) + "\n")

# What a model might return. One correct rename, one plausible-sounding
# hallucination, and one item that violates the response schema.
llm = {"renames": [
    {"old": "cust_id", "new": "customer_id", "confidence": 0.93,
     "rationale": "abbreviation expanded, same type"},
    {"old": "region", "new": "promo_code", "confidence": 0.81,
     "rationale": "region replaced by campaign targeting code"},
    {"old": "order_total", "new": "amount", "confidence": 0.7, "rationale": "synonym"},
]}
(HERE / "llm_response.json").write_text(json.dumps(llm, indent=2) + "\n")


# Labeled rename cases for the evaluation harness.
def ids(prefix, lo, hi, n):
    return [f"{prefix}{rng.randint(lo, hi):05d}" for _ in range(n)]


def nums(lo, hi, n, nd=2):
    return [round(rng.uniform(lo, hi), nd) for _ in range(n)]


cases = []
k = 0


def add(old, otype, ov, new, nv, conf, truth, note):
    global k
    k += 1
    cases.append({"id": f"case_{k:02d}", "note": note, "llm_confidence": conf, "is_rename": truth,
                  "old": {"name": old, "type": otype, "values": ov},
                  "new": {"name": new, "type": otype, "values": nv}})


for conf in (0.95, 0.88, 0.74, 0.66):
    add("cust_id", "string", ids("C", 1, 300, 80), "customer_id", ids("C", 1, 320, 80), conf, True,
        "id rename, overlapping key space")
for conf in (0.9, 0.81):
    add("amt", "double", nums(5, 500, 80), "amount", nums(5, 520, 80), conf, True,
        "measure rename, similar distribution")
add("prod_cd", "string", [rng.choice(["A1", "B2", "C3", "D4"]) for _ in range(60)], "product_code",
    [rng.choice(["A1", "B2", "C3", "D4", "E5"]) for _ in range(60)], 0.86, True, "categorical rename")
add("ship_dt", "date", [f"2026-10-{rng.randint(1, 28):02d}" for _ in range(60)], "shipped_on",
    [f"2026-10-{rng.randint(1, 28):02d}" for _ in range(60)], 0.58, True,
    "true rename the model was unsure about")
add("notes", "string", [f"note {rng.random():.6f}" for _ in range(40)], "comment",
    [f"note {rng.random():.6f}" for _ in range(40)], 0.83, True,
    "free text rename, values never repeat, gate cannot verify")
# Plausible but wrong proposals.
add("region", "string", [rng.choice(REGIONS) for _ in range(60)], "promo_code",
    [rng.choice(["WINTER10", "VIP5"]) for _ in range(60)], 0.81, False, "semantic hallucination")
add("unit_price", "double", nums(1, 40, 80), "line_total", nums(20, 900, 80), 0.88, False,
    "related measure, different scale")
add("cust_id", "string", ids("C", 1, 300, 80), "account_id", ids("A", 1, 300, 80), 0.77, False,
    "different key space")
add("order_dt", "date", [f"2026-09-{rng.randint(1, 28):02d}" for _ in range(60)], "delivery_dt",
    [f"2026-11-{rng.randint(1, 28):02d}" for _ in range(60)], 0.79, False, "different date window")
add("discount_pct", "double", nums(0, 30, 80), "tax_pct", nums(0, 30, 80), 0.72, False,
    "known hard case, same range different meaning")
add("billing_country", "string", [rng.choice(["AE", "PK", "SA"]) for _ in range(60)],
    "shipping_country", [rng.choice(["AE", "PK", "SA"]) for _ in range(60)], 0.84, False,
    "known hard case, same domain different meaning")
add("status", "string", [rng.choice(STATUS) for _ in range(60)], "channel",
    [rng.choice(["web", "app", "store"]) for _ in range(60)], 0.62, False, "low confidence wrong")
(HERE / "eval_cases.json").write_text(json.dumps(cases, indent=1) + "\n")
print(f"wrote examples, {len(cases)} eval cases")
