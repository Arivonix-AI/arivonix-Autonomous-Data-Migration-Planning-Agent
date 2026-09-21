"""Typed data model for everything the agent produces.

These models are the contract between the introspection/analysis stages
(deterministic, code-driven) and the report renderer / optional LLM
synthesizer. Keeping them as pydantic models means the whole plan can be
serialized to JSON as-is, and validated on the way in or out.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------
# Schema introspection
# --------------------------------------------------------------------------

class ColumnInfo(BaseModel):
    name: str
    type: str
    nullable: bool = True
    primary_key: bool = False
    default: Optional[str] = None
    comment: Optional[str] = None


class ForeignKeyInfo(BaseModel):
    constrained_columns: list[str]
    referred_table: str
    referred_columns: list[str]


class IndexInfo(BaseModel):
    name: str
    columns: list[str]
    unique: bool = False


class TableInfo(BaseModel):
    name: str
    columns: list[ColumnInfo]
    primary_key: list[str] = Field(default_factory=list)
    foreign_keys: list[ForeignKeyInfo] = Field(default_factory=list)
    indexes: list[IndexInfo] = Field(default_factory=list)
    row_count: Optional[int] = None

    def column(self, name: str) -> Optional[ColumnInfo]:
        return next((c for c in self.columns if c.name == name), None)


class SchemaSnapshot(BaseModel):
    dialect: str
    tables: dict[str, TableInfo]

    @property
    def table_names(self) -> list[str]:
        return list(self.tables.keys())


# --------------------------------------------------------------------------
# Data quality profiling
# --------------------------------------------------------------------------

class ColumnProfile(BaseModel):
    table: str
    column: str
    row_count: int = 0
    null_count: int = 0
    distinct_count: Optional[int] = None
    min_value: Optional[str] = None
    max_value: Optional[str] = None
    sample_values: list[str] = Field(default_factory=list)

    @property
    def null_ratio(self) -> float:
        if self.row_count == 0:
            return 0.0
        return self.null_count / self.row_count


class DataQualityReport(BaseModel):
    column_profiles: list[ColumnProfile] = Field(default_factory=list)

    def for_table(self, table: str) -> list[ColumnProfile]:
        return [p for p in self.column_profiles if p.table == table]


# --------------------------------------------------------------------------
# Mapping
# --------------------------------------------------------------------------

class MatchKind(str, Enum):
    EXACT = "exact"
    FUZZY = "fuzzy"
    TYPE_CAST = "type_cast"
    UNMATCHED_SOURCE = "unmatched_source"
    UNMATCHED_TARGET = "unmatched_target"


class ColumnMapping(BaseModel):
    source_table: str
    source_column: Optional[str] = None
    target_table: str
    target_column: Optional[str] = None
    source_type: Optional[str] = None
    target_type: Optional[str] = None
    match_kind: MatchKind
    confidence: float = 0.0
    transformation: Optional[str] = None
    notes: Optional[str] = None


class TableMapping(BaseModel):
    source_table: str
    target_table: Optional[str]
    column_mappings: list[ColumnMapping] = Field(default_factory=list)
    unmatched_source_columns: list[str] = Field(default_factory=list)
    unmatched_target_columns: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Risk
# --------------------------------------------------------------------------

class RiskSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


_SEVERITY_ORDER = {
    RiskSeverity.CRITICAL: 0,
    RiskSeverity.HIGH: 1,
    RiskSeverity.MEDIUM: 2,
    RiskSeverity.LOW: 3,
}


class Risk(BaseModel):
    id: str
    category: str
    severity: RiskSeverity
    title: str
    description: str
    affected_objects: list[str] = Field(default_factory=list)
    mitigation: str

    @property
    def sort_key(self) -> int:
        return _SEVERITY_ORDER[self.severity]


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

class ValidationCheckType(str, Enum):
    ROW_COUNT = "row_count"
    NULL_PARITY = "null_parity"
    CHECKSUM = "checksum"
    REFERENTIAL_INTEGRITY = "referential_integrity"
    SAMPLE_DIFF = "sample_diff"
    BUSINESS_RULE = "business_rule"


class ValidationCheck(BaseModel):
    name: str
    type: ValidationCheckType
    target_objects: list[str]
    description: str
    source_query: Optional[str] = None
    target_query: Optional[str] = None


# --------------------------------------------------------------------------
# Execution plan
# --------------------------------------------------------------------------

class ExecutionPhase(BaseModel):
    order: int
    name: str
    description: str
    tables: list[str] = Field(default_factory=list)
    tasks: list[str] = Field(default_factory=list)
    entry_criteria: list[str] = Field(default_factory=list)
    exit_criteria: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Top-level plan
# --------------------------------------------------------------------------

class MigrationPlan(BaseModel):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source_dialect: str
    target_dialect: str
    architecture_summary: str
    migration_order: list[str]
    cyclic_dependencies: list[list[str]] = Field(default_factory=list)
    table_mappings: list[TableMapping]
    risks: list[Risk]
    validation_plan: list[ValidationCheck]
    execution_phases: list[ExecutionPhase]
    notes: list[str] = Field(default_factory=list)

    def risks_by_severity(self) -> list[Risk]:
        return sorted(self.risks, key=lambda r: r.sort_key)
