from migration_agent.mapper import build_table_mappings
from migration_agent.models import ColumnInfo, MatchKind, SchemaSnapshot, TableInfo


def _source_schema() -> SchemaSnapshot:
    return SchemaSnapshot(
        dialect="sqlite",
        tables={
            "customers": TableInfo(
                name="customers",
                primary_key=["id"],
                columns=[
                    ColumnInfo(name="id", type="INTEGER", primary_key=True, nullable=False),
                    ColumnInfo(name="full_name", type="VARCHAR(255)", nullable=False),
                    ColumnInfo(name="email", type="VARCHAR(255)", nullable=True),
                    ColumnInfo(name="notes", type="TEXT", nullable=True),
                ],
            ),
            "legacy_notes": TableInfo(
                name="legacy_notes",
                primary_key=[],
                columns=[ColumnInfo(name="note_text", type="TEXT")],
            ),
        },
    )


def _target_schema() -> SchemaSnapshot:
    return SchemaSnapshot(
        dialect="postgresql",
        tables={
            "crm_customers": TableInfo(
                name="crm_customers",
                primary_key=["id"],
                columns=[
                    ColumnInfo(name="id", type="BIGINT", primary_key=True, nullable=False),
                    ColumnInfo(name="fullname", type="VARCHAR(255)", nullable=False),
                    ColumnInfo(name="email", type="VARCHAR(50)", nullable=True),
                    ColumnInfo(name="loyalty_tier", type="VARCHAR(20)", nullable=True),
                ],
            ),
            "audit_log": TableInfo(
                name="audit_log",
                primary_key=["id"],
                columns=[ColumnInfo(name="id", type="BIGINT", primary_key=True)],
            ),
        },
    )


def test_table_fuzzy_match_and_unmatched_tables():
    mappings, unmatched_target_tables = build_table_mappings(_source_schema(), _target_schema())

    by_source = {m.source_table: m for m in mappings}
    assert by_source["customers"].target_table == "crm_customers"
    assert by_source["legacy_notes"].target_table is None
    assert unmatched_target_tables == ["audit_log"]


def test_column_exact_and_fuzzy_and_unmatched():
    mappings, _ = build_table_mappings(_source_schema(), _target_schema())
    customers_mapping = next(m for m in mappings if m.source_table == "customers")
    by_source_col = {cm.source_column: cm for cm in customers_mapping.column_mappings if cm.source_column}

    assert by_source_col["id"].match_kind == MatchKind.EXACT
    assert by_source_col["email"].match_kind == MatchKind.EXACT
    assert by_source_col["full_name"].match_kind == MatchKind.FUZZY
    assert by_source_col["full_name"].target_column == "fullname"

    assert "notes" in customers_mapping.unmatched_source_columns
    assert "loyalty_tier" in customers_mapping.unmatched_target_columns


def test_narrower_target_varchar_flagged_in_notes():
    mappings, _ = build_table_mappings(_source_schema(), _target_schema())
    customers_mapping = next(m for m in mappings if m.source_table == "customers")
    email_mapping = next(cm for cm in customers_mapping.column_mappings if cm.source_column == "email")

    assert email_mapping.notes is not None
    assert "truncation" in email_mapping.notes.lower()
