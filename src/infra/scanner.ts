import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { resolve as resolvePath, sep, join, basename, dirname } from "node:path";

import { parse as parseYaml } from "yaml";

import type { ParsedSkill, SkillScope } from "../domain/types.js";

const HOME = homedir();
const HOME_RESOLVED = resolvePath(HOME);

function expanduser(p: string): string {
  if (p === "~") return HOME;
  if (p.startsWith("~/")) return join(HOME, p.slice(2));
  return p;
}

function safeStatExists(p: string): boolean {
  try {
    return existsSync(p);
  } catch {
    return false;
  }
}

function isDir(p: string): boolean {
  try {
    return statSync(p).isDirectory();
  } catch {
    return false;
  }
}

function decodeProjectPath(encoded: string): string | null {
  if (!encoded.startsWith("-")) return null;
  const parts = encoded.slice(1).split("-");
  let current = "/";
  let i = 0;
  while (i < parts.length) {
    let matched = false;
    // Greedy longest match first so 'decepticon-docs' beats 'decepticon' + 'docs'
    for (let j = parts.length; j > i; j--) {
      const candidateName = parts.slice(i, j).join("-");
      const candidate = join(current, candidateName);
      if (safeStatExists(candidate)) {
        current = candidate;
        i = j;
        matched = true;
        break;
      }
    }
    if (!matched) {
      // Remaining parts don't resolve; append as-is
      current = join(current, parts.slice(i).join("-"));
      break;
    }
  }
  return current;
}

function discoverProjectRoots(): string[] {
  const registry = join(HOME, ".claude", "projects");
  if (!safeStatExists(registry)) return [];

  const userSkills = resolvePath(join(HOME, ".claude", "skills"));
  const found: string[] = [];
  const seen = new Set<string>();

  let entries: string[];
  try {
    entries = readdirSync(registry).sort();
  } catch {
    return [];
  }

  for (const entry of entries) {
    const fullPath = join(registry, entry);
    if (!isDir(fullPath)) continue;
    const projectPath = decodeProjectPath(entry);
    if (projectPath === null) continue;
    const skillsDir = join(projectPath, ".claude", "skills");
    if (!safeStatExists(skillsDir)) continue;
    const resolved = resolvePath(skillsDir);
    if (resolved === userSkills) continue;
    if (seen.has(resolved)) continue;
    seen.add(resolved);
    found.push(skillsDir);
  }

  return found;
}

function extraProjectRoots(): Set<string> {
  const roots = new Set<string>();
  const raw = process.env.SKILLOGY_EXTRA_ROOTS ?? "";
  for (const part of raw.split(":")) {
    const trimmed = part.trim();
    if (!trimmed) continue;
    const expanded = resolvePath(expanduser(trimmed));
    if (safeStatExists(expanded)) roots.add(expanded);
  }
  return roots;
}

export function defaultRoots(): string[] {
  const userSkills = join(HOME, ".claude", "skills");
  const userPlugins = join(HOME, ".claude", "plugins");
  const candidates: string[] = [userSkills, userPlugins, ...discoverProjectRoots()];

  const raw = process.env.SKILLOGY_EXTRA_ROOTS ?? "";
  for (const part of raw.split(":")) {
    const trimmed = part.trim();
    if (trimmed) candidates.push(expanduser(trimmed));
  }

  return candidates.filter((p) => safeStatExists(p));
}

export function scopeForPath(path: string, extraRoots?: Set<string>): SkillScope {
  const abs = resolvePath(path);
  const resolved = resolvePath(path);
  const home = HOME_RESOLVED;

  if (extraRoots) {
    for (const root of extraRoots) {
      if (abs.startsWith(root) || resolved.startsWith(root)) return "project";
    }
  }

  if (resolved.includes(`${sep}.claude${sep}plugins${sep}`)) return "plugin";

  const userSkillsPrefix = home + `${sep}.claude${sep}skills`;
  if (resolved.startsWith(userSkillsPrefix + sep) || resolved === userSkillsPrefix) return "user";

  if (resolved.includes(`${sep}.claude${sep}skills${sep}`)) return "project";

  return "user";
}

function* walkSkillFiles(root: string): Generator<string> {
  let stack: string[] = [root];
  const seen = new Set<string>();
  while (stack.length) {
    const dir = stack.pop()!;
    let realDir: string;
    try {
      realDir = resolvePath(dir);
    } catch {
      continue;
    }
    if (seen.has(realDir)) continue;
    seen.add(realDir);

    let entries: string[];
    try {
      entries = readdirSync(dir);
    } catch {
      continue;
    }

    for (const name of entries) {
      const full = join(dir, name);
      let st;
      try {
        st = statSync(full);
      } catch {
        continue;
      }
      if (st.isDirectory()) {
        stack.push(full);
      } else if (name === "SKILL.md") {
        yield full;
      }
    }
  }
}

export function scanSkills(roots?: string[]): ParsedSkill[] {
  const useRoots = roots ?? defaultRoots();
  const extras = extraProjectRoots();
  const results: ParsedSkill[] = [];
  const seen = new Set<string>();

  for (const root of useRoots) {
    if (!safeStatExists(root)) continue;
    for (const skillPath of walkSkillFiles(root)) {
      const dedupKey = resolvePath(skillPath);
      if (seen.has(dedupKey)) continue;
      seen.add(dedupKey);
      const parsed = parseSkillMd(skillPath);
      if (parsed) {
        parsed.scope = scopeForPath(skillPath, extras);
        results.push(parsed);
      }
    }
  }

  return results;
}

export function scanByScope(roots?: string[]): Record<SkillScope, ParsedSkill[]> {
  const buckets: Record<SkillScope, ParsedSkill[]> = { project: [], user: [], plugin: [] };
  for (const skill of scanSkills(roots)) {
    const bucket = (["project", "user", "plugin"] as const).includes(skill.scope)
      ? skill.scope
      : "user";
    buckets[bucket].push(skill);
  }
  return buckets;
}

function regexFrontmatter(yamlText: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of yamlText.split(/\r?\n/)) {
    const m = /^([A-Za-z][A-Za-z0-9_-]*)\s*:\s*(.+?)\s*$/.exec(line);
    if (m) out[m[1]!] = m[2]!;
  }
  return out;
}

function firstParagraph(body: string): string {
  for (const chunk of body.trim().split(/\n\s*\n/)) {
    const trimmed = chunk.trim();
    if (trimmed && !trimmed.startsWith("#")) {
      return trimmed.slice(0, 500);
    }
  }
  return "";
}

export function parseSkillMd(path: string): ParsedSkill | null {
  let raw: string;
  try {
    raw = readFileSync(path, "utf-8");
  } catch {
    return null;
  }

  let frontmatter: Record<string, unknown> = {};
  let body = raw;
  const warnings: string[] = [];

  if (raw.startsWith("---")) {
    // Split into [pre, yaml, body] using the second '---'
    const parts = raw.split(/^---\s*$/m);
    // raw.split with ---\s*$/m on raw='---\nyaml\n---\nbody' produces ['', '\nyaml\n', '\nbody']
    if (parts.length >= 3) {
      const yamlText = parts[1] ?? "";
      body = (parts.slice(2).join("---")).replace(/^\n/, "");
      try {
        const parsedYaml = parseYaml(yamlText);
        if (parsedYaml && typeof parsedYaml === "object" && !Array.isArray(parsedYaml)) {
          frontmatter = parsedYaml as Record<string, unknown>;
        } else {
          warnings.push("YAML did not parse to a mapping");
          frontmatter = regexFrontmatter(yamlText);
        }
      } catch (exc) {
        warnings.push(`YAML parse error: ${(exc as Error).message}`);
        frontmatter = regexFrontmatter(yamlText);
      }
    } else {
      warnings.push("Malformed front-matter: missing closing ---");
    }
  }

  const rawName = frontmatter["name"];
  const name = typeof rawName === "string" && rawName.trim()
    ? rawName.trim()
    : (warnings.push("name missing; derived from parent directory"),
        basename(dirname(path)).toLowerCase().replace(/_/g, "-"));

  const rawDescription = frontmatter["description"];
  let description = "";
  if (typeof rawDescription === "string" && rawDescription.trim()) {
    description = rawDescription.split(/\s+/).filter(Boolean).join(" ");
  } else {
    const firstPara = firstParagraph(body);
    if (firstPara) {
      description = firstPara;
      warnings.push("description missing; derived from first body paragraph");
    }
  }

  return {
    name,
    description,
    body,
    sourcePath: path,
    rawFrontmatter: frontmatter,
    scope: "user",
    warnings,
  };
}
