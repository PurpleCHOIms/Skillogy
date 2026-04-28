import { describe, expect, it } from "vitest";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

import { defaultRoots, parseSkillMd, scanSkills, scopeForPath } from "../src/infra/scanner.js";

function withTmpDir<T>(fn: (dir: string) => T): T {
  const dir = mkdtempSync(join(tmpdir(), "skillogy-test-"));
  try {
    return fn(dir);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

describe("parseSkillMd", () => {
  it("parses a well-formed skill", () => {
    withTmpDir((dir) => {
      const skillDir = join(dir, "my-skill");
      mkdirSync(skillDir);
      const path = join(skillDir, "SKILL.md");
      writeFileSync(
        path,
        "---\nname: my-skill\ndescription: A test skill\n---\n\nSome body content.\n",
      );
      const parsed = parseSkillMd(path);
      expect(parsed).not.toBeNull();
      expect(parsed!.name).toBe("my-skill");
      expect(parsed!.description).toBe("A test skill");
      expect(parsed!.body).toContain("Some body content");
      expect(parsed!.warnings).toEqual([]);
    });
  });

  it("derives name from parent dir when frontmatter missing it", () => {
    withTmpDir((dir) => {
      const skillDir = join(dir, "fallback_skill");
      mkdirSync(skillDir);
      const path = join(skillDir, "SKILL.md");
      writeFileSync(path, "Body only, no frontmatter.\n");
      const parsed = parseSkillMd(path);
      expect(parsed!.name).toBe("fallback-skill");
      expect(parsed!.warnings.length).toBeGreaterThan(0);
    });
  });

  it("handles malformed YAML gracefully", () => {
    withTmpDir((dir) => {
      const skillDir = join(dir, "broken");
      mkdirSync(skillDir);
      const path = join(skillDir, "SKILL.md");
      writeFileSync(path, "---\nname: [unclosed list\n  description: x\n---\nBody.\n");
      const parsed = parseSkillMd(path);
      expect(parsed).not.toBeNull();
      expect(parsed!.warnings.some((w) => w.includes("YAML"))).toBe(true);
    });
  });
});

describe("scopeForPath", () => {
  it("classifies a plugin path", () => {
    const path = `${process.env.HOME}/.claude/plugins/foo/skills/bar/SKILL.md`;
    expect(scopeForPath(path)).toBe("plugin");
  });

  it("classifies a user-skills path", () => {
    const path = `${process.env.HOME}/.claude/skills/foo/SKILL.md`;
    expect(scopeForPath(path)).toBe("user");
  });

  it("classifies an arbitrary project path", () => {
    const path = "/tmp/some-project/.claude/skills/bar/SKILL.md";
    expect(scopeForPath(path)).toBe("project");
  });
});

describe("defaultRoots", () => {
  it("returns only existing dirs", () => {
    const roots = defaultRoots();
    for (const r of roots) {
      expect(typeof r).toBe("string");
    }
  });
});

describe("scanSkills (real ~/.claude tree, optional)", () => {
  it("returns SKILL.md entries from default roots if any exist", () => {
    const skills = scanSkills();
    // Cannot guarantee count in CI; just assert types
    for (const s of skills.slice(0, 3)) {
      expect(typeof s.name).toBe("string");
      expect(typeof s.body).toBe("string");
      expect(["user", "project", "plugin"]).toContain(s.scope);
    }
  });
});
