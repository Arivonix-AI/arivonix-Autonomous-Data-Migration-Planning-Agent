from migration_agent.type_mapping import is_narrowing, map_type, normalize


def test_normalize_sized_varchar():
    t = normalize("VARCHAR(255)")
    assert t.kind == "VARCHAR"
    assert t.length == 255


def test_normalize_decimal_with_scale():
    t = normalize("NUMERIC(10, 2)")
    assert t.kind == "DECIMAL"
    assert t.precision == 10
    assert t.scale == 2


def test_map_varchar_to_snowflake():
    result = map_type("VARCHAR(100)", "snowflake")
    assert result.target_type == "VARCHAR(100)"
    assert result.lossy is False


def test_map_json_to_snowflake_becomes_variant():
    result = map_type("JSON", "snowflake")
    assert result.target_type == "VARIANT"


def test_map_integer_to_bigquery():
    result = map_type("INTEGER", "bigquery")
    assert result.target_type == "INT64"


def test_map_timestamp_to_postgresql():
    result = map_type("TIMESTAMP", "postgresql")
    assert result.target_type == "TIMESTAMP"


def test_unrecognized_type_flagged_lossy():
    result = map_type("SOME_EXOTIC_TYPE", "postgresql")
    assert result.lossy is True
    assert "verify manually" in result.note


def test_is_narrowing_varchar():
    assert is_narrowing("VARCHAR(255)", "VARCHAR(50)") is True
    assert is_narrowing("VARCHAR(50)", "VARCHAR(255)") is False


def test_is_narrowing_decimal_precision():
    assert is_narrowing("NUMERIC(10,2)", "NUMERIC(6,2)") is True
    assert is_narrowing("NUMERIC(6,2)", "NUMERIC(10,2)") is False
