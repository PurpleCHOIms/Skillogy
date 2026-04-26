"""Shared dataclasses for skill_router."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ParsedSkill:
    name: str
    description: str
    body: str
    source_path: Path
    raw_frontmatter: dict
    scope: str = "user"   # "project" | "user" | "plugin"
    warnings: list[str] = field(default_factory=list)


@dataclass
class Signal:
    kind: str   # keyword | file_ext | tool_name | error_pattern | pattern
    value: str


@dataclass
class TriggerSurface:
    """The trigger surface of a single skill — what user inputs should activate it."""
    skill_name: str
    intents: list[str]                     # user-goal phrases
    signals: list[Signal]                  # concrete trigger signals (positive)
    exclusions: list[Signal]               # signals that should PREVENT activation
    related_skills: list[str] = field(default_factory=list)
    extraction_cost_usd: float = 0.0
    extraction_warnings: list[str] = field(default_factory=list)
