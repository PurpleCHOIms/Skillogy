import neo4j, { type Driver } from "neo4j-driver";

import { getDriver } from "../infra/db.js";
import type { ParsedSkill, TriggerSurface } from "../domain/types.js";

const SCHEMA_STATEMENTS = [
  "CREATE CONSTRAINT skill_name_unique IF NOT EXISTS FOR (s:Skill) REQUIRE s.name IS UNIQUE",
  "CREATE INDEX intent_label_idx IF NOT EXISTS FOR (i:Intent) ON (i.label)",
  "CREATE INDEX signal_kind_value_idx IF NOT EXISTS FOR (s:Signal) ON (s.kind, s.value)",
];

export async function initSchema(driver?: Driver): Promise<void> {
  const drv = driver ?? getDriver();
  for (const stmt of SCHEMA_STATEMENTS) {
    await drv.executeQuery(stmt);
  }
}

export interface BuildGraphCounts {
  skills: number;
  intents: number;
  signals: number;
  exclusions: number;
  edges: number;
  related_to: number;
}

export interface BuildGraphOptions {
  driver?: Driver;
  clearFirst?: boolean;
}

export async function buildGraph(
  extracted: TriggerSurface[],
  opts: BuildGraphOptions = {},
): Promise<BuildGraphCounts> {
  const drv = opts.driver ?? getDriver();
  const clearFirst = opts.clearFirst ?? false;

  if (clearFirst) {
    await drv.executeQuery("MATCH (n) DETACH DELETE n");
  }

  const counts: BuildGraphCounts = {
    skills: 0,
    intents: 0,
    signals: 0,
    exclusions: 0,
    edges: 0,
    related_to: 0,
  };

  for (const surface of extracted) {
    await drv.executeQuery("MERGE (s:Skill {name: $name})", { name: surface.skillName });
    counts.skills += 1;

    for (const label of surface.intents) {
      await drv.executeQuery(
        "MERGE (i:Intent {label: $label}) " +
          "WITH i MATCH (s:Skill {name: $skill}) " +
          "MERGE (s)-[:TRIGGERED_BY]->(i)",
        { label, skill: surface.skillName },
      );
      counts.intents += 1;
      counts.edges += 1;
    }

    for (const sig of surface.signals) {
      await drv.executeQuery(
        "MERGE (n:Signal {kind: $kind, value: $value}) " +
          "WITH n MATCH (s:Skill {name: $skill}) " +
          "MERGE (s)-[:TRIGGERED_BY]->(n)",
        { kind: sig.kind, value: sig.value, skill: surface.skillName },
      );
      counts.signals += 1;
      counts.edges += 1;
    }

    for (const exc of surface.exclusions) {
      await drv.executeQuery(
        "MERGE (n:Signal {kind: $kind, value: $value}) " +
          "WITH n MATCH (s:Skill {name: $skill}) " +
          "MERGE (s)-[:EXCLUDED_BY]->(n)",
        { kind: exc.kind, value: exc.value, skill: surface.skillName },
      );
      counts.exclusions += 1;
      counts.edges += 1;
    }
  }

  // Pass 3: RELATES_TO inter-skill edges
  for (const ex of extracted) {
    for (const targetName of ex.relatedSkills) {
      if (!targetName || targetName === ex.skillName) continue;
      await drv.executeQuery(
        `
          MATCH (s:Skill {name: $skill_name})
          OPTIONAL MATCH (t:Skill {name: $target_name})
          WITH s, t
          WHERE t IS NOT NULL AND s <> t
          MERGE (s)-[:RELATES_TO]->(t)
        `,
        { skill_name: ex.skillName, target_name: targetName },
      );
      counts.edges += 1;
      counts.related_to += 1;
    }
  }

  return counts;
}

export async function enrichWithParsed(
  parsedLookup: Map<string, ParsedSkill>,
  driver?: Driver,
): Promise<number> {
  const drv = driver ?? getDriver();
  let enriched = 0;
  for (const [skillName, parsed] of parsedLookup) {
    const { records } = await drv.executeQuery(
      `
        MATCH (s:Skill {name: $name})
        SET s.description = $description,
            s.source_path = $source_path,
            s.body_length = $body_length,
            s.scope = $scope
        RETURN s.name AS name
      `,
      {
        name: skillName,
        description: parsed.description,
        source_path: parsed.sourcePath,
        body_length: parsed.body.length,
        scope: parsed.scope,
      },
    );
    if (records.length > 0) enriched += 1;
  }
  return enriched;
}

export interface GraphNode {
  id: string;
  kind: string;
  [key: string]: unknown;
}

export interface GraphEdge {
  src: string;
  dst: string;
  etype: string;
}

export interface GraphJson {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

interface Neo4jNodeLike {
  labels: string[];
  properties: Record<string, unknown>;
  elementId: string;
}

function nodeIdFromKind(node: Neo4jNodeLike, kind: string): string {
  const props = node.properties;
  if (kind === "Skill") return String(props.name ?? node.elementId);
  if (kind === "Intent") return `intent::${String(props.label ?? "")}`;
  if (kind === "Signal") {
    return `signal::${String(props.kind ?? "")}::${String(props.value ?? "")}`;
  }
  return node.elementId;
}

export async function exportGraphJson(driver?: Driver): Promise<GraphJson> {
  const drv = driver ?? getDriver();

  const { records: nodeRecords } = await drv.executeQuery("MATCH (n) RETURN n", undefined, {
    routing: neo4j.routing.READ,
  });

  const nodes: GraphNode[] = [];
  for (const record of nodeRecords) {
    const n = record.get("n") as Neo4jNodeLike;
    const labels = n.labels;
    const kind = labels[0] ?? "Unknown";
    const props = n.properties;

    const entry: GraphNode = { id: "", kind };
    if (kind === "Skill") {
      entry.id = String(props.name ?? "");
      entry.description = String(props.description ?? "");
      entry.source_path = String(props.source_path ?? "");
      entry.body_length = Number(props.body_length ?? 0);
      entry.scope = String(props.scope ?? "user");
    } else if (kind === "Intent") {
      const label = String(props.label ?? "");
      entry.id = `intent::${label}`;
      entry.label = label;
    } else if (kind === "Signal") {
      const sigKind = String(props.kind ?? "");
      const value = String(props.value ?? "");
      entry.id = `signal::${sigKind}::${value}`;
      entry.signal_kind = sigKind;
      entry.value = value;
    } else {
      entry.id = n.elementId;
    }
    nodes.push(entry);
  }

  const { records: edgeRecords } = await drv.executeQuery(
    "MATCH (a)-[r]->(b) RETURN a, r, b",
    undefined,
    { routing: neo4j.routing.READ },
  );

  const edges: GraphEdge[] = [];
  for (const record of edgeRecords) {
    const a = record.get("a") as Neo4jNodeLike;
    const b = record.get("b") as Neo4jNodeLike;
    const r = record.get("r") as { type: string };
    const aKind = a.labels[0] ?? "Unknown";
    const bKind = b.labels[0] ?? "Unknown";
    edges.push({
      src: nodeIdFromKind(a, aKind),
      dst: nodeIdFromKind(b, bKind),
      etype: r.type.toLowerCase(),
    });
  }

  return { nodes, edges };
}

export async function clearGraph(driver?: Driver): Promise<void> {
  const drv = driver ?? getDriver();
  await drv.executeQuery("MATCH (n) DETACH DELETE n");
}
