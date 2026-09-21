"""Creates a small sample SQLite "legacy" source database so the agent can
be tried out end-to-end without needing a real database. Pair it with
``examples/target_schema.yaml`` as a declarative (not-yet-built) target.

Usage::

    python examples/generate_sample_dbs.py
    migration-agent plan \\
        --source-url sqlite:///examples/legacy_app.db \\
        --target-schema examples/target_schema.yaml \\
        --offline --output-dir out/
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    create_engine,
    insert,
)

DB_PATH = Path(__file__).parent / "legacy_app.db"


def build_schema(metadata: MetaData) -> dict[str, Table]:
    customers = Table(
        "customers",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("full_name", String(255), nullable=False),
        Column("email", String(255)),
        Column("phone", String(50)),
        Column("created_at", DateTime, nullable=False),
        Column("notes", Text),
    )

    products = Table(
        "products",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("sku", String(50), nullable=False),
        Column("name", String(255), nullable=False),
        Column("price", Numeric(10, 2), nullable=False),
        Column("description", Text),
    )

    orders = Table(
        "orders",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("customer_id", Integer, ForeignKey("customers.id"), nullable=False),
        Column("order_date", DateTime, nullable=False),
        Column("status", String(20), nullable=False),
        Column("total_amount", Numeric(12, 2), nullable=False),
    )

    order_items = Table(
        "order_items",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("order_id", Integer, ForeignKey("orders.id"), nullable=False),
        Column("product_id", Integer, ForeignKey("products.id"), nullable=False),
        Column("quantity", Integer, nullable=False),
        Column("unit_price", Numeric(10, 2), nullable=False),
    )

    # Intentionally has no primary key and no FK constraint (common in real
    # "legacy" databases) so the agent's risk assessor has something to flag.
    legacy_notes = Table(
        "legacy_notes",
        metadata,
        Column("note_id", Integer),
        Column("customer_id", Integer),
        Column("note_text", Text),
    )

    return {
        "customers": customers,
        "products": products,
        "orders": orders,
        "order_items": order_items,
        "legacy_notes": legacy_notes,
    }


def seed_data(engine, tables: dict[str, Table]) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(tables["customers"]),
            [
                {"id": 1, "full_name": "Ada Lovelace", "email": "ada@example.com", "phone": "555-0100",
                 "created_at": datetime(2021, 1, 5), "notes": "VIP customer"},
                {"id": 2, "full_name": "Grace Hopper", "email": "grace@example.com", "phone": "555-0101",
                 "created_at": datetime(2021, 3, 14), "notes": None},
                {"id": 3, "full_name": "Alan Turing", "email": None, "phone": None,
                 "created_at": datetime(2022, 6, 23), "notes": None},
            ],
        )
        conn.execute(
            insert(tables["products"]),
            [
                {"id": 1, "sku": "WIDGET-1", "name": "Widget", "price": 9.99, "description": "A widget."},
                {"id": 2, "sku": "GADGET-1", "name": "Gadget", "price": 19.99, "description": "A gadget."},
            ],
        )
        conn.execute(
            insert(tables["orders"]),
            [
                {"id": 1, "customer_id": 1, "order_date": datetime(2023, 1, 10), "status": "shipped", "total_amount": 29.98},
                {"id": 2, "customer_id": 2, "order_date": datetime(2023, 2, 2), "status": "pending", "total_amount": 9.99},
            ],
        )
        conn.execute(
            insert(tables["order_items"]),
            [
                {"id": 1, "order_id": 1, "product_id": 1, "quantity": 1, "unit_price": 9.99},
                {"id": 2, "order_id": 1, "product_id": 2, "quantity": 1, "unit_price": 19.99},
                {"id": 3, "order_id": 2, "product_id": 1, "quantity": 1, "unit_price": 9.99},
            ],
        )
        conn.execute(
            insert(tables["legacy_notes"]),
            [
                {"note_id": 1, "customer_id": 1, "note_text": "Called about invoice."},
            ],
        )


def main() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()

    engine = create_engine(f"sqlite:///{DB_PATH}")
    metadata = MetaData()
    tables = build_schema(metadata)
    metadata.create_all(engine)
    seed_data(engine, tables)
    print(f"Created sample database at {DB_PATH}")


if __name__ == "__main__":
    main()
