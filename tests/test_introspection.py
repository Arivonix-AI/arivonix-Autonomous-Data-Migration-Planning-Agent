from pathlib import Path

from sqlalchemy import Column, ForeignKey, Integer, MetaData, String, Table, create_engine, insert

from migration_agent.introspection import load_target_schema_spec, profile_data_quality, reflect_schema

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


def _build_sample_engine():
    engine = create_engine("sqlite://")
    metadata = MetaData()
    parents = Table(
        "parents",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("name", String(50), nullable=False),
    )
    children = Table(
        "children",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("parent_id", Integer, ForeignKey("parents.id"), nullable=False),
        Column("label", String(50), nullable=True),
    )
    metadata.create_all(engine)

    with engine.begin() as conn:
        conn.execute(insert(parents), [{"id": 1, "name": "root"}])
        conn.execute(
            insert(children),
            [
                {"id": 1, "parent_id": 1, "label": "a"},
                {"id": 2, "parent_id": 1, "label": None},
            ],
        )
    return engine


def test_reflect_schema_captures_tables_columns_and_fks():
    engine = _build_sample_engine()
    schema = reflect_schema(engine)

    assert set(schema.table_names) == {"parents", "children"}
    assert schema.tables["parents"].primary_key == ["id"]

    children = schema.tables["children"]
    assert len(children.foreign_keys) == 1
    fk = children.foreign_keys[0]
    assert fk.referred_table == "parents"
    assert fk.constrained_columns == ["parent_id"]


def test_profile_data_quality_counts_nulls_and_rows():
    engine = _build_sample_engine()
    schema = reflect_schema(engine)
    report = profile_data_quality(engine, schema, sample_size=2)

    label_profile = next(p for p in report.column_profiles if p.table == "children" and p.column == "label")
    assert label_profile.row_count == 2
    assert label_profile.null_count == 1
    assert label_profile.null_ratio == 0.5

    parents_profile = next(p for p in report.column_profiles if p.table == "parents" and p.column == "name")
    assert parents_profile.row_count == 1
    assert parents_profile.null_count == 0


def test_load_target_schema_spec_from_yaml():
    schema = load_target_schema_spec(EXAMPLES_DIR / "target_schema.yaml")

    assert schema.dialect == "postgresql"
    assert "crm_customers" in schema.tables
    crm = schema.tables["crm_customers"]
    assert crm.primary_key == ["id"]
    id_col = crm.column("id")
    assert id_col is not None
    assert id_col.primary_key is True

    orders = schema.tables["orders"]
    assert len(orders.foreign_keys) == 1
    assert orders.foreign_keys[0].referred_table == "crm_customers"
