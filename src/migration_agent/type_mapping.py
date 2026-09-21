"""Cross-dialect type translation.

Source column types (as reported by SQLAlchemy reflection, e.g.
``VARCHAR(255)``, ``NUMERIC(10, 2)``) are first normalized into a small
generic type vocabulary, then rendered into the target dialect's syntax.
This keeps the N-source x M-target matrix to two small tables (normalize,
render) instead of one combinatorial mapping per pair of dialects.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class GenericType:
    kind: str  # e.g. "VARCHAR", "DECIMAL", "INTEGER"
    length: int | None = None
    precision: int | None = None
    scale: int | None = None


_SIZED_RE = re.compile(r"^(?P<kind>[A-Z0-9_ ]+?)\s*\((?P<args>[^)]*)\)\s*$")

# Longest-prefix-ish normalization: map a raw SQL type keyword to a generic
# kind. Order doesn't matter here since we match the leading keyword.
_KEYWORD_TO_GENERIC = {
    "BIGINT": "BIGINT",
    "SMALLINT": "SMALLINT",
    "TINYINT": "SMALLINT",
    "INT": "INTEGER",
    "INTEGER": "INTEGER",
    "SERIAL": "INTEGER",
    "BIGSERIAL": "BIGINT",
    "NUMERIC": "DECIMAL",
    "DECIMAL": "DECIMAL",
    "REAL": "FLOAT",
    "FLOAT": "FLOAT",
    "DOUBLE": "DOUBLE",
    "DOUBLE PRECISION": "DOUBLE",
    "BOOL": "BOOLEAN",
    "BOOLEAN": "BOOLEAN",
    "VARCHAR": "VARCHAR",
    "CHARACTER VARYING": "VARCHAR",
    "CHAR": "CHAR",
    "TEXT": "TEXT",
    "CLOB": "TEXT",
    "NVARCHAR": "VARCHAR",
    "DATE": "DATE",
    "TIME": "TIME",
    "DATETIME": "TIMESTAMP",
    "TIMESTAMP": "TIMESTAMP",
    "TIMESTAMP WITHOUT TIME ZONE": "TIMESTAMP",
    "TIMESTAMP WITH TIME ZONE": "TIMESTAMPTZ",
    "JSON": "JSON",
    "JSONB": "JSON",
    "BLOB": "BLOB",
    "BYTEA": "BLOB",
    "VARBINARY": "BLOB",
    "UUID": "UUID",
    "ENUM": "VARCHAR",
}


def normalize(raw_type: str) -> GenericType:
    """Parse a raw SQLAlchemy/SQL type string into a GenericType."""
    raw = raw_type.strip().upper()
    match = _SIZED_RE.match(raw)
    kind_token = match.group("kind").strip() if match else raw
    kind = _KEYWORD_TO_GENERIC.get(kind_token, kind_token.split(" ")[0])
    kind = _KEYWORD_TO_GENERIC.get(kind, kind)

    if not match:
        return GenericType(kind=kind)

    args = [a.strip() for a in match.group("args").split(",") if a.strip()]
    if kind in ("VARCHAR", "CHAR") and args:
        try:
            return GenericType(kind=kind, length=int(args[0]))
        except ValueError:
            return GenericType(kind=kind)
    if kind == "DECIMAL" and args:
        try:
            precision = int(args[0])
            scale = int(args[1]) if len(args) > 1 else 0
            return GenericType(kind=kind, precision=precision, scale=scale)
        except ValueError:
            return GenericType(kind=kind)
    return GenericType(kind=kind)


# Renderers per target dialect. Each takes a GenericType and returns the
# dialect-native DDL type string. Dialects not listed here fall back to
# the "generic" renderer (ANSI-ish, works for most warehouses).

def _render_generic(t: GenericType) -> str:
    if t.kind == "VARCHAR":
        return f"VARCHAR({t.length})" if t.length else "VARCHAR"
    if t.kind == "CHAR":
        return f"CHAR({t.length})" if t.length else "CHAR(1)"
    if t.kind == "DECIMAL":
        if t.precision is not None:
            return f"DECIMAL({t.precision},{t.scale or 0})"
        return "DECIMAL"
    return t.kind


def _render_snowflake(t: GenericType) -> str:
    if t.kind in ("VARCHAR", "CHAR", "TEXT"):
        return f"VARCHAR({t.length})" if t.length else "VARCHAR"
    if t.kind in ("BLOB",):
        return "BINARY"
    if t.kind == "JSON":
        return "VARIANT"
    if t.kind in ("TIMESTAMP", "TIMESTAMPTZ"):
        return "TIMESTAMP_NTZ" if t.kind == "TIMESTAMP" else "TIMESTAMP_TZ"
    if t.kind == "UUID":
        return "VARCHAR(36)"
    return _render_generic(t)


def _render_bigquery(t: GenericType) -> str:
    mapping = {
        "INTEGER": "INT64",
        "BIGINT": "INT64",
        "SMALLINT": "INT64",
        "FLOAT": "FLOAT64",
        "DOUBLE": "FLOAT64",
        "BOOLEAN": "BOOL",
        "TEXT": "STRING",
        "VARCHAR": "STRING",
        "CHAR": "STRING",
        "BLOB": "BYTES",
        "JSON": "JSON",
        "TIMESTAMP": "DATETIME",
        "TIMESTAMPTZ": "TIMESTAMP",
        "UUID": "STRING",
    }
    if t.kind == "DECIMAL":
        return f"NUMERIC({t.precision},{t.scale or 0})" if t.precision else "NUMERIC"
    return mapping.get(t.kind, t.kind)


def _render_postgresql(t: GenericType) -> str:
    if t.kind == "TIMESTAMPTZ":
        return "TIMESTAMP WITH TIME ZONE"
    if t.kind == "TIMESTAMP":
        return "TIMESTAMP"
    if t.kind == "JSON":
        return "JSONB"
    if t.kind == "BLOB":
        return "BYTEA"
    if t.kind == "UUID":
        return "UUID"
    return _render_generic(t)


def _render_mysql(t: GenericType) -> str:
    if t.kind == "TEXT" and not t.length:
        return "TEXT"
    if t.kind in ("TIMESTAMP", "TIMESTAMPTZ"):
        return "DATETIME"
    if t.kind == "BLOB":
        return "BLOB"
    if t.kind == "JSON":
        return "JSON"
    if t.kind == "UUID":
        return "CHAR(36)"
    if t.kind == "BOOLEAN":
        return "TINYINT(1)"
    return _render_generic(t)


_RENDERERS = {
    "postgresql": _render_postgresql,
    "postgres": _render_postgresql,
    "mysql": _render_mysql,
    "mariadb": _render_mysql,
    "snowflake": _render_snowflake,
    "bigquery": _render_bigquery,
    "sqlite": _render_generic,
    "mssql": _render_generic,
    "oracle": _render_generic,
    "generic": _render_generic,
}


@dataclass(frozen=True)
class TypeMappingResult:
    target_type: str
    lossy: bool
    note: str | None = None


def map_type(source_type: str, target_dialect: str) -> TypeMappingResult:
    """Translate a source column type into the target dialect's type,
    flagging cases that are likely to need manual review.
    """
    generic = normalize(source_type)
    renderer = _RENDERERS.get(target_dialect.lower(), _render_generic)
    target_type = renderer(generic)

    lossy = False
    note = None
    if generic.kind not in _KEYWORD_TO_GENERIC.values() and generic.kind not in (
        "VARCHAR", "CHAR", "TEXT", "DECIMAL",
    ):
        lossy = True
        note = f"Unrecognized source type '{source_type}'; mapped as-is to '{target_type}' - verify manually."

    return TypeMappingResult(target_type=target_type, lossy=lossy, note=note)


def is_narrowing(source_type: str, target_type: str) -> bool:
    """Best-effort check for VARCHAR(n) shrinking or DECIMAL precision loss,
    used by the risk assessor to flag possible truncation.
    """
    src = normalize(source_type)
    tgt = normalize(target_type)

    if src.kind in ("VARCHAR", "CHAR") and tgt.kind in ("VARCHAR", "CHAR"):
        if src.length and tgt.length and tgt.length < src.length:
            return True
    if src.kind == "DECIMAL" and tgt.kind == "DECIMAL":
        if src.precision and tgt.precision and tgt.precision < src.precision:
            return True
        if src.scale and tgt.scale is not None and tgt.scale < src.scale:
            return True
    return False
