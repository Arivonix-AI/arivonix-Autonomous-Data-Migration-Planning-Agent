"""Rule-based risk detection over the schema, dependency graph, mappings
and data-quality profile. Every rule is deterministic and explainable;
the optional LLM stage (llm.py) may add further risks on top, but nothing
here depends on it.
"""
from __future__ import annotations

import re

from .models import (
    DataQualityReport,
    MatchKind,
    Risk,
    RiskSeverity,
    SchemaSnapshot,
    TableMapping,
)

# Column-name substrings that suggest personally identifiable / sensitive
# data. Deliberately conservative (favors false positives) since the cost
# of flagging a false positive is a human glancing at a report row.
_PII_PATTERNS = [
    "email", "ssn", "social_security", "password", "passwd", "secret",
    "dob", "date_of_birth", "birth_date", "phone", "address", "credit_card",
    "card_number", "cvv", "national_id", "passport", "tax_id", "salary",
]

LARGE_TABLE_ROW_THRESHOLD = 5_000_000
HIGH_NULL_RATIO_THRESHOLD = 0.4


def _next_id(counter: list[int]) -> str:
    counter[0] += 1
    return f"R{counter[0]:03d}"


def assess_risks(
    source: SchemaSnapshot,
    target: SchemaSnapshot,
    table_mappings: list[TableMapping],
    cyclic_dependencies: list[list[str]],
    quality: DataQualityReport | None = None,
    unmatched_target_tables: list[str] | None = None,
) -> list[Risk]:
    counter = [0]
    risks: list[Risk] = []

    # --- Structural risks -------------------------------------------------
    for name, table in source.tables.items():
        if not table.primary_key:
            risks.append(
                Risk(
                    id=_next_id(counter),
                    category="schema",
                    severity=RiskSeverity.HIGH,
                    title=f"Table '{name}' has no primary key",
                    description=(
                        "Without a primary key, incremental/idempotent loads, "
                        "checksums and row-level reconciliation are unreliable."
                    ),
                    affected_objects=[name],
                    mitigation="Identify or synthesize a stable natural/surrogate key before migration.",
                )
            )

        if table.row_count is not None and table.row_count > LARGE_TABLE_ROW_THRESHOLD:
            risks.append(
                Risk(
                    id=_next_id(counter),
                    category="performance",
                    severity=RiskSeverity.MEDIUM,
                    title=f"Table '{name}' is large ({table.row_count:,} rows)",
                    description="Full-table loads at this size risk long load windows and lock contention.",
                    affected_objects=[name],
                    mitigation="Use chunked/parallel extraction (e.g. keyset pagination) and a dedicated load window.",
                )
            )

    if cyclic_dependencies:
        for component in cyclic_dependencies:
            risks.append(
                Risk(
                    id=_next_id(counter),
                    category="dependency",
                    severity=RiskSeverity.HIGH,
                    title=f"Circular foreign-key dependency: {', '.join(component)}",
                    description=(
                        "These tables reference each other in a cycle, so no single "
                        "load order satisfies all foreign keys."
                    ),
                    affected_objects=component,
                    mitigation=(
                        "Defer/disable FK constraints during load and validate referential "
                        "integrity afterward, or break the cycle with a nullable FK loaded in a second pass."
                    ),
                )
            )

    # --- Mapping risks ------------------------------------------------------
    for tm in table_mappings:
        if tm.target_table is None:
            risks.append(
                Risk(
                    id=_next_id(counter),
                    category="mapping",
                    severity=RiskSeverity.CRITICAL,
                    title=f"Source table '{tm.source_table}' has no target table",
                    description="This table's data has nowhere to land in the target schema as currently defined.",
                    affected_objects=[tm.source_table],
                    mitigation="Confirm the table is intentionally decommissioned, or add a target table for it.",
                )
            )
            continue

        unmatched_src = [c for c in tm.column_mappings if c.match_kind == MatchKind.UNMATCHED_SOURCE]
        if unmatched_src:
            risks.append(
                Risk(
                    id=_next_id(counter),
                    category="mapping",
                    severity=RiskSeverity.MEDIUM,
                    title=f"{len(unmatched_src)} unmapped source column(s) in '{tm.source_table}'",
                    description="These source columns have no target column and will be dropped unless mapped.",
                    affected_objects=[f"{tm.source_table}.{c.source_column}" for c in unmatched_src],
                    mitigation="Confirm each column is safe to drop, or map it to a new/renamed target column.",
                )
            )

        unmatched_tgt = [c for c in tm.column_mappings if c.match_kind == MatchKind.UNMATCHED_TARGET]
        if unmatched_tgt:
            risks.append(
                Risk(
                    id=_next_id(counter),
                    category="mapping",
                    severity=RiskSeverity.MEDIUM,
                    title=f"{len(unmatched_tgt)} target column(s) in '{tm.target_table}' have no source",
                    description="These target columns need a default, constant, or derivation rule to populate.",
                    affected_objects=[f"{tm.target_table}.{c.target_column}" for c in unmatched_tgt],
                    mitigation="Define a default value or transformation to populate these columns during load.",
                )
            )

        for cm in tm.column_mappings:
            if cm.notes and "truncation" in cm.notes.lower():
                risks.append(
                    Risk(
                        id=_next_id(counter),
                        category="data_quality",
                        severity=RiskSeverity.HIGH,
                        title=f"Possible truncation: {tm.source_table}.{cm.source_column} -> {tm.target_table}.{cm.target_column}",
                        description=cm.notes,
                        affected_objects=[f"{tm.source_table}.{cm.source_column}"],
                        mitigation="Widen the target column, or validate max source length fits before cutover.",
                    )
                )

            if cm.source_column and _looks_like_pii(cm.source_column):
                risks.append(
                    Risk(
                        id=_next_id(counter),
                        category="compliance",
                        severity=RiskSeverity.MEDIUM,
                        title=f"Possible sensitive data in {tm.source_table}.{cm.source_column}",
                        description="Column name suggests PII/sensitive data; confirm handling requirements.",
                        affected_objects=[f"{tm.source_table}.{cm.source_column}"],
                        mitigation="Apply masking/encryption/access controls per data classification policy before migrating.",
                    )
                )

    if unmatched_target_tables:
        risks.append(
            Risk(
                id=_next_id(counter),
                category="mapping",
                severity=RiskSeverity.LOW,
                title=f"{len(unmatched_target_tables)} target table(s) have no source counterpart",
                description="These target tables will start empty unless populated from elsewhere.",
                affected_objects=unmatched_target_tables,
                mitigation="Confirm these are net-new tables, seeded separately, or derived/computed post-load.",
            )
        )

    # --- Data quality risks ------------------------------------------------
    if quality:
        for profile in quality.column_profiles:
            if profile.row_count > 0 and profile.null_ratio >= HIGH_NULL_RATIO_THRESHOLD:
                target_requires_not_null = _target_requires_not_null(
                    target, table_mappings, profile.table, profile.column
                )
                if target_requires_not_null:
                    risks.append(
                        Risk(
                            id=_next_id(counter),
                            category="data_quality",
                            severity=RiskSeverity.HIGH,
                            title=f"{profile.table}.{profile.column} is {profile.null_ratio:.0%} null but target requires NOT NULL",
                            description="Loading this column as-is will violate the target's NOT NULL constraint.",
                            affected_objects=[f"{profile.table}.{profile.column}"],
                            mitigation="Backfill a default, relax the constraint, or scrub source data before load.",
                        )
                    )
                elif profile.null_ratio >= 0.8:
                    risks.append(
                        Risk(
                            id=_next_id(counter),
                            category="data_quality",
                            severity=RiskSeverity.LOW,
                            title=f"{profile.table}.{profile.column} is {profile.null_ratio:.0%} null",
                            description="Very high null ratio - confirm this column still carries value in the target.",
                            affected_objects=[f"{profile.table}.{profile.column}"],
                            mitigation="Confirm with data owner whether to migrate, drop, or investigate upstream cause.",
                        )
                    )

    return risks


def _looks_like_pii(column_name: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "_", column_name.lower())
    return any(pattern in normalized for pattern in _PII_PATTERNS)


def _target_requires_not_null(
    target: SchemaSnapshot, table_mappings: list[TableMapping], source_table: str, source_column: str
) -> bool:
    """True if the source column maps to a target column that is NOT NULL."""
    for tm in table_mappings:
        if tm.source_table != source_table or not tm.target_table:
            continue
        for cm in tm.column_mappings:
            if cm.source_column == source_column and cm.target_column:
                target_table = target.tables.get(tm.target_table)
                target_col = target_table.column(cm.target_column) if target_table else None
                if target_col is not None:
                    return not target_col.nullable
    return False
