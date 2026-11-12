"""Turn gate decisions into an expand / backfill / switch / contract plan for Delta Lake.

Readers bind to a contract view (<table>_v). Renames never break readers because
the view keeps the old name as an alias until every consumer has migrated.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .models import Action, Column, Contract, Decision, SchemaError
from .policy import GateReport

_TABLE_RE = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*){0,2}$")
_SQL_TYPE = {"byte": "TINYINT", "short": "SMALLINT", "int": "INT", "long": "BIGINT",
             "float": "FLOAT", "double": "DOUBLE", "string": "STRING", "boolean": "BOOLEAN",
             "date": "DATE", "timestamp": "TIMESTAMP", "timestamp_ntz": "TIMESTAMP_NTZ"}
RESCUE_COL = "_rescued_data"


def sql_type(t: str) -> str:
    return _SQL_TYPE.get(t, t.upper())


def q(name: str) -> str:
    return f"`{name}`"


@dataclass
class Step:
    id: str
    phase: str  # preflight | expand | backfill | switch | contract
    description: str
    sql: str
    rollback_sql: str | None
    deferred: bool = False  # never executed by an automatic run


@dataclass
class Plan:
    plan_id: str
    table: str
    view: str
    from_version: int
    to_version: int
    verdict: str
    steps: list[Step]
    writer_mapping: dict[str, Any]
    next_contract: dict[str, Any]
    view_columns: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    def to_sql(self) -> str:
        lines = [f"-- plan {self.plan_id}  {self.table}  v{self.from_version} -> v{self.to_version}",
                 f"-- verdict {self.verdict}"]
        for s in self.steps:
            tag = " (DEFERRED, run only after consumers migrate)" if s.deferred else ""
            lines += ["", f"-- [{s.phase}] {s.id} {s.description}{tag}", s.sql + ";"]
        return "\n".join(lines) + "\n"


def _plan_id(contract: Contract, decisions: list[Decision]) -> str:
    payload = json.dumps({"t": contract.table, "v": contract.version,
                          "d": [d.to_dict() for d in decisions]}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def build_plan(contract: Contract, report: GateReport, table: str | None = None) -> Plan:
    table = table or contract.table
    if not _TABLE_RE.match(table):
        raise SchemaError(f"invalid table identifier '{table}'")
    view = f"{table}_v"
    decisions = report.decisions
    pid = _plan_id(contract, decisions)
    steps: list[Step] = []
    restore = f"RESTORE TABLE {table} TO VERSION AS OF {{pre_version}}"

    if report.verdict != "PROCEED":
        # Blocked batches still get a reviewable plan, but nothing executable.
        return Plan(pid, table, view, contract.version, contract.version, report.verdict, [],
                    {"dual_write": {}, "rescue": []}, contract.to_dict(), [])

    renames = [d for d in decisions if d.action == Action.EXPAND_CONTRACT]
    adds = [d for d in decisions if d.action == Action.AUTO_APPLY and d.change_kind == "NEW_COLUMN"]
    widens = [d for d in decisions if d.action == Action.AUTO_APPLY and d.change_kind == "TYPE_WIDEN"]
    rescues = [d for d in decisions if d.action == Action.RESCUE]
    cols = contract.by_name()
    need_rescue_col = bool(rescues) and RESCUE_COL not in cols

    if widens:
        steps.append(Step("enable_type_widening", "preflight", "enable Delta type widening",
                          f"ALTER TABLE {table} SET TBLPROPERTIES ('delta.enableTypeWidening' = 'true')",
                          None))
    add_cols = [(d.column, d.new_type) for d in adds]
    add_cols += [(d.new_column, d.new_type) for d in renames]
    if need_rescue_col:
        add_cols.append((RESCUE_COL, "string"))
    if add_cols:
        body = ", ".join(f"{q(n)} {sql_type(t)} COMMENT 'schema_evolver {pid}'" for n, t in add_cols)
        steps.append(Step("add_columns", "expand", f"add {len(add_cols)} nullable column(s)",
                          f"ALTER TABLE {table} ADD COLUMNS ({body})", restore))
    for d in widens:
        steps.append(Step(f"widen_{d.column}", "expand", f"widen {d.column} {d.old_type} to {d.new_type}",
                          f"ALTER TABLE {table} ALTER COLUMN {q(d.column)} TYPE {sql_type(d.new_type)}",
                          restore))
    for d in renames:
        steps.append(Step(f"backfill_{d.new_column}", "backfill",
                          f"copy history {d.column} into {d.new_column}",
                          f"UPDATE {table} SET {q(d.new_column)} = CAST({q(d.column)} AS {sql_type(d.new_type)}) "
                          f"WHERE {q(d.new_column)} IS NULL AND {q(d.column)} IS NOT NULL",
                          None))

    # Contract view: every old name still resolves, new names are available.
    rename_old = {d.column: d for d in renames}
    final_types = {c.name: c.type for c in contract.columns}
    for d in widens:
        final_types[d.column] = d.new_type
    select, view_cols = [], []
    for c in contract.columns:
        if c.name in rename_old:
            d = rename_old[c.name]
            expr = f"COALESCE({q(d.new_column)}, CAST({q(c.name)} AS {sql_type(d.new_type)}))"
            select.append(f"{expr} AS {q(d.new_column)}")
            select.append(f"{expr} AS {q(c.name)}")
            view_cols += [d.new_column, c.name]
        else:
            select.append(q(c.name))
            view_cols.append(c.name)
    for d in adds:
        select.append(q(d.column))
        view_cols.append(d.column)
    prev_view = f"SELECT {', '.join(q(c.name) for c in contract.columns)} FROM {table}"
    steps.append(Step("switch_view", "switch", "repoint contract view (old names kept as aliases)",
                      f"CREATE OR REPLACE VIEW {view} AS SELECT {', '.join(select)} FROM {table}",
                      f"CREATE OR REPLACE VIEW {view} AS {prev_view}"))

    if renames:
        steps.append(Step("enable_column_mapping", "contract",
                          "column mapping is required for DROP COLUMNS; may affect streaming reads and CDF",
                          f"ALTER TABLE {table} SET TBLPROPERTIES ('delta.columnMapping.mode' = 'name')",
                          None, deferred=True))
        for d in renames:
            if not cols[d.column].nullable:
                steps.append(Step(f"relax_not_null_{d.column}", "contract",
                                  f"drop NOT NULL on {d.column} before writers stop dual writing",
                                  f"ALTER TABLE {table} ALTER COLUMN {q(d.column)} DROP NOT NULL",
                                  None, deferred=True))
        olds = ", ".join(q(d.column) for d in renames)
        steps.append(Step("drop_old_columns", "contract", "drop renamed source columns",
                          f"ALTER TABLE {table} DROP COLUMNS ({olds})", None, deferred=True))
        final_select = [q(n) if n not in rename_old else q(rename_old[n].new_column)
                        for n in [c.name for c in contract.columns]] + [q(d.column) for d in adds]
        steps.append(Step("drop_view_aliases", "contract", "remove old-name aliases from view",
                          f"CREATE OR REPLACE VIEW {view} AS SELECT {', '.join(final_select)} FROM {table}",
                          None, deferred=True))

    # Next contract version.
    new_cols: list[Column] = []
    for c in contract.columns:
        if c.name in rename_old:
            d = rename_old[c.name]
            new_cols.append(Column(d.new_column, d.new_type, c.nullable, c.pii))
        else:
            new_cols.append(Column(c.name, final_types[c.name], c.nullable, c.pii))
    new_cols += [Column(d.column, d.new_type, True) for d in adds]
    aliases = dict(contract.aliases)
    aliases.update({d.column: d.new_column for d in renames})
    nxt = Contract(contract.table, contract.version + 1, new_cols, aliases)

    mapping = {"dual_write": {d.new_column: d.column for d in renames},
               "rescue": [d.column for d in rescues]}
    return Plan(pid, table, view, contract.version, nxt.version, report.verdict, steps,
                mapping, nxt.to_dict(), view_cols)


def apply_writer_mapping(record: dict[str, Any], mapping: dict[str, Any]) -> dict[str, Any]:
    """Shape one incoming record for the expanded table.

    New-name values are written to the new column and mirrored to the old one;
    rescued columns are packed
    into _rescued_data as JSON so nothing is silently dropped.
    """
    out = dict(record)
    # Dual write during the migrate window: old readers, NOT NULL constraints and
    # any job still selecting the old physical column keep working.
    for new, old in mapping.get("dual_write", {}).items():
        if new in out and old not in out:
            out[old] = out[new]
    rescued = {k: out.pop(k) for k in mapping.get("rescue", []) if k in out}
    if rescued:
        prev = out.get(RESCUE_COL)
        merged = json.loads(prev) if isinstance(prev, str) and prev else {}
        merged.update(rescued)
        out[RESCUE_COL] = json.dumps(merged, sort_keys=True, default=str)
    return out


def reader_compatibility(view_columns: list[str], consumers: dict[str, list[str]]) -> dict[str, list[str]]:
    """Return consumers whose required columns the view no longer exposes."""
    exposed = set(view_columns)
    broken = {name: sorted(set(req) - exposed) for name, req in consumers.items()}
    return {k: v for k, v in broken.items() if v}


def contract_phase_blockers(aliases: dict[str, str], consumers: dict[str, list[str]]) -> dict[str, list[str]]:
    """Consumers still reading an old (aliased) name. Contract phase must wait."""
    old = set(aliases)
    out = {name: sorted(set(req) & old) for name, req in consumers.items()}
    return {k: v for k, v in out.items() if v}
