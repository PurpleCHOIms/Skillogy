"""Tests for skill_router.infra.scanner and skill_router.domain.types."""

from pathlib import Path

import pytest

from skill_router.infra.scanner import default_roots, parse_skill_md, scan_skills
from skill_router.domain.types import ParsedSkill


# ---------------------------------------------------------------------------
# test_parse_normal
# ---------------------------------------------------------------------------

def test_parse_normal(tmp_path: Path) -> None:
    """Parse a well-formed SKILL.md and verify all fields."""
    skill_dir = tmp_path / "my-tool"
    skill_dir.mkdir()
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        "---\n"
        "name: my-tool\n"
        "description: Does something useful.\n"
        "version: 1.0.0\n"
        "---\n"
        "## Usage\n\n"
        "Run this tool to do stuff.\n",
        encoding="utf-8",
    )

    result = parse_skill_md(skill_file)

    assert result is not None
    assert isinstance(result, ParsedSkill)
    assert result.name == "my-tool"
    assert result.description == "Does something useful."
    assert "## Usage" in result.body or "Run this tool" in result.body
    assert result.source_path == skill_file
    assert result.raw_frontmatter["version"] == "1.0.0"
    assert result.warnings == []


# ---------------------------------------------------------------------------
# test_parse_malformed_yaml
# ---------------------------------------------------------------------------

def test_parse_malformed_yaml(tmp_path: Path) -> None:
    """SKILL.md with broken YAML falls back to regex extraction."""
    skill_dir = tmp_path / "broken-skill"
    skill_dir.mkdir()
    skill_file = skill_dir / "SKILL.md"
    # Deliberately malformed YAML (unbalanced brackets)
    skill_file.write_text(
        "---\n"
        "name: broken-skill\n"
        "description: [unclosed bracket\n"
        "version: 2.0\n"
        "---\n"
        "Body text here.\n",
        encoding="utf-8",
    )

    result = parse_skill_md(skill_file)

    assert result is not None
    # Regex fallback should recover name
    assert result.name == "broken-skill"
    # Warnings must be non-empty (YAML error recorded)
    assert len(result.warnings) > 0
    assert any("YAML" in w or "yaml" in w.lower() for w in result.warnings)


# ---------------------------------------------------------------------------
# test_parse_missing_description
# ---------------------------------------------------------------------------

def test_parse_missing_description(tmp_path: Path) -> None:
    """When description is absent, derive it from the first body paragraph."""
    skill_dir = tmp_path / "nodesc-skill"
    skill_dir.mkdir()
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        "---\n"
        "name: nodesc-skill\n"
        "---\n"
        "\n"
        "This is the first real paragraph of the body.\n"
        "\n"
        "Second paragraph here.\n",
        encoding="utf-8",
    )

    result = parse_skill_md(skill_file)

    assert result is not None
    assert result.name == "nodesc-skill"
    assert "first real paragraph" in result.description
    assert len(result.warnings) > 0
    assert any("description" in w.lower() for w in result.warnings)


# ---------------------------------------------------------------------------
# test_parse_missing_name
# ---------------------------------------------------------------------------

def test_parse_missing_name(tmp_path: Path) -> None:
    """When name is absent, derive it from the parent directory name."""
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        "---\n"
        "description: A skill without a name.\n"
        "---\n"
        "Body content.\n",
        encoding="utf-8",
    )

    result = parse_skill_md(skill_file)

    assert result is not None
    assert result.name == "my-skill"
    assert len(result.warnings) > 0
    assert any("name" in w.lower() for w in result.warnings)


# ---------------------------------------------------------------------------
# test_scan_real_local
# ---------------------------------------------------------------------------

def test_scan_real_local() -> None:
    """Scan default_roots() and assert at least 100 SKILL.md files found."""
    roots = default_roots()
    if not roots:
        pytest.skip("No ~/.claude roots found; skipping real scan test.")

    results = scan_skills(roots)

    if len(results) == 0:
        pytest.skip("No SKILL.md files found in default roots; skipping.")

    assert len(results) >= 100, (
        f"Expected at least 100 skills, found {len(results)}. "
        f"Roots searched: {roots}"
    )
    # Spot-check structure
    for skill in results[:5]:
        assert isinstance(skill, ParsedSkill)
        assert skill.name, f"Empty name for {skill.source_path}"
        assert skill.source_path.exists()
