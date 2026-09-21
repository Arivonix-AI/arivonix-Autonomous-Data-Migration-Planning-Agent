"""Extracts schema metadata and lightweight data-quality profiles from a
live database, or loads a declarative target schema from YAML/JSON when no
target database exists yet (e.g. a greenfield warehouse).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import MetaData, create_engine, func, select, text
from sqlalchemy.engine import Engine

from .models import (
    ColumnInfo,
    ColumnProfile,
    DataQualityReport,
    ForeignKeyInfo,
    IndexInfo,
    SchemaSnapshot,
    TableInfo,
)


def connect(url: str) -> Engine:
    return create_engine(url)


def reflect_schema(engine: Engine, *, schema: str | None = None) -> SchemaSnapshot:
    """Reflect all tables reachable from ``engine`` into a SchemaSnapshot."""
    metadata = MetaData()
    metadata.reflect(bind=engine, schema=schema)

    tables: dict[str, TableInfo] = {}
    for table in metadata.sorted_tables:
        columns = [
            ColumnInfo(
                name=col.name,
                type=str(col.type),
                nullable=col.nullable if col.nullable is not None else True,
                primary_key=col.primary_key,
                default=str(col.server_default.arg) if col.server_default is not None else None,
                comment=col.comment,
            )
            for col in table.columns
        ]

        foreign_keys = []
        for fk_constraint in table.foreign_key_constraints:
            referred_table = fk_constraint.referred_table.name
            constrained_columns = list(fk_constraint.column_keys)
            referred_columns = [fk.column.name for fk in fk_constraint.elements]
            foreign_keys.append(
                ForeignKeyInfo(
                    constrained_columns=constrained_columns,
                    referred_table=referred_table,
                    referred_columns=referred_columns,
                )
            )

        indexes = [
            IndexInfo(
                name=idx.name or f"{table.name}_idx",
                columns=[c.name for c in idx.columns],
                unique=idx.unique or False,
            )
            for idx in table.indexes
        ]

        tables[table.name] = TableInfo(
            name=table.name,
            columns=columns,
            primary_key=[c.name for c in table.primary_key.columns],
            foreign_keys=foreign_keys,
            indexes=indexes,
        )

    return SchemaSnapshot(dialect=engine.dialect.name, tables=tables)


def profile_data_quality(
    engine: Engine,
    schema: SchemaSnapshot,
    *,
    sample_size: int = 5,
    max_distinct_scan_rows: int = 2_000_000,
) -> DataQualityReport:
    """Run lightweight profiling queries against every table/column.

    Kept deliberately simple (COUNT/COUNT DISTINCT/MIN/MAX plus a small
    sample) so it stays cheap even on tables with millions of rows; callers
    that need deeper profiling can extend ColumnProfile and this function.
    """
    profiles: list[ColumnProfile] = []
    metadata = MetaData()
    metadata.reflect(bind=engine)

    with engine.connect() as conn:
        for table_name, table_info in schema.tables.items():
            sa_table = metadata.tables.get(table_name)
            if sa_table is None:
                continue

            row_count = conn.execute(select(func.count()).select_from(sa_table)).scalar_one()
            table_info.row_count = row_count

            for col_info in table_info.columns:
                sa_col = sa_table.c[col_info.name]
                null_count = conn.execute(
                    select(func.count()).select_from(sa_table).where(sa_col.is_(None))
                ).scalar_one()

                distinct_count = None
                min_value = max_value = None
                if row_count <= max_distinct_scan_rows:
                    try:
                        distinct_count = conn.execute(
                            select(func.count(func.distinct(sa_col)))
                        ).scalar_one()
                        bounds = conn.execute(
                            select(func.min(sa_col), func.max(sa_col))
                        ).one()
                        min_value = None if bounds[0] is None else str(bounds[0])
                        max_value = None if bounds[1] is None else str(bounds[1])
                    except Exception:
                        # Some column types (blobs, json, etc.) don't support
                        # MIN/MAX/DISTINCT on every backend - profiling is
                        # best-effort, so skip rather than fail the run.
                        pass

                samples: list[str] = []
                if sample_size > 0 and row_count > 0:
                    try:
                        rows = conn.execute(
                            select(sa_col).where(sa_col.is_not(None)).limit(sample_size)
                        ).scalars().all()
                        samples = [str(r) for r in rows]
                    except Exception:
                        pass

                profiles.append(
                    ColumnProfile(
                        table=table_name,
                        column=col_info.name,
                        row_count=row_count,
                        null_count=null_count,
                        distinct_count=distinct_count,
                        min_value=min_value,
                        max_value=max_value,
                        sample_values=samples,
                    )
                )

    return DataQualityReport(column_profiles=profiles)


def load_target_schema_spec(path: str | Path) -> SchemaSnapshot:
    """Load a declarative target schema from a YAML/JSON spec file.

    Used when the target system doesn't exist yet (new warehouse, new
    service database) and there is nothing to reflect against. Expected
    shape::

        dialect: postgresql
        tables:
          customers:
            columns:
              - {name: id, type: BIGINT, nullable: false, primary_key: true}
              - {name: email, type: VARCHAR(255), nullable: false}
            primary_key: [id]
            foreign_keys:
              - {constrained_columns: [account_id], referred_table: accounts, referred_columns: [id]}
    """
    path = Path(path)
    raw: dict[str, Any] = yaml.safe_load(path.read_text())

    tables: dict[str, TableInfo] = {}
    for table_name, table_def in (raw.get("tables") or {}).items():
        columns = [ColumnInfo(**c) for c in table_def.get("columns", [])]
        foreign_keys = [ForeignKeyInfo(**fk) for fk in table_def.get("foreign_keys", [])]
        indexes = [IndexInfo(**idx) for idx in table_def.get("indexes", [])]
        primary_key = table_def.get("primary_key") or [c.name for c in columns if c.primary_key]

        tables[table_name] = TableInfo(
            name=table_name,
            columns=columns,
            primary_key=primary_key,
            foreign_keys=foreign_keys,
            indexes=indexes,
        )

    return SchemaSnapshot(dialect=raw.get("dialect", "generic"), tables=tables)
