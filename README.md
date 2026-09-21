# Autonomous Data Migration Planning Agent

Analyzes a source database's schema, table dependencies and data quality
alongside a target schema (an existing database, or a declarative spec for
a system that doesn't exist yet) to produce a concrete **migration plan**:
architecture summary, table/column mappings, a risk assessment, a
validation plan, and a phased execution plan.

Every stage except the narrative summary is deterministic, rule-based code
(schema reflection, dependency-graph topological sort, name/type matching
heuristics, rule-based risk detection) - the agent runs fully **offline**
with zero external calls. An optional LLM stage (Claude, via the
`anthropic` SDK) can be layered on top to write the architecture narrative
in prose and flag additional judgment-call risks a rule can't easily
express; if no API key is configured, or the call fails for any reason, the
agent transparently falls back to a templated offline summary.

## What it does

1. **Schema introspection** - reflects the source database (tables,
   columns, types, primary keys, foreign keys, indexes) via SQLAlchemy.
   The target is either reflected the same way (an existing database) or
   loaded from a declarative YAML/JSON spec (a system that doesn't exist
   yet - see `examples/target_schema.yaml`).
2. **Dependency analysis** - builds a foreign-key dependency graph and
   computes a safe, parents-first load order via topological sort,
   detecting and reporting any circular dependencies.
3. **Data quality profiling** - lightweight per-column profiling (row
   count, null count/ratio, distinct count, min/max, sample values)
   against the source.
4. **Schema mapping** - matches source tables/columns to target
   tables/columns by exact then fuzzy name matching, and flags type
   mismatches, including likely truncation (e.g. `VARCHAR(255)` ->
   `VARCHAR(50)`, or precision-narrowing `DECIMAL`s).
5. **Risk assessment** - rule-based detection of missing primary keys,
   large tables, circular dependencies, unmapped columns/tables, likely
   truncation, NOT NULL violations implied by source null ratios, and
   PII/sensitive-data column names.
6. **Validation plan** - row count, null parity, checksum, referential
   integrity and sample-diff checks per table, plus explicit checks tying
   back to each high/critical risk's mitigation.
7. **Execution plan** - six phases: discovery & prep, target DDL, reference
   data load, transactional data load, validation & reconciliation, and
   cutover & decommission.
8. **Optional LLM synthesis** - Claude writes the executive-summary
   narrative and can add further risks; entirely optional and safely
   sandboxed behind a try/except with an offline fallback.

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"        # add ".[llm]" too, or just install anthropic, for LLM synthesis
```

## Quickstart (fully offline, no database required)

```bash
python examples/generate_sample_dbs.py

migration-agent plan \
  --source-url sqlite:///examples/legacy_app.db \
  --target-schema examples/target_schema.yaml \
  --offline \
  --output-dir out/

cat out/migration_plan.md
```

This produces `out/migration_plan.md` (human-readable) and
`out/migration_plan.json` (the full `MigrationPlan`, for downstream
tooling). The bundled sample data deliberately exercises every code path:
a renamed table, a table with no primary key, a table with no target
counterpart, a target-only table, a truncating type change, and
PII-looking column names.

## Real usage

Against two live databases, with Claude-synthesized narrative (set
`ANTHROPIC_API_KEY`, e.g. via a `.env` file - see `.env.example`):

```bash
migration-agent plan \
  --source-url postgresql://user:pass@host/source_db \
  --target-url postgresql://user:pass@host/target_db \
  --output-dir out/
```

Against a target that doesn't exist yet (a redesigned schema, a new
warehouse), skip `--target-url` and pass a declarative spec instead - see
`examples/target_schema.yaml` for the format:

```bash
migration-agent plan \
  --source-url mysql+pymysql://user:pass@host/legacy_db \
  --target-schema target_schema.yaml \
  --output-dir out/
```

Flags:

- `--offline` - skip the LLM stage even if `ANTHROPIC_API_KEY` is set.
- `--no-profile` - skip data-quality profiling (faster on very large
  source databases; the rest of the plan still runs).
- `--sample-size N` - sample values captured per profiled column (default 5).

## Project layout

```
src/migration_agent/
  models.py            Pydantic data model shared by every stage (the plan's contract)
  introspection.py      Schema reflection, data-quality profiling, YAML target-spec loader
  dependency_graph.py    Foreign-key dependency graph + topological sort
  type_mapping.py        Cross-dialect type normalization/translation
  mapper.py               Table/column matching heuristics (exact + fuzzy)
  risk.py                 Rule-based risk detection
  validation.py           Validation/reconciliation check generation
  llm.py                  Optional Claude-backed narrative synthesis + offline fallback
  planner.py              Orchestrates all of the above into a MigrationPlan
  report.py               Renders a MigrationPlan to Markdown / JSON
  cli.py                  `migration-agent plan ...` entrypoint
examples/
  generate_sample_dbs.py  Builds a sample SQLite source database
  target_schema.yaml      Sample declarative target schema
tests/                    pytest suite (offline; no network or live DB required)
```

## Running tests

```bash
pip install -e ".[dev]"
pytest
```
