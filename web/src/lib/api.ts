/**
 * Typed fetch helpers for the skill-router backend API.
 */

const BASE_URL = (import.meta.env.VITE_API_BASE as string | undefined) ?? "http://localhost:8765"

export type Scope = "project" | "user" | "plugin"

export interface Project {
  path: string
  name: string
  skill_count: number
}

export interface SkillSummary {
  name: string
  description: string
  scope: Scope
  project_path: string | null
}

export interface SkillDetail {
  name: string
  description: string
  body: string
  source_path: string
  scope: Scope
  project_path: string | null
  raw_frontmatter: Record<string, unknown>
}

export interface ScopeCounts {
  project: number
  user: number
  plugin: number
  total: number
}

export async function listSkills(scope?: Scope | "all"): Promise<SkillSummary[]> {
  const url = scope && scope !== "all"
    ? `${BASE_URL}/api/skills?scope=${encodeURIComponent(scope)}`
    : `${BASE_URL}/api/skills`
  const res = await fetch(url)
  if (!res.ok) throw new Error(`Failed to list skills: ${res.status}`)
  return res.json() as Promise<SkillSummary[]>
}

export async function getScopes(): Promise<ScopeCounts> {
  const res = await fetch(`${BASE_URL}/api/scopes`)
  if (!res.ok) throw new Error(`Failed to get scopes: ${res.status}`)
  return res.json() as Promise<ScopeCounts>
}

export async function getProjects(): Promise<Project[]> {
  const res = await fetch(`${BASE_URL}/api/projects`)
  if (!res.ok) throw new Error(`Failed to get projects: ${res.status}`)
  return res.json() as Promise<Project[]>
}

export async function getSkill(name: string): Promise<SkillDetail> {
  const res = await fetch(`${BASE_URL}/api/skills/${encodeURIComponent(name)}`)
  if (!res.ok) throw new Error(`Failed to get skill '${name}': ${res.status}`)
  return res.json() as Promise<SkillDetail>
}

export interface GraphNode {
  id: string
  kind: string
  description?: string
  label?: string
  signal_kind?: string
  value?: string
}

export interface GraphEdge {
  src: string
  dst: string
  etype: "triggered_by" | "excluded_by" | "relates_to" | string
}

export interface GraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

export async function getGraph(): Promise<GraphData> {
  const res = await fetch(`${BASE_URL}/api/graph`)
  if (!res.ok) throw new Error(`Failed to load graph: ${res.status}`)
  return res.json() as Promise<GraphData>
}

export function subscribeGraph(
  onUpdate: (nodes: GraphNode[], edges: GraphEdge[]) => void,
  onError?: () => void,
): () => void {
  const es = new EventSource(`${BASE_URL}/api/graph/stream`)

  es.onmessage = (event: MessageEvent) => {
    try {
      const data = JSON.parse(event.data as string) as { nodes?: GraphNode[]; edges?: GraphEdge[] }
      onUpdate(data.nodes ?? [], data.edges ?? [])
    } catch {
      // ignore malformed messages
    }
  }

  es.onerror = () => {
    onError?.()
    es.close()
  }

  return () => {
    es.close()
  }
}
