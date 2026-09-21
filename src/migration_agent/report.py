"""Renders a MigrationPlan to Markdown (for humans) and JSON (for tooling)."""
from __future__ import annotations

from .models import MigrationPlan

_SEVERITY_EMOJI_FREE_LABEL = {
    "critical": "CRITICAL",
    "high": "HIGH",
    "medium": "MEDIUM",
    "low": "LOW",
}


def to_json(plan: MigrationPlan) -> str:
    return plan.model_dump_json(indent=2)


def to_markdown(plan: MigrationPlan) -> str:
    lines: list[str] = []
    a = lines.append

    a("# Data Migration Plan")
    a("")
    a(f"- **Source dialect:** {plan.source_dialect}")
    a(f"- **Target dialect:** {plan.target_dialect}")
    a(f"- **Generated:** {plan.generated_at.isoformat()}")
    a("")

    a("## Executive Summary")
    a("")
    a(plan.architecture_summary)
    a("")

    a("## Migration Order (Dependency-Resolved)")
    a("")
    if plan.migration_order:
        for i, table in enumerate(plan.migration_order, start=1):
            a(f"{i}. `{table}`")
    else:
        a("_No tables resolved into a load order._")
    a("")
    if plan.cyclic_dependencies:
        a("### Circular Dependencies (excluded from ordering above)")
        a("")
        for group in plan.cyclic_dependencies:
            a(f"- {', '.join(f'`{t}`' for t in group)}")
        a("")

    a("## Table & Column Mappings")
    a("")
    for tm in plan.table_mappings:
        target_label = f"`{tm.target_table}`" if tm.target_table else "**(no target table found)**"
        a(f"### `{tm.source_table}` -> {target_label}")
        a("")
        if tm.column_mappings:
            a("| Source Column | Target Column | Source Type | Target Type | Match | Confidence | Notes |")
            a("|---|---|---|---|---|---|---|")
            for cm in tm.column_mappings:
                a(
                    "| {sc} | {tc} | {st} | {tt} | {mk} | {conf} | {notes} |".format(
                        sc=cm.source_column or "_(none)_",
                        tc=cm.target_column or "_(none)_",
                        st=cm.source_type or "",
                        tt=cm.target_type or "",
                        mk=cm.match_kind.value,
                        conf=f"{cm.confidence:.2f}",
                        notes=(cm.notes or "").replace("|", "/"),
                    )
                )
        a("")

    a("## Risks")
    a("")
    a("| ID | Severity | Category | Title | Affected Objects | Mitigation |")
    a("|---|---|---|---|---|---|")
    for r in plan.risks_by_severity():
        label = _SEVERITY_EMOJI_FREE_LABEL.get(r.severity.value, r.severity.value.upper())
        a(
            "| {id} | {sev} | {cat} | {title} | {objs} | {mit} |".format(
                id=r.id,
                sev=label,
                cat=r.category,
                title=r.title,
                objs=", ".join(f"`{o}`" for o in r.affected_objects) or "-",
                mit=r.mitigation,
            )
        )
    a("")

    a("## Validation Plan")
    a("")
    a("| Check | Type | Target Objects | Description |")
    a("|---|---|---|---|")
    for v in plan.validation_plan:
        a(
            "| {name} | {type} | {objs} | {desc} |".format(
                name=v.name,
                type=v.type.value,
                objs=", ".join(f"`{o}`" for o in v.target_objects),
                desc=v.description,
            )
        )
    a("")

    a("## Execution Phases")
    a("")
    for phase in sorted(plan.execution_phases, key=lambda p: p.order):
        a(f"### Phase {phase.order}: {phase.name}")
        a("")
        a(phase.description)
        a("")
        if phase.entry_criteria:
            a("**Entry criteria:**")
            for c in phase.entry_criteria:
                a(f"- {c}")
        if phase.tasks:
            a("**Tasks:**")
            for t in phase.tasks:
                a(f"- {t}")
        if phase.tables:
            shown = phase.tables[:20]
            more = f" (+{len(phase.tables) - 20} more)" if len(phase.tables) > 20 else ""
            a(f"**Tables:** {', '.join(f'`{t}`' for t in shown)}{more}")
        if phase.exit_criteria:
            a("**Exit criteria:**")
            for c in phase.exit_criteria:
                a(f"- {c}")
        a("")

    if plan.notes:
        a("## Notes")
        a("")
        for n in plan.notes:
            a(f"- {n}")
        a("")

    return "\n".join(lines)
