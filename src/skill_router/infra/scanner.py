"""Skill scanner — discovers and parses SKILL.md from configurable roots.

Default roots: ``~/.claude/skills``, ``~/.claude/plugins`` (recursive), and
``./.claude/skills`` for the current project. Recursively walks each root and
parses every ``SKILL.md`` it finds, deduplicating by absolute path.

Scope mapping:
  - "plugin"  — path contains ``/.claude/plugins/``
  - "user"    — path is under ``~/.claude/skills/``
  - "project" — path contains ``/.claude/skills/`` but is NOT under home
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml

from skill_router.domain.types import ParsedSkill

logger = logging.getLogger(__name__)


def default_roots() -> list[Path]:
    """Return the list of default discovery roots that actually exist.

    Scope mapping for discovered paths:
      - ~/.claude/plugins/         -> "plugin"
      - ~/.claude/skills/          -> "user"
      - <project>/.claude/skills/  -> "project"  (auto-discovered from Claude registry)
      - SKILL_ROUTER_EXTRA_ROOTS   -> "project"  (colon-separated env var, isolated from Claude Code)
    """
    import os  # noqa: PLC0415
    user_skills = Path.home() / ".claude" / "skills"
    user_plugins = Path.home() / ".claude" / "plugins"
    candidates = [user_skills, user_plugins]
    candidates.extend(_discover_project_roots())

    extra_env = os.environ.get("SKILL_ROUTER_EXTRA_ROOTS", "")
    for raw in extra_env.split(":"):
        raw = raw.strip()
        if raw:
            candidates.append(Path(raw).expanduser())

    return [p for p in candidates if p.exists()]


def _decode_project_path(encoded: str) -> Path | None:
    """Decode a Claude project dir name to a filesystem path using greedy matching.

    e.g. '-home-catow-GIT-Hackathon' -> Path('/home/catow/GIT/Hackathon')
    Handles hyphens in directory names by checking filesystem existence at each step.
    """
    if not encoded.startswith('-'):
        return None
    parts = encoded[1:].split('-')
    current = Path('/')
    i = 0
    while i < len(parts):
        matched = False
        # Try longest match first (greedy) so 'decepticon-docs' beats 'decepticon' + 'docs'
        for j in range(len(parts), i, -1):
            candidate_name = '-'.join(parts[i:j])
            candidate = current / candidate_name
            if candidate.exists():
                current = candidate
                i = j
                matched = True
                break
        if not matched:
            # Remaining parts don't resolve to existing dirs — append as-is
            current = current / '-'.join(parts[i:])
            break
    return current


def _discover_project_roots() -> list[Path]:
    """Auto-discover project .claude/skills/ dirs from Claude Code's project registry."""
    registry = Path.home() / ".claude" / "projects"
    if not registry.exists():
        return []

    user_skills = Path.home() / ".claude" / "skills"
    found: list[Path] = []
    seen: set[Path] = set()

    for entry in sorted(registry.iterdir()):
        if not entry.is_dir():
            continue
        project_path = _decode_project_path(entry.name)
        if project_path is None:
            continue
        skills_dir = project_path / ".claude" / "skills"
        resolved = skills_dir.resolve() if skills_dir.exists() else skills_dir
        if skills_dir.exists() and resolved != user_skills.resolve() and resolved not in seen:
            seen.add(resolved)
            found.append(skills_dir)
            logger.debug("Discovered project skills: %s", skills_dir)

    logger.info("Auto-discovered %d project skill roots", len(found))
    return found


def _extra_project_roots() -> set[Path]:
    """Return resolved paths from SKILL_ROUTER_EXTRA_ROOTS env var."""
    import os  # noqa: PLC0415
    roots: set[Path] = set()
    for raw in os.environ.get("SKILL_ROUTER_EXTRA_ROOTS", "").split(":"):
        raw = raw.strip()
        if raw:
            p = Path(raw).expanduser().resolve()
            if p.exists():
                roots.add(p)
    return roots


def scope_for_path(path: Path, extra_project_roots: set[Path] | None = None) -> str:
    """Classify a SKILL.md path into 'project' | 'user' | 'plugin'."""
    # Use absolute (non-resolved) path so symlinked testbed dirs are classified by WHERE they live,
    # not where they ultimately resolve to.
    abs_str = str(path.absolute())
    resolved_str = str(path.resolve())
    home = str(Path.home().resolve())

    # Paths inside SKILL_ROUTER_EXTRA_ROOTS are always "project" — check unresolved path first
    if extra_project_roots:
        for root in extra_project_roots:
            root_s = str(root)
            if abs_str.startswith(root_s) or resolved_str.startswith(root_s):
                return "project"

    if "/.claude/plugins/" in resolved_str:
        return "plugin"
    if resolved_str.startswith(home + "/.claude/skills/") or resolved_str.startswith(home + "/.claude/skills"):
        return "user"
    if "/.claude/skills/" in resolved_str:
        return "project"
    return "user"


def _walk_skill_files(root: Path):
    """Yield all SKILL.md paths under root using os.walk (followlinks handles symlinked skill dirs)."""
    import os  # noqa: PLC0415
    for dirpath, _dirs, filenames in os.walk(root, followlinks=True):
        if "SKILL.md" in filenames:
            yield Path(dirpath) / "SKILL.md"


def scan_skills(roots: list[Path] | None = None) -> list[ParsedSkill]:
    """Recursively discover and parse all SKILL.md under the given roots.

    Parameters
    ----------
    roots:
        Directories to walk. ``None`` means use :func:`default_roots`.
    """
    if roots is None:
        roots = default_roots()

    extra_roots = _extra_project_roots()
    results: list[ParsedSkill] = []
    seen: set[Path] = set()

    for root in roots:
        if not root.exists():
            logger.warning("Root %s does not exist; skipping", root)
            continue
        for skill_path in _walk_skill_files(root):
            # Dedup by absolute path so symlinked testbed dirs aren't collapsed with their targets
            dedup_key = skill_path.absolute()
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            parsed = parse_skill_md(skill_path)
            if parsed is not None:
                parsed.scope = scope_for_path(skill_path, extra_roots)
                results.append(parsed)

    logger.info("Scanned %d SKILL.md files from %d roots", len(results), len(roots))
    return results


def scan_by_scope(roots: list[Path] | None = None) -> dict[str, list[ParsedSkill]]:
    """Scan skills and return them bucketed by scope.

    Returns a dict with keys "project", "user", "plugin", each holding a
    (possibly empty) list of ParsedSkill objects.
    """
    all_skills = scan_skills(roots)
    buckets: dict[str, list[ParsedSkill]] = {"project": [], "user": [], "plugin": []}
    for skill in all_skills:
        bucket = skill.scope if skill.scope in buckets else "user"
        buckets[bucket].append(skill)
    return buckets


def parse_skill_md(path: Path) -> ParsedSkill | None:
    """Parse a single SKILL.md.

    Returns ``None`` only if the file is completely unreadable. Malformed YAML,
    missing fields, etc. are handled gracefully via warnings.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not read %s: %s", path, exc)
        return None
    except UnicodeDecodeError as exc:
        logger.warning("Encoding error in %s: %s", path, exc)
        return None

    frontmatter: dict = {}
    body: str = raw
    warnings: list[str] = []

    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            yaml_text = parts[1]
            body = parts[2].lstrip("\n")
            try:
                parsed_yaml = yaml.safe_load(yaml_text)
                if isinstance(parsed_yaml, dict):
                    frontmatter = parsed_yaml
                else:
                    warnings.append("YAML did not parse to a mapping")
                    frontmatter = _regex_frontmatter(yaml_text)
            except yaml.YAMLError as exc:
                warnings.append(f"YAML parse error: {exc}")
                frontmatter = _regex_frontmatter(yaml_text)
        else:
            warnings.append("Malformed front-matter: missing closing ---")

    # Resolve name (frontmatter -> parent directory fallback)
    raw_name = frontmatter.get("name")
    if raw_name:
        name = str(raw_name).strip()
    else:
        name = path.parent.name.lower().replace("_", "-")
        warnings.append("name missing; derived from parent directory")

    # Resolve description (frontmatter -> first body paragraph fallback)
    raw_description = frontmatter.get("description")
    if raw_description:
        description = " ".join(str(raw_description).split())
    else:
        first_para = _first_paragraph(body)
        if first_para:
            description = first_para
            warnings.append("description missing; derived from first body paragraph")
        else:
            description = ""

    return ParsedSkill(
        name=name,
        description=description,
        body=body,
        source_path=path,
        raw_frontmatter=frontmatter,
        warnings=warnings,
    )


def _regex_frontmatter(yaml_text: str) -> dict:
    """Best-effort ``key: value`` extraction when YAML parsing fails."""
    out: dict = {}
    for line in yaml_text.splitlines():
        m = re.match(r"^([A-Za-z][A-Za-z0-9_-]*)\s*:\s*(.+?)\s*$", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def _first_paragraph(body: str) -> str:
    """Return the first non-empty, non-heading markdown paragraph (truncated)."""
    for chunk in re.split(r"\n\s*\n", body.strip()):
        chunk = chunk.strip()
        if chunk and not chunk.startswith("#"):
            return chunk[:500]
    return ""
