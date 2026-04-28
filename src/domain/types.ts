export type SkillScope = "user" | "project" | "plugin";

export type SignalKind =
  | "keyword"
  | "tool_name"
  | "file_ext"
  | "error_pattern"
  | "pattern";

export const VALID_SIGNAL_KINDS: ReadonlySet<SignalKind> = new Set([
  "keyword",
  "tool_name",
  "file_ext",
  "error_pattern",
  "pattern",
]);

export interface Signal {
  kind: SignalKind;
  value: string;
}

export interface ParsedSkill {
  name: string;
  description: string;
  body: string;
  sourcePath: string;
  rawFrontmatter: Record<string, unknown>;
  scope: SkillScope;
  warnings: string[];
}

export interface TriggerSurface {
  skillName: string;
  intents: string[];
  signals: Signal[];
  exclusions: Signal[];
  relatedSkills: string[];
  extractionCostUsd: number;
  extractionWarnings: string[];
}

export interface RoutingAlternative {
  name: string;
  score?: number;
  description?: string;
  scope?: SkillScope;
  via?: "relates_to";
}

export interface RoutingResult {
  skillName: string;
  skillBody: string;
  reasoningPath: Array<[string, string, string]>;
  alternatives: RoutingAlternative[];
  score: number;
}
