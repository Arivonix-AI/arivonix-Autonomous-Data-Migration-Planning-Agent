"""Heuristic table/column matching between a source and target schema.

Matching strategy, cheapest-first:
  1. Exact name match (case/underscore-insensitive) -> confidence 1.0
  2. Fuzzy name match (difflib ratio) above a threshold -> confidence = ratio
  3. Anything left over is reported as unmatched, for a human (or the LLM
     synthesizer, if enabled) to resolve.

Tables are matched the same way, then columns are matched within each
matched table pair. This is intentionally simple and explainable - every
decision traces back to a name comparison and/or a type check, which is
what makes it possible to run entirely offline.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass

from .models import ColumnMapping, MatchKind, SchemaSnapshot, TableInfo, TableMapping
from .type_mapping import is_narrowing, map_type

FUZZY_THRESHOLD = 0.72


def _normalize(name: str) -> str:
    return name.strip().lower().replace("-", "_").replace(" ", "_")


def _best_fuzzy_match(name: str, candidates: list[str]) -> tuple[str, float] | None:
    if not candidates:
        return None
    norm_name = _normalize(name)
    scored = [
        (cand, difflib.SequenceMatcher(None, norm_name, _normalize(cand)).ratio())
        for cand in candidates
    ]
    scored.sort(key=lambda x: x[1], reverse=True)
    best_candidate, best_score = scored[0]
    if best_score >= FUZZY_THRESHOLD:
        return best_candidate, best_score
    return None


@dataclass
class _GreedyMatch:
    matched: dict[str, str]
    unmatched_left: list[str]
    unmatched_right: list[str]


def _greedy_match(left: list[str], right: list[str]) -> _GreedyMatch:
    """Match names from `left` to `right`, exact first then fuzzy, each
    right-hand candidate usable at most once.
    """
    matched: dict[str, str] = {}
    remaining_right = list(right)

    still_left = []
    for name in left:
        norm = _normalize(name)
        exact = next((r for r in remaining_right if _normalize(r) == norm), None)
        if exact:
            matched[name] = exact
            remaining_right.remove(exact)
        else:
            still_left.append(name)

    unresolved_left = []
    for name in still_left:
        found = _best_fuzzy_match(name, remaining_right)
        if found:
            candidate, _score = found
            matched[name] = candidate
            remaining_right.remove(candidate)
        else:
            unresolved_left.append(name)

    return _GreedyMatch(matched=matched, unmatched_left=unresolved_left, unmatched_right=remaining_right)


def match_tables(source: SchemaSnapshot, target: SchemaSnapshot) -> _GreedyMatch:
    return _greedy_match(source.table_names, target.table_names)


def match_columns(source_table: TableInfo, target_table: TableInfo, target_dialect: str) -> TableMapping:
    source_names = [c.name for c in source_table.columns]
    target_names = [c.name for c in target_table.columns]
    result = _greedy_match(source_names, target_names)

    column_mappings: list[ColumnMapping] = []
    for src_name, tgt_name in result.matched.items():
        src_col = source_table.column(src_name)
        tgt_col = target_table.column(tgt_name)
        assert src_col and tgt_col

        kind = MatchKind.EXACT if _normalize(src_name) == _normalize(tgt_name) else MatchKind.FUZZY
        score = 1.0 if kind == MatchKind.EXACT else difflib.SequenceMatcher(
            None, _normalize(src_name), _normalize(tgt_name)
        ).ratio()

        expected = map_type(src_col.type, target_dialect)
        transformation = None
        notes = None
        if _normalize(expected.target_type) != _normalize(tgt_col.type):
            transformation = f"CAST({src_name} AS {tgt_col.type})"
            notes = f"Target column type '{tgt_col.type}' differs from expected '{expected.target_type}'."
        if is_narrowing(src_col.type, tgt_col.type):
            notes = ((notes + " ") if notes else "") + "Target type is narrower than source - possible truncation."

        column_mappings.append(
            ColumnMapping(
                source_table=source_table.name,
                source_column=src_name,
                target_table=target_table.name,
                target_column=tgt_name,
                source_type=src_col.type,
                target_type=tgt_col.type,
                match_kind=kind,
                confidence=round(score, 3),
                transformation=transformation,
                notes=notes,
            )
        )

    for src_name in result.unmatched_left:
        src_col = source_table.column(src_name)
        column_mappings.append(
            ColumnMapping(
                source_table=source_table.name,
                source_column=src_name,
                target_table=target_table.name,
                target_column=None,
                source_type=src_col.type if src_col else None,
                match_kind=MatchKind.UNMATCHED_SOURCE,
                confidence=0.0,
                notes="No corresponding target column found - decide drop, rename, or new target column.",
            )
        )

    for tgt_name in result.unmatched_right:
        tgt_col = target_table.column(tgt_name)
        column_mappings.append(
            ColumnMapping(
                source_table=source_table.name,
                source_column=None,
                target_table=target_table.name,
                target_column=tgt_name,
                target_type=tgt_col.type if tgt_col else None,
                match_kind=MatchKind.UNMATCHED_TARGET,
                confidence=0.0,
                notes="No source column maps here - needs a default, constant, or derivation rule.",
            )
        )

    return TableMapping(
        source_table=source_table.name,
        target_table=target_table.name,
        column_mappings=column_mappings,
        unmatched_source_columns=result.unmatched_left,
        unmatched_target_columns=result.unmatched_right,
    )


def build_table_mappings(
    source: SchemaSnapshot, target: SchemaSnapshot
) -> tuple[list[TableMapping], list[str]]:
    """Returns (table_mappings, unmatched_target_table_names)."""
    table_match = match_tables(source, target)
    mappings: list[TableMapping] = []

    for src_table_name, tgt_table_name in table_match.matched.items():
        mappings.append(
            match_columns(source.tables[src_table_name], target.tables[tgt_table_name], target.dialect)
        )

    for src_table_name in table_match.unmatched_left:
        src_table = source.tables[src_table_name]
        mappings.append(
            TableMapping(
                source_table=src_table_name,
                target_table=None,
                column_mappings=[
                    ColumnMapping(
                        source_table=src_table_name,
                        source_column=c.name,
                        target_table="(none)",
                        target_column=None,
                        source_type=c.type,
                        match_kind=MatchKind.UNMATCHED_SOURCE,
                        confidence=0.0,
                        notes="No corresponding target table found.",
                    )
                    for c in src_table.columns
                ],
                unmatched_source_columns=[c.name for c in src_table.columns],
            )
        )

    return mappings, table_match.unmatched_right
