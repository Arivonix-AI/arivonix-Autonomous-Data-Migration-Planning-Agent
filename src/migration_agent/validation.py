"""Generates a validation/reconciliation plan: concrete checks to run after
each load phase to prove the migration is correct, not just "it ran".
"""
from __future__ import annotations

from .models import MatchKind, Risk, SchemaSnapshot, TableMapping, ValidationCheck, ValidationCheckType


def build_validation_plan(
    source: SchemaSnapshot,
    table_mappings: list[TableMapping],
    risks: list[Risk],
) -> list[ValidationCheck]:
    checks: list[ValidationCheck] = []

    for tm in table_mappings:
        if not tm.target_table:
            continue

        checks.append(
            ValidationCheck(
                name=f"row_count::{tm.source_table}",
                type=ValidationCheckType.ROW_COUNT,
                target_objects=[tm.source_table, tm.target_table],
                description=f"Row count in '{tm.target_table}' must equal row count in '{tm.source_table}' (or a documented delta).",
                source_query=f"SELECT COUNT(*) FROM {tm.source_table};",
                target_query=f"SELECT COUNT(*) FROM {tm.target_table};",
            )
        )

        mapped_pairs = [
            (cm.source_column, cm.target_column)
            for cm in tm.column_mappings
            if cm.match_kind in (MatchKind.EXACT, MatchKind.FUZZY) and cm.source_column and cm.target_column
        ]
        if mapped_pairs:
            src_cols = ", ".join(c for c, _ in mapped_pairs)
            checks.append(
                ValidationCheck(
                    name=f"null_parity::{tm.source_table}",
                    type=ValidationCheckType.NULL_PARITY,
                    target_objects=[tm.source_table, tm.target_table],
                    description=f"Per-column null counts for mapped columns in '{tm.target_table}' should match '{tm.source_table}' within tolerance.",
                    source_query=(
                        "SELECT "
                        + ", ".join(f"SUM(CASE WHEN {c} IS NULL THEN 1 ELSE 0 END) AS {c}_nulls" for c, _ in mapped_pairs)
                        + f" FROM {tm.source_table};"
                    ),
                )
            )

            src_pk = source.tables.get(tm.source_table)
            key_cols = src_pk.primary_key if src_pk and src_pk.primary_key else None
            if key_cols and all(any(sc == k for sc, _ in mapped_pairs) for k in key_cols):
                checks.append(
                    ValidationCheck(
                        name=f"checksum::{tm.source_table}",
                        type=ValidationCheckType.CHECKSUM,
                        target_objects=[tm.source_table, tm.target_table],
                        description=(
                            f"Row-level checksum (hash of mapped columns, keyed by {', '.join(key_cols)}) "
                            f"should match between '{tm.source_table}' and '{tm.target_table}' for a sampled or full set of keys."
                        ),
                    )
                )

            checks.append(
                ValidationCheck(
                    name=f"sample_diff::{tm.source_table}",
                    type=ValidationCheckType.SAMPLE_DIFF,
                    target_objects=[tm.source_table, tm.target_table],
                    description=f"Manually diff a random sample of rows from '{tm.source_table}' against '{tm.target_table}' for the mapped columns ({src_cols}).",
                )
            )

    for name, table in source.tables.items():
        if table.foreign_keys:
            checks.append(
                ValidationCheck(
                    name=f"referential_integrity::{name}",
                    type=ValidationCheckType.REFERENTIAL_INTEGRITY,
                    target_objects=[name] + [fk.referred_table for fk in table.foreign_keys],
                    description=(
                        f"Every foreign key in migrated '{name}' must resolve to an existing row in its "
                        f"referenced table(s): {', '.join(sorted({fk.referred_table for fk in table.foreign_keys}))}."
                    ),
                )
            )

    # Turn high/critical risks into explicit business-rule checks so the
    # validation plan closes the loop on what the risk assessment found.
    for risk in risks:
        if risk.severity.value in ("high", "critical") and risk.category in ("data_quality", "mapping"):
            checks.append(
                ValidationCheck(
                    name=f"business_rule::{risk.id}",
                    type=ValidationCheckType.BUSINESS_RULE,
                    target_objects=risk.affected_objects,
                    description=f"Verify mitigation for risk {risk.id} ({risk.title}) was applied before sign-off.",
                )
            )

    return checks
