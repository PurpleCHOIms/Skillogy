import { readFileSync } from "node:fs";

import neo4j, { type Driver } from "neo4j-driver";

import { getDriver } from "../infra/db.js";
import { getLlmClient, type LLMClient } from "../infra/llm.js";
import type { RoutingAlternative, RoutingResult, Signal } from "../domain/types.js";

const QUERY_EXTRACTION_SYSTEM = `\
You are parsing a developer's message to extract trigger signals for routing.
Output STRICT JSON: {"intents": [...], "signals": [{"kind": ..., "value": ...}, ...]}
- intents: lowercase short user-goal phrases derived from the message.
- signals: concrete tokens — keywords, tool names, file extensions, error codes — that may match a skill's trigger surface. kind ∈ {keyword, file_ext, tool_name, error_pattern, pattern}.
Return ONLY the JSON object.`;

const JUDGE_SYSTEM = `\
You will receive a developer's request and a shortlist of candidate Claude Code skills with
their descriptions and graph-derived scores. Pick the SINGLE best skill for this request.
Output STRICT JSON: {"winner": "<skill_name>", "reason": "<one short sentence>"}
Return ONLY the JSON object, no preamble.`;

const RELATED_NEIGHBORS_CYPHER = `
MATCH (s:Skill {name: $name})-[:RELATES_TO]->(t:Skill)
WHERE NOT t.name IN $exclude
RETURN t.name AS name, t.description AS description, t.scope AS scope
LIMIT $k
`;

const COLLECT_SCORE_CYPHER = `\
MATCH (s:Skill)-[:TRIGGERED_BY]->(n)
WHERE (n:Intent AND n.label IN $intents)
   OR (n:Signal AND [n.kind, n.value] IN $signal_pairs)
WITH s,
     sum(CASE WHEN n:Intent THEN 2.0 ELSE 1.0 END) AS score,
     collect(DISTINCT {kind: head(labels(n)), id: coalesce(n.label, n.value)}) AS hits
WHERE NOT EXISTS {
  MATCH (s)-[:EXCLUDED_BY]->(e:Signal)
  WHERE [e.kind, e.value] IN $signal_pairs
}
RETURN s.name AS name,
       s.description AS description,
       s.source_path AS source_path,
       s.scope AS scope,
       score,
       hits
ORDER BY score DESC
LIMIT $top_k
`;

interface CandidateRow {
  name: string;
  description: string;
  source_path: string;
  scope: string;
  score: number;
  hits: Array<{ kind: string; id: string }>;
}

export interface FindSkillOptions {
  query: string;
  topK?: number;
  judge?: boolean;
  extract?: boolean;
  loadBody?: boolean;
}

export class Router {
  constructor(
    private readonly driver: Driver = getDriver(),
    private readonly llm?: LLMClient,
  ) {}

  private async getLlm(): Promise<LLMClient> {
    return this.llm ?? (await getLlmClient());
  }

  async findSkill(opts: FindSkillOptions): Promise<RoutingResult> {
    const topK = opts.topK ?? 5;
    const judge = opts.judge ?? true;
    const extract = opts.extract ?? true;
    const loadBody = opts.loadBody ?? true;
    const query = opts.query;

    let intents: string[];
    let signals: Signal[];
    if (extract) {
      ({ intents, signals } = await this.extractQueryNodes(query));
    } else {
      const kws = query
        .split(/\s+/)
        .filter((w) => w.length > 2)
        .map((w) => w.toLowerCase());
      intents = [];
      signals = kws.slice(0, 10).map((k) => ({ kind: "keyword", value: k }));
    }

    const rows = await this.collectAndScore(intents, signals, topK);

    if (rows.length === 0) {
      return {
        skillName: "",
        skillBody: "",
        reasoningPath: [],
        alternatives: [],
        score: 0.0,
      };
    }

    let winnerName: string;
    if (judge && rows.length > 1) {
      winnerName = await this.llmJudge(query, rows);
    } else {
      winnerName = rows[0]!.name;
    }

    const winnerRow = rows.find((r) => r.name === winnerName) ?? rows[0]!;
    const winnerScore = winnerRow.score;
    const reasoningPath = this.reasoningPathFromHits(winnerName, winnerRow.hits);

    let skillBody = "";
    if (loadBody) {
      const sourcePath = winnerRow.source_path ?? "";
      if (sourcePath) {
        try {
          skillBody = readFileSync(sourcePath, "utf-8");
        } catch {
          // swallow — body stays empty
        }
      }
    }

    let alternatives: RoutingAlternative[] = rows
      .filter((r) => r.name !== winnerName)
      .map((r) => ({ name: r.name, score: r.score }));

    const relatesK = parseInt(process.env.SKILLOGY_RELATES_K ?? "3", 10);
    const exclude = new Set<string>([winnerName, ...alternatives.map((a) => a.name)]);
    const related = await this.fetchRelated(winnerName, exclude, relatesK);
    alternatives = [...alternatives, ...related];

    return {
      skillName: winnerName,
      skillBody,
      reasoningPath,
      alternatives,
      score: winnerScore,
    };
  }

  private async extractQueryNodes(
    query: string,
  ): Promise<{ intents: string[]; signals: Signal[] }> {
    try {
      const llm = await this.getLlm();
      const raw = await llm.complete({
        prompt: query,
        system: QUERY_EXTRACTION_SYSTEM,
        maxTokens: 2048,
        temperature: 0.0,
      });
      const data = JSON.parse(raw.trim()) as {
        intents?: unknown[];
        signals?: Array<{ kind?: string; value?: string }>;
      };
      const intents = Array.isArray(data.intents)
        ? data.intents.map((i) => String(i).toLowerCase())
        : [];
      const signals: Signal[] = [];
      if (Array.isArray(data.signals)) {
        for (const s of data.signals) {
          if (s && typeof s === "object" && "kind" in s && "value" in s) {
            signals.push({
              kind: String(s.kind).toLowerCase() as Signal["kind"],
              value: String(s.value),
            });
          }
        }
      }
      return { intents, signals };
    } catch {
      const kws = query
        .split(/\s+/)
        .filter((w) => w.length > 2)
        .map((w) => w.toLowerCase());
      return {
        intents: [],
        signals: kws.slice(0, 10).map((k) => ({ kind: "keyword", value: k })),
      };
    }
  }

  private async collectAndScore(
    intents: string[],
    signals: Signal[],
    topK: number,
  ): Promise<CandidateRow[]> {
    const signalPairs: Array<[string, string]> = signals.map((s) => [s.kind, s.value]);
    const { records } = await this.driver.executeQuery(
      COLLECT_SCORE_CYPHER,
      { intents, signal_pairs: signalPairs, top_k: neo4j.int(topK) },
      { routing: neo4j.routing.READ },
    );
    const rows: CandidateRow[] = [];
    for (const record of records) {
      const score = record.get("score");
      const hits = record.get("hits") as Array<{ kind: string; id: string }>;
      rows.push({
        name: String(record.get("name")),
        description: String(record.get("description") ?? ""),
        source_path: String(record.get("source_path") ?? ""),
        scope: String(record.get("scope") ?? "user"),
        score: typeof score === "number" ? score : Number(score?.toNumber?.() ?? score),
        hits,
      });
    }
    return rows;
  }

  private reasoningPathFromHits(
    skillName: string,
    hits: Array<{ kind: string; id: string }>,
  ): Array<[string, string, string]> {
    const path: Array<[string, string, string]> = [];
    for (const hit of hits) {
      if (hit.id) {
        path.push([skillName, "triggered_by", hit.id]);
      }
    }
    return path;
  }

  private async fetchRelated(
    topSkillName: string,
    exclude: Set<string>,
    k: number,
  ): Promise<RoutingAlternative[]> {
    if (!topSkillName) return [];
    const { records } = await this.driver.executeQuery(
      RELATED_NEIGHBORS_CYPHER,
      { name: topSkillName, exclude: Array.from(exclude), k: neo4j.int(k) },
      { routing: neo4j.routing.READ },
    );
    const out: RoutingAlternative[] = [];
    for (const r of records) {
      out.push({
        name: String(r.get("name")),
        description: String(r.get("description") ?? ""),
        scope: String(r.get("scope") ?? "user") as RoutingAlternative["scope"],
        via: "relates_to",
      });
    }
    return out;
  }

  private async llmJudge(query: string, rows: CandidateRow[]): Promise<string> {
    const candidatesText = rows
      .map((r) => `- ${r.name} (score=${r.score.toFixed(2)}): ${(r.description ?? "").slice(0, 300)}`)
      .join("\n");
    const prompt = `User request: ${query}\n\nCandidates:\n${candidatesText}\n\nPick the best one.`;
    try {
      const llm = await this.getLlm();
      const raw = await llm.complete({
        prompt,
        system: JUDGE_SYSTEM,
        maxTokens: 200,
        temperature: 0.0,
      });
      const data = JSON.parse(raw.trim()) as { winner?: string };
      const winner = data.winner;
      if (winner && rows.some((r) => r.name === winner)) {
        return winner;
      }
    } catch {
      // fall through
    }
    return rows[0]!.name;
  }
}
