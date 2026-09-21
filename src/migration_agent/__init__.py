"""Autonomous Data Migration Planning Agent.

Analyzes a source database's schema, dependencies and data quality, plus a
target schema (existing database or declarative spec), to produce a
migration architecture, column mappings, risk assessment, validation plan
and phased execution plan.
"""
from .models import MigrationPlan
from .planner import PlannerInputs, build_plan

__all__ = ["MigrationPlan", "PlannerInputs", "build_plan"]

__version__ = "0.1.0"
