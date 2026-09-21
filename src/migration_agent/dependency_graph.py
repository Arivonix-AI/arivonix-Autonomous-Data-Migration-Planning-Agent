"""Builds a table-level dependency graph from foreign keys and computes a
safe migration/load order via topological sort (Kahn's algorithm).

An edge ``child -> parent`` means "child depends on parent" (the child's FK
references the parent), so parents must be loaded first.
"""
from __future__ import annotations

from collections import deque

from .models import SchemaSnapshot


def build_dependency_edges(schema: SchemaSnapshot) -> dict[str, set[str]]:
    """Return {table: {tables it depends on}} for tables present in schema.

    Self-references and foreign keys pointing at tables outside the
    snapshot are ignored (nothing to order them against).
    """
    edges: dict[str, set[str]] = {name: set() for name in schema.tables}
    for name, table in schema.tables.items():
        for fk in table.foreign_keys:
            if fk.referred_table == name:
                continue  # self-referencing FK, not an ordering constraint
            if fk.referred_table in schema.tables:
                edges[name].add(fk.referred_table)
    return edges


def topological_order(schema: SchemaSnapshot) -> tuple[list[str], list[list[str]]]:
    """Kahn's algorithm. Returns (order, cycles).

    ``order`` lists tables parents-first (safe load order). Any tables
    involved in a circular dependency are excluded from ``order`` and
    reported (grouped by strongly-connected remainder) in ``cycles`` so the
    caller can flag them as a risk rather than silently dropping them.
    """
    depends_on = build_dependency_edges(schema)

    # dependents[p] = tables that depend on p (reverse edges), for Kahn's algorithm
    dependents: dict[str, set[str]] = {name: set() for name in schema.tables}
    in_degree: dict[str, int] = {name: 0 for name in schema.tables}
    for table, deps in depends_on.items():
        in_degree[table] = len(deps)
        for dep in deps:
            dependents[dep].add(table)

    queue = deque(sorted(t for t, deg in in_degree.items() if deg == 0))
    order: list[str] = []
    remaining_in_degree = dict(in_degree)

    while queue:
        table = queue.popleft()
        order.append(table)
        for dependent in sorted(dependents[table]):
            remaining_in_degree[dependent] -= 1
            if remaining_in_degree[dependent] == 0:
                queue.append(dependent)

    ordered_set = set(order)
    unresolved = [t for t in schema.tables if t not in ordered_set]
    cycles = _group_cycle_components(unresolved, depends_on) if unresolved else []

    return order, cycles


def _group_cycle_components(unresolved: list[str], depends_on: dict[str, set[str]]) -> list[list[str]]:
    """Group unresolved (cyclic) tables into connected components for a
    more readable report, e.g. [["a", "b"], ["c", "d", "e"]] instead of a
    single flat list.
    """
    unresolved_set = set(unresolved)
    undirected: dict[str, set[str]] = {t: set() for t in unresolved}
    for table in unresolved:
        for dep in depends_on.get(table, set()):
            if dep in unresolved_set:
                undirected[table].add(dep)
                undirected[dep].add(table)

    seen: set[str] = set()
    components: list[list[str]] = []
    for start in unresolved:
        if start in seen:
            continue
        stack = [start]
        component = []
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            component.append(node)
            stack.extend(undirected[node] - seen)
        components.append(sorted(component))

    return components
