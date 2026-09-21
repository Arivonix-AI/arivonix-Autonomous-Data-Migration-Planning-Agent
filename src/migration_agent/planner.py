"""Orchestrates the full pipeline: schema introspection -> dependency graph
-> mapping -> risk assessment -> validation plan -> execution phases ->
optional LLM narrative synthesis -> MigrationPlan.
"""
from __future__ import annotations

from dataclasses import dataclass

from .dependency_graph import topological_order
from .llm import Synthesizer, get_synthesizer, synthesize_safely
from .mapper import build_table_mappings
from .models import (
    DataQualityReport,
    ExecutionPhase,
    MigrationPlan,
    SchemaSnapshot,
    TableMapping,
)
from .risk import assess_risks
from .validation import build_validation_plan


@dataclass
class PlannerInputs:
    source: SchemaSnapshot
    target: SchemaSnapshot
    quality: DataQualityReport | None = None


def build_execution_phases(
    migration_order: list[str],
    table_mappings: list[TableMapping],
    cyclic_dependencies: list[list[str]],
) -> list[ExecutionPhase]:
    source_to_target = {tm.source_table: tm.target_table for tm in table_mappings if tm.target_table}
    # Target table names, in the same dependency-safe order as migration_order,
    # skipping source tables that have no target counterpart.
    target_load_order = [source_to_target[t] for t in migration_order if t in source_to_target]
    cyclic_flat = [t for group in cyclic_dependencies for t in group]

    phases: list[ExecutionPhase] = [
        ExecutionPhase(
            order=0,
            name="Discovery & Environment Preparation",
            description="Confirm schema snapshots are current, provision target infrastructure, and freeze source DDL changes.",
            tables=[],
            tasks=[
                "Re-run schema introspection against the source to confirm this plan is current.",
                "Provision target database/warehouse and network/access connectivity for the migration tooling.",
                "Announce a DDL freeze window on the source schema for the duration of the migration.",
            ],
            entry_criteria=["Migration plan reviewed and approved by data/platform owners."],
            exit_criteria=["Target environment reachable; source DDL freeze acknowledged."],
        ),
        ExecutionPhase(
            order=1,
            name="Target Schema & DDL Migration",
            description="Create target tables, constraints and indexes in dependency order.",
            tables=list(target_load_order),
            tasks=[
                "Apply target DDL for each table in migration order (parents before children).",
                "Create indexes after bulk load where the target platform benefits from deferred indexing.",
            ],
            entry_criteria=["Phase 0 complete."],
            exit_criteria=["All target tables/constraints exist and validate against the mapping."],
        ),
        ExecutionPhase(
            order=2,
            name="Reference & Low-Dependency Data Load",
            description="Load tables with no (or few) inbound dependents first - lookup/reference tables.",
            tables=target_load_order[: max(1, len(target_load_order) // 3)],
            tasks=["Bulk load reference/lookup tables.", "Spot-check row counts against source."],
            entry_criteria=["Phase 1 complete for these tables."],
            exit_criteria=["Reference tables loaded and row counts reconciled."],
        ),
        ExecutionPhase(
            order=3,
            name="Transactional Data Load",
            description="Bulk load remaining tables in dependency order, respecting foreign keys.",
            tables=target_load_order,
            tasks=[
                "Load large/high-volume tables using chunked, parallel extraction.",
                "Apply column transformations/casts defined in the table mappings.",
                "Defer or validate foreign-key constraints for any circular-dependency tables." if cyclic_flat else "Enforce foreign-key constraints as each table loads.",
            ],
            entry_criteria=["Phase 2 complete."],
            exit_criteria=["All mapped tables loaded."],
        ),
        ExecutionPhase(
            order=4,
            name="Validation & Reconciliation",
            description="Run the validation plan (row counts, checksums, referential integrity, sample diffs).",
            tables=target_load_order,
            tasks=[
                "Execute every check in the Validation Plan.",
                "Resolve or formally accept any discrepancies found.",
                "Re-enable/verify foreign-key constraints deferred during load.",
            ],
            entry_criteria=["Phase 3 complete."],
            exit_criteria=["All validation checks pass or have a signed-off exception."],
        ),
        ExecutionPhase(
            order=5,
            name="Cutover & Decommission",
            description="Switch application traffic to the target system and retire the source once stable.",
            tables=[],
            tasks=[
                "Cut over application/service configuration to the target system.",
                "Run the source in read-only/standby mode for a rollback window.",
                "Decommission the source system once the rollback window closes without incident.",
            ],
            entry_criteria=["Phase 4 complete; stakeholder sign-off obtained."],
            exit_criteria=["Traffic fully on target; rollback window closed."],
        ),
    ]
    return phases


def build_plan(
    inputs: PlannerInputs,
    *,
    synthesizer: Synthesizer | None = None,
    offline: bool = False,
) -> MigrationPlan:
    source, target = inputs.source, inputs.target

    migration_order, cyclic_dependencies = topological_order(source)
    table_mappings, unmatched_target_tables = build_table_mappings(source, target)

    risks = assess_risks(
        source=source,
        target=target,
        table_mappings=table_mappings,
        cyclic_dependencies=cyclic_dependencies,
        quality=inputs.quality,
        unmatched_target_tables=unmatched_target_tables,
    )

    validation_plan = build_validation_plan(source=source, table_mappings=table_mappings, risks=risks)
    execution_phases = build_execution_phases(migration_order, table_mappings, cyclic_dependencies)

    risk_counts: dict[str, int] = {}
    for r in risks:
        risk_counts[r.severity.value] = risk_counts.get(r.severity.value, 0) + 1

    context = {
        "source_dialect": source.dialect,
        "target_dialect": target.dialect,
        "table_count": len(source.tables),
        "mapped_table_count": sum(1 for tm in table_mappings if tm.target_table),
        "unmatched_target_tables": unmatched_target_tables,
        "migration_order": migration_order,
        "cyclic_dependencies": cyclic_dependencies,
        "risk_counts": risk_counts,
        "top_risks": [
            {"id": r.id, "severity": r.severity.value, "title": r.title}
            for r in sorted(risks, key=lambda r: r.sort_key)[:15]
        ],
    }

    synthesizer = synthesizer or get_synthesizer(offline=offline)
    synthesis = synthesize_safely(synthesizer, context)

    all_risks = risks + synthesis.additional_risks

    return MigrationPlan(
        source_dialect=source.dialect,
        target_dialect=target.dialect,
        architecture_summary=synthesis.architecture_summary,
        migration_order=migration_order,
        cyclic_dependencies=cyclic_dependencies,
        table_mappings=table_mappings,
        risks=all_risks,
        validation_plan=validation_plan,
        execution_phases=execution_phases,
        notes=synthesis.notes,
    )
