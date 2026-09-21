from datetime import datetime
from pathlib import Path

from sqlalchemy import Column, DateTime, ForeignKey, Integer, MetaData, Numeric, String, Table, Text, create_engine, insert

from migration_agent.introspection import load_target_schema_spec, profile_data_quality, reflect_schema
from migration_agent.models import RiskSeverity
from migration_agent.planner import PlannerInputs, build_plan
from migration_agent.report import to_markdown

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


def _build_source_engine():
    """Mirrors examples/generate_sample_dbs.py so the test doesn't depend
    on that script having been run, or on importing a non-package module.
    """
    engine = create_engine("sqlite://")
    metadata = MetaData()

    customers = Table(
        "customers", metadata,
        Column("id", Integer, primary_key=True),
        Column("full_name", String(255), nullable=False),
        Column("email", String(255)),
        Column("notes", Text),
    )
    products = Table(
        "products", metadata,
        Column("id", Integer, primary_key=True),
        Column("sku", String(50), nullable=False),
        Column("name", String(255), nullable=False),
        Column("price", Numeric(10, 2), nullable=False),
        Column("description", Text),
    )
    orders = Table(
        "orders", metadata,
        Column("id", Integer, primary_key=True),
        Column("customer_id", Integer, ForeignKey("customers.id"), nullable=False),
        Column("order_date", DateTime, nullable=False),
        Column("status", String(20), nullable=False),
        Column("total_amount", Numeric(12, 2), nullable=False),
    )
    order_items = Table(
        "order_items", metadata,
        Column("id", Integer, primary_key=True),
        Column("order_id", Integer, ForeignKey("orders.id"), nullable=False),
        Column("product_id", Integer, ForeignKey("products.id"), nullable=False),
        Column("quantity", Integer, nullable=False),
        Column("unit_price", Numeric(10, 2), nullable=False),
    )
    legacy_notes = Table(
        "legacy_notes", metadata,
        Column("note_id", Integer),
        Column("customer_id", Integer),
        Column("note_text", Text),
    )
    metadata.create_all(engine)

    with engine.begin() as conn:
        conn.execute(insert(customers), [{"id": 1, "full_name": "Ada Lovelace", "email": "ada@example.com", "notes": "VIP"}])
        conn.execute(insert(products), [{"id": 1, "sku": "W-1", "name": "Widget", "price": 9.99, "description": "x"}])
        conn.execute(insert(orders), [{"id": 1, "customer_id": 1, "order_date": datetime(2023, 1, 1), "status": "shipped", "total_amount": 9.99}])
        conn.execute(insert(order_items), [{"id": 1, "order_id": 1, "product_id": 1, "quantity": 1, "unit_price": 9.99}])
        conn.execute(insert(legacy_notes), [{"note_id": 1, "customer_id": 1, "note_text": "called"}])

    return engine


def _build_plan():
    engine = _build_source_engine()
    source = reflect_schema(engine)
    quality = profile_data_quality(engine, source)
    target = load_target_schema_spec(EXAMPLES_DIR / "target_schema.yaml")

    plan = build_plan(PlannerInputs(source=source, target=target, quality=quality), offline=True)
    return plan


def test_offline_plan_runs_without_network():
    plan = _build_plan()
    assert plan.source_dialect == "sqlite"
    assert plan.target_dialect == "postgresql"
    assert plan.architecture_summary  # heuristic synthesizer always produces text
    assert len(plan.execution_phases) == 6


def test_migration_order_respects_dependencies():
    plan = _build_plan()
    order = plan.migration_order
    assert order.index("customers") < order.index("orders")
    assert order.index("products") < order.index("order_items")
    assert order.index("orders") < order.index("order_items")


def test_expected_risks_present():
    plan = _build_plan()
    titles = " | ".join(r.title for r in plan.risks)

    # legacy_notes has no primary key
    assert any("no primary key" in r.title.lower() and "legacy_notes" in r.title for r in plan.risks)
    # legacy_notes has no target table
    assert any("no target table" in r.title.lower() for r in plan.risks)
    # email VARCHAR(50) target is narrower than source VARCHAR(255)
    assert any("truncation" in r.title.lower() or "truncation" in r.description.lower() for r in plan.risks)


def test_risks_sorted_by_severity():
    plan = _build_plan()
    severities = [r.severity for r in plan.risks_by_severity()]
    order_rank = {RiskSeverity.CRITICAL: 0, RiskSeverity.HIGH: 1, RiskSeverity.MEDIUM: 2, RiskSeverity.LOW: 3}
    ranks = [order_rank[s] for s in severities]
    assert ranks == sorted(ranks)


def test_validation_plan_covers_mapped_tables():
    plan = _build_plan()
    check_names = {c.name for c in plan.validation_plan}
    assert "row_count::customers" in check_names
    assert "row_count::orders" in check_names
    assert any(name.startswith("referential_integrity::") for name in check_names)


def test_markdown_report_renders():
    plan = _build_plan()
    markdown = to_markdown(plan)
    assert "# Data Migration Plan" in markdown
    assert "## Risks" in markdown
    assert "## Execution Phases" in markdown
