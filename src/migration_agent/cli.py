"""Command-line entrypoint.

Examples
--------
Plan a migration between two live databases, with LLM-assisted narrative
(requires ANTHROPIC_API_KEY)::

    migration-agent plan \\
        --source-url postgresql://user:pass@host/source_db \\
        --target-url postgresql://user:pass@host/target_db \\
        --output-dir out/

Plan a migration into a target that doesn't exist yet, purely offline::

    migration-agent plan \\
        --source-url sqlite:///examples/legacy_app.db \\
        --target-schema examples/target_schema.yaml \\
        --offline --output-dir out/
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from . import report
from .introspection import connect, load_target_schema_spec, profile_data_quality, reflect_schema
from .planner import PlannerInputs, build_plan


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="migration-agent",
        description="Autonomous Data Migration Planning Agent",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    plan_cmd = sub.add_parser("plan", help="Analyze source/target and produce a migration plan.")
    plan_cmd.add_argument("--source-url", required=True, help="SQLAlchemy URL for the source database.")

    target_group = plan_cmd.add_mutually_exclusive_group(required=True)
    target_group.add_argument("--target-url", help="SQLAlchemy URL for an existing target database.")
    target_group.add_argument(
        "--target-schema", help="Path to a YAML/JSON declarative target schema (for a target that doesn't exist yet)."
    )

    plan_cmd.add_argument(
        "--output-dir", default="out", help="Directory to write migration_plan.md and migration_plan.json into."
    )
    plan_cmd.add_argument(
        "--profile",
        dest="profile",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run data-quality profiling queries against the source (default: on; disable with --no-profile).",
    )
    plan_cmd.add_argument(
        "--offline",
        action="store_true",
        help="Skip the LLM synthesis step even if ANTHROPIC_API_KEY is set.",
    )
    plan_cmd.add_argument(
        "--sample-size", type=int, default=5, help="Number of sample values to capture per profiled column."
    )

    return parser


def run_plan(args: argparse.Namespace) -> int:
    load_dotenv()

    source_engine = connect(args.source_url)
    source_schema = reflect_schema(source_engine)

    quality = None
    if args.profile:
        quality = profile_data_quality(source_engine, source_schema, sample_size=args.sample_size)

    if args.target_url:
        target_engine = connect(args.target_url)
        target_schema = reflect_schema(target_engine)
    else:
        target_schema = load_target_schema_spec(args.target_schema)

    plan = build_plan(
        PlannerInputs(source=source_schema, target=target_schema, quality=quality),
        offline=args.offline,
    )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    md_path = output_dir / "migration_plan.md"
    json_path = output_dir / "migration_plan.json"
    md_path.write_text(report.to_markdown(plan))
    json_path.write_text(report.to_json(plan))

    print(f"Wrote {md_path}")
    print(f"Wrote {json_path}")
    print(
        f"{len(plan.table_mappings)} table(s) analyzed, "
        f"{len(plan.risks)} risk(s) found, "
        f"{len(plan.validation_plan)} validation check(s) generated."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if args.command == "plan":
        return run_plan(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
