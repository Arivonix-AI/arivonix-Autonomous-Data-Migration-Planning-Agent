from migration_agent.dependency_graph import topological_order
from migration_agent.models import ForeignKeyInfo, SchemaSnapshot, TableInfo


def _table(name: str, fks: list[ForeignKeyInfo] | None = None) -> TableInfo:
    return TableInfo(name=name, columns=[], primary_key=["id"], foreign_keys=fks or [])


def _fk(referred_table: str) -> ForeignKeyInfo:
    return ForeignKeyInfo(constrained_columns=["x_id"], referred_table=referred_table, referred_columns=["id"])


def test_parents_before_children():
    schema = SchemaSnapshot(
        dialect="sqlite",
        tables={
            "customers": _table("customers"),
            "orders": _table("orders", [_fk("customers")]),
            "order_items": _table("order_items", [_fk("orders")]),
        },
    )
    order, cycles = topological_order(schema)

    assert cycles == []
    assert order.index("customers") < order.index("orders")
    assert order.index("orders") < order.index("order_items")
    assert set(order) == {"customers", "orders", "order_items"}


def test_independent_tables_all_included():
    schema = SchemaSnapshot(
        dialect="sqlite",
        tables={"a": _table("a"), "b": _table("b"), "c": _table("c")},
    )
    order, cycles = topological_order(schema)
    assert cycles == []
    assert set(order) == {"a", "b", "c"}


def test_cycle_detected_and_excluded_from_order():
    schema = SchemaSnapshot(
        dialect="sqlite",
        tables={
            "a": _table("a", [_fk("b")]),
            "b": _table("b", [_fk("a")]),
            "c": _table("c"),  # not involved in the cycle
        },
    )
    order, cycles = topological_order(schema)

    assert order == ["c"]
    assert cycles == [["a", "b"]]


def test_self_reference_is_not_a_cycle():
    schema = SchemaSnapshot(
        dialect="sqlite",
        tables={"tree": _table("tree", [_fk("tree")])},
    )
    order, cycles = topological_order(schema)
    assert order == ["tree"]
    assert cycles == []
