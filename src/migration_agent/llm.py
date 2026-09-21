"""Optional LLM-backed synthesis stage.

Everything upstream (introspection, dependency graph, mapping, rule-based
risk assessment, validation plan) is deterministic and works with zero
external calls. This module adds an optional layer on top: given the
already-computed facts, ask Claude to write the architecture narrative and
flag any additional risks a human reviewer would likely raise - the kind of
judgment calls that don't reduce to a fixed rule.

If no API key is configured, the `anthropic` package isn't installed, or
the call fails for any reason, `HeuristicSynthesizer` produces a reasonable
fallback so the planner never hard-depends on network access.
"""
from __future__ import annotations

import json
import os
from typing import Any, Protocol

from pydantic import BaseModel, Field

from .models import Risk, RiskSeverity

DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")


class LLMSynthesis(BaseModel):
    architecture_summary: str
    additional_risks: list[Risk] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class Synthesizer(Protocol):
    def synthesize(self, context: dict[str, Any]) -> LLMSynthesis: ...


class HeuristicSynthesizer:
    """Deterministic, offline fallback. Produces a serviceable narrative
    from the same context an LLM would receive, using templated prose.
    """

    def synthesize(self, context: dict[str, Any]) -> LLMSynthesis:
        table_count = context.get("table_count", 0)
        mapped_count = context.get("mapped_table_count", 0)
        source_dialect = context.get("source_dialect", "source")
        target_dialect = context.get("target_dialect", "target")
        risk_counts = context.get("risk_counts", {})
        cycles = context.get("cyclic_dependencies", [])

        summary_lines = [
            f"This plan migrates {table_count} table(s) from {source_dialect} to {target_dialect}, "
            f"with {mapped_count} table(s) resolved to a target counterpart automatically by name matching.",
            "Migration follows dependency (foreign-key) order so that referenced/parent tables are loaded "
            "before the tables that depend on them, minimizing constraint violations during load.",
        ]
        if cycles:
            summary_lines.append(
                f"{len(cycles)} circular dependency group(s) were detected and require constraint "
                "deferral or a two-pass load strategy (see Risks)."
            )
        if risk_counts.get("critical") or risk_counts.get("high"):
            summary_lines.append(
                f"{risk_counts.get('critical', 0)} critical and {risk_counts.get('high', 0)} high-severity "
                "risks were identified and should be resolved before the production cutover phase."
            )
        summary_lines.append(
            "Recommended approach: stand up the target schema, backfill in dependency order using "
            "chunked batch loads for large tables, validate with the checks in the Validation Plan, "
            "then cut traffic over in a low-activity window with rollback via the source system kept "
            "read-only during the validation soak period."
        )

        return LLMSynthesis(
            architecture_summary=" ".join(summary_lines),
            additional_risks=[],
            notes=["Generated offline by the heuristic synthesizer (no LLM call was made)."],
        )


_SYNTHESIS_TOOL = {
    "name": "submit_migration_synthesis",
    "description": "Submit the synthesized migration architecture narrative and any additional risks.",
    "input_schema": {
        "type": "object",
        "properties": {
            "architecture_summary": {
                "type": "string",
                "description": "2-5 paragraph narrative describing the recommended migration architecture and approach.",
            },
            "additional_risks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string"},
                        "severity": {"type": "string", "enum": [s.value for s in RiskSeverity]},
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "affected_objects": {"type": "array", "items": {"type": "string"}},
                        "mitigation": {"type": "string"},
                    },
                    "required": ["category", "severity", "title", "description", "mitigation"],
                },
            },
            "notes": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["architecture_summary"],
    },
}

_SYSTEM_PROMPT = (
    "You are a staff data engineer specializing in database migrations. You are given a "
    "machine-generated summary of a source schema, its dependency graph, a proposed table/column "
    "mapping to a target schema, and risks already detected by rule-based analysis. "
    "Write a concise, concrete architecture narrative for how to execute this migration, and add any "
    "additional risks a senior engineer reviewing this plan would flag that the rule-based pass likely "
    "missed (judgment calls, business-logic implications, sequencing subtleties) - do not repeat risks "
    "already listed in the context. Always respond by calling the submit_migration_synthesis tool."
)


class AnthropicSynthesizer:
    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL):
        import anthropic  # imported lazily so the dependency stays optional

        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def synthesize(self, context: dict[str, Any]) -> LLMSynthesis:
        message = self._client.messages.create(
            model=self._model,
            max_tokens=2000,
            system=_SYSTEM_PROMPT,
            tools=[_SYNTHESIS_TOOL],
            tool_choice={"type": "tool", "name": "submit_migration_synthesis"},
            messages=[{"role": "user", "content": json.dumps(context, default=str)}],
        )

        for block in message.content:
            if getattr(block, "type", None) == "tool_use" and block.name == "submit_migration_synthesis":
                payload = dict(block.input)
                risks_in = payload.get("additional_risks") or []
                payload["additional_risks"] = [
                    Risk(
                        id=f"LLM{i + 1:03d}",
                        category=r.get("category", "other"),
                        severity=RiskSeverity(r.get("severity", "medium")),
                        title=r["title"],
                        description=r["description"],
                        affected_objects=r.get("affected_objects", []),
                        mitigation=r["mitigation"],
                    )
                    for i, r in enumerate(risks_in)
                ]
                return LLMSynthesis(**payload)

        raise RuntimeError("Model did not return a submit_migration_synthesis tool call.")


def get_synthesizer(*, offline: bool = False) -> Synthesizer:
    """Returns an Anthropic-backed synthesizer if usable, else the offline
    heuristic fallback. Never raises - any setup failure just downgrades
    to the fallback so the rest of the pipeline is unaffected.
    """
    if offline:
        return HeuristicSynthesizer()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return HeuristicSynthesizer()

    try:
        return AnthropicSynthesizer(api_key=api_key)
    except Exception:
        return HeuristicSynthesizer()


def synthesize_safely(synthesizer: Synthesizer, context: dict[str, Any]) -> LLMSynthesis:
    """Runs synthesis, falling back to the heuristic synthesizer if the
    (possibly network-backed) synthesizer raises for any reason.
    """
    try:
        return synthesizer.synthesize(context)
    except Exception as exc:
        fallback = HeuristicSynthesizer().synthesize(context)
        fallback.notes.append(f"LLM synthesis failed ({exc.__class__.__name__}: {exc}); used offline fallback.")
        return fallback
