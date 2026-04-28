import type { LLMClient } from "../infra/llm.js";
import type { ParsedSkill, Signal, SignalKind, TriggerSurface } from "../domain/types.js";
import { VALID_SIGNAL_KINDS } from "../domain/types.js";

const LARGE_BODY_THRESHOLD = 100_000;

const SYSTEM_PROMPT = `\
You are a TRIGGER SURFACE ANALYST for Claude Code skills. Your job is to deeply
reason about a skill's content and infer the FULL RANGE of user situations that
should activate it — going beyond what the skill explicitly states.

## Your reasoning process (do this internally before outputting JSON):
1. UNDERSTAND the skill's expertise domain: what knowledge, patterns, or workflows does it encode?
2. INFER user goals: what tasks, problems, or decisions would a user be working on when this skill becomes useful? Think from the USER's perspective, not the skill's perspective.
3. DIVERSIFY intents: cover different user experience levels, phrasings, and angles. A senior engineer and a beginner would describe the same need differently.
4. SCAN the full body for technical signals: tool names, file extensions, error code patterns, framework names, API names, specific keywords that appear in real user messages.
5. IDENTIFY exclusions: only when the body explicitly says "do NOT use for X" or when using the skill for a clearly wrong context would mislead the user.
6. FIND related skills: skill names referenced in backticks, "see also", "use X first/after" patterns, or complementary skills implied by the domain.

## Output STRICT JSON with no preamble or markdown fences:
{
  "intents": [
    "build a rag pipeline with chroma",
    "set up document retrieval for my chatbot",
    "chunk and embed pdfs for search",
    "implement semantic search over documents",
    "connect langchain to a vector database"
  ],
  "signals": [
    {"kind": "keyword",       "value": "rag"},
    {"kind": "keyword",       "value": "retrieval"},
    {"kind": "keyword",       "value": "chroma"},
    {"kind": "keyword",       "value": "faiss"},
    {"kind": "keyword",       "value": "embeddings"},
    {"kind": "keyword",       "value": "vector store"},
    {"kind": "tool_name",     "value": "chromadb"},
    {"kind": "keyword",       "value": "document loader"},
    {"kind": "keyword",       "value": "text splitter"}
  ],
  "exclusions": [],
  "related_skills": ["langchain-fundamentals", "langsmith-trace"]
}

## Intent rules:
- 5–15 phrases. Short, lowercase, conversational. Start with action verbs (build, set up, implement, debug, understand, migrate, configure, use, connect, create).
- Cover MULTIPLE angles: what the user wants to BUILD, what PROBLEM they have, what CONCEPT they want to understand.
- Do NOT copy "when to use" text verbatim — rephrase into natural user language.
- Do NOT include the skill name itself as an intent.

## Signal rules:
- Extract from the FULL BODY: framework names, API names, CLI commands, library imports, error code patterns, config file names, file extensions.
- kind ∈ {keyword, file_ext, tool_name, error_pattern, pattern}
- Values lowercase. Include both short forms and full forms (e.g., "rag" AND "retrieval-augmented generation").
- tool_name: only for actual executable tool/CLI names.
- file_ext: must start with dot (e.g., ".ts", ".py").
- error_pattern: regex fragment that matches real error strings.

## Exclusion rules:
- Only add when there's a clear boundary in the skill body ("do not use for X", "this is NOT for Y").
- Most skills have zero exclusions.

## Related skills rules:
- Look for backtick \`skill-name\` references, "see also", "use X first/after", or complementary skills implied by the domain.
- Use exact skill names as they appear (lowercase with hyphens).
- Max 5 related skills. Empty array if none.
`;

function buildPrompt(
  name: string,
  description: string,
  body: string,
  frontmatter: Record<string, unknown> | null,
): string {
  const parts = [`Skill name: ${name}`, `Skill description: ${description}`];
  if (frontmatter) {
    for (const key of ["trigger", "when_to_use", "use_when", "tags", "category"]) {
      if (key in frontmatter) {
        parts.push(`Frontmatter ${key}: ${JSON.stringify(frontmatter[key])}`);
      }
    }
  }
  parts.push(`\nSkill body:\n${body}`);
  return parts.join("\n");
}

function parseLlmResponse(response: string): unknown {
  let text = response.trim();
  if (text.startsWith("```")) {
    text = text.replace(/^```[a-z]*\n?/i, "");
    text = text.replace(/\n?```$/, "");
    text = text.trim();
  }
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function parseIntents(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const item of raw) {
    const v = String(item).trim().toLowerCase();
    if (v && !seen.has(v)) {
      seen.add(v);
      out.push(v);
    }
  }
  return out;
}

function parseSignals(raw: unknown): Signal[] {
  if (!Array.isArray(raw)) return [];
  const out: Signal[] = [];
  const seen = new Set<string>();
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const obj = item as Record<string, unknown>;
    const kindStr = String(obj.kind ?? "").trim().toLowerCase();
    if (!VALID_SIGNAL_KINDS.has(kindStr as SignalKind)) continue;
    const value = String(obj.value ?? "").trim().toLowerCase();
    if (!value) continue;
    const key = `${kindStr}::${value}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({ kind: kindStr as SignalKind, value });
  }
  return out;
}

function parseRelatedSkills(raw: unknown, skillName: string): string[] {
  if (!Array.isArray(raw)) return [];
  const seen = new Set<string>();
  const out: string[] = [];
  const lowerName = skillName.toLowerCase();
  for (const item of raw) {
    const v = String(item).trim().toLowerCase();
    if (!v || v === lowerName || seen.has(v)) continue;
    seen.add(v);
    out.push(v);
  }
  return out;
}

function emptySurface(parsed: ParsedSkill, warnings: string[]): TriggerSurface {
  return {
    skillName: parsed.name,
    intents: [],
    signals: [],
    exclusions: [],
    relatedSkills: [],
    extractionCostUsd: 0.0,
    extractionWarnings: warnings,
  };
}

export async function extract(parsed: ParsedSkill, llm: LLMClient): Promise<TriggerSurface> {
  const warnings: string[] = [];
  if (parsed.body.length > LARGE_BODY_THRESHOLD) {
    warnings.push(
      `Body is ${parsed.body.length} chars (> ${LARGE_BODY_THRESHOLD}); passing full body to LLM`,
    );
  }

  const prompt = buildPrompt(
    parsed.name,
    parsed.description,
    parsed.body,
    parsed.rawFrontmatter ?? null,
  );

  let response: string;
  try {
    response = await llm.complete({
      prompt,
      system: SYSTEM_PROMPT,
      maxTokens: 8192,
      temperature: 0.0,
    });
  } catch (exc) {
    warnings.push(`LLM call failed: ${(exc as Error).message}`);
    return emptySurface(parsed, warnings);
  }

  const parsedData = parseLlmResponse(response);
  if (parsedData === null || typeof parsedData !== "object") {
    warnings.push(`Failed to parse JSON from LLM response: ${response.slice(0, 200)}`);
    return emptySurface(parsed, warnings);
  }

  const data = parsedData as Record<string, unknown>;
  return {
    skillName: parsed.name,
    intents: parseIntents(data.intents),
    signals: parseSignals(data.signals),
    exclusions: parseSignals(data.exclusions),
    relatedSkills: parseRelatedSkills(data.related_skills, parsed.name),
    extractionCostUsd: 0.0,
    extractionWarnings: warnings,
  };
}
