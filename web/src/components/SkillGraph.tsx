/**
 * SkillGraph — Canvas-style knowledge map UI (US-015).
 * Toolbar: layout selector + cluster/hide/related toggles.
 * Legend overlay, hover-dimming, scope clustering.
 */
import { useEffect, useRef, useState, useCallback } from 'react'
import CytoscapeComponent from 'react-cytoscapejs'
import cytoscape from 'cytoscape'
// eslint-disable-next-line @typescript-eslint/ban-ts-comment
// @ts-ignore
import fcose from 'cytoscape-fcose'
import coseBilkent from 'cytoscape-cose-bilkent'
import { subscribeGraph } from '@/lib/api'
import type { GraphData, GraphNode, GraphEdge, Scope } from '@/lib/api'
import { useTheme } from '@/components/theme-provider'

// Register layouts once
cytoscape.use(fcose)
cytoscape.use(coseBilkent)

// ── Constants ────────────────────────────────────────────────────────────────

const SCOPE_BORDER: Record<string, string> = {
  project: '#2563eb',
  user: '#16a34a',
  plugin: '#6b7280',
}

const SCOPE_BG_TINT: Record<string, string> = {
  project: '#eff6ff',
  user: '#f0fdf4',
  plugin: '#f9fafb',
}

const SIGNAL_KIND_COLOR: Record<string, string> = {
  keyword: '#fbbf24',
  file_ext: '#5eead4',
  tool_name: '#c4b5fd',
  error_pattern: '#fca5a5',
  pattern: '#d1d5db',
}

const LAYOUT_NAMES = ['fcose', 'cose-bilkent', 'circle', 'concentric'] as const
type LayoutName = typeof LAYOUT_NAMES[number]

const SCOPE_PARENT_IDS = ['scope-project', 'scope-user', 'scope-plugin'] as const

// ── Stylesheet ───────────────────────────────────────────────────────────────

function buildStylesheet(resolvedTheme: 'dark' | 'light'): cytoscape.StylesheetStyle[] {
  const edgeLabelBg = resolvedTheme === 'dark' ? '#1f2937' : '#ffffff'
  return [
    // Default node
    {
      selector: 'node',
      style: {
        'background-color': 'data(bgColor)',
        'width': 'data(size)',
        'height': 'data(size)',
        'shape': 'data(shape)' as cytoscape.Css.Node['shape'],
        'label': 'data(displayLabel)',
        'font-size': 11,
        'color': resolvedTheme === 'dark' ? '#e5e7eb' : '#111827',
        'text-valign': 'center',
        'text-halign': 'right',
        'text-margin-x': 6,
        'text-outline-width': 0,
        'border-width': 'data(borderWidth)',
        'border-color': 'data(borderColor)',
        'transition-property': 'opacity',
        'transition-duration': 200,
        'min-zoomed-font-size': 8,
        'z-index': 1,
      },
    },
    // Compound (scope parent) nodes
    {
      selector: ':parent',
      style: {
        'background-color': 'data(bgColor)',
        'background-opacity': 0.35,
        'border-width': 1,
        'border-color': 'data(borderColor)',
        'border-style': 'dashed',
        'label': 'data(label)',
        'text-valign': 'top',
        'text-halign': 'center',
        'font-size': 10,
        'color': 'data(borderColor)',
        'padding': '20px',
      },
    },
    // Base edge style — thin, low-opacity, no labels by default
    {
      selector: 'edge',
      style: {
        'curve-style': 'bezier',
        'opacity': 0.5,
        'label': '',
        'font-size': 9,
        'text-rotation': 'autorotate',
        'text-background-opacity': 0,
        'color': resolvedTheme === 'dark' ? '#9ca3af' : '#374151',
        'transition-property': 'opacity',
        'transition-duration': 200,
        'target-arrow-shape': 'triangle',
        'target-arrow-color': 'data(lineColor)',
        'line-color': 'data(lineColor)',
        'width': 'data(edgeWidth)',
        'line-style': 'data(lineStyle)' as cytoscape.Css.Edge['line-style'],
      },
    },
    // triggered_by edge
    {
      selector: 'edge[etype = "triggered_by"]',
      style: {
        'line-color': '#9ca3af',
        'target-arrow-color': '#9ca3af',
        'width': 1,
      },
    },
    // excluded_by edge
    {
      selector: 'edge[etype = "excluded_by"]',
      style: {
        'line-color': '#dc2626',
        'target-arrow-color': '#dc2626',
        'line-style': 'dashed',
        'width': 1.2,
        'opacity': 0.6,
      },
    },
    // relates_to edge
    {
      selector: 'edge[etype = "relates_to"]',
      style: {
        'line-color': '#3b82f6',
        'target-arrow-color': '#3b82f6',
        'opacity': 0.4,
        'width': 2,
      },
    },
    // Dimmed
    {
      selector: 'node.dimmed',
      style: { 'opacity': 0.15 },
    },
    {
      selector: 'edge.dimmed',
      style: { 'opacity': 0.04 },
    },
    // Highlighted nodes
    {
      selector: 'node.highlighted',
      style: { 'opacity': 1 },
    },
    // Highlighted edges — show labels
    {
      selector: 'edge.highlighted',
      style: {
        'label': 'data(etype)',
        'text-background-color': edgeLabelBg,
        'text-background-opacity': 0.85,
        'text-background-padding': '2px',
        'opacity': 1,
        'width': 2,
        'z-index': 999,
      },
    },
  ]
}

// ── Element builders ─────────────────────────────────────────────────────────

function scopeParentId(scope: string): string {
  return `scope-${scope}`
}

function buildElements(
  data: GraphData,
  scopeFilter: Scope | 'all',
  clusterByScope: boolean,
  hideSignals: boolean,
  showOnlyRelated: boolean,
  selectedId: string | null,
  resolvedTheme: 'dark' | 'light' = 'light',
  filteredSkillNames: Set<string> | null = null,
): cytoscape.ElementDefinition[] {
  const els: cytoscape.ElementDefinition[] = []

  // Scope-filtered Skill ids (further restricted by project filter if active)
  const allowedSkillIds = new Set<string>()
  for (const n of data.nodes) {
    if (n.kind === 'Skill') {
      const ns = (n as { scope?: string }).scope ?? 'user'
      if (scopeFilter !== 'all' && ns !== scopeFilter) continue
      if (filteredSkillNames && !filteredSkillNames.has(n.id)) continue
      allowedSkillIds.add(n.id)
    }
  }

  // Build kind lookup for edge expansion
  const nodeKindMap = new Map<string, string>()
  for (const n of data.nodes) nodeKindMap.set(n.id, n.kind)

  // Expand to connected Intent/Signal nodes only (never leak foreign Skill nodes)
  const allowedNodeIds = new Set<string>(allowedSkillIds)
  for (const e of data.edges) {
    if (allowedSkillIds.has(e.src) && nodeKindMap.get(e.dst) !== 'Skill') allowedNodeIds.add(e.dst)
    if (allowedSkillIds.has(e.dst) && nodeKindMap.get(e.src) !== 'Skill') allowedNodeIds.add(e.src)
  }

  // "Show only related" — keep selected + 1-hop + 2-hop
  let visibleNodeIds: Set<string> | null = null
  if (showOnlyRelated && selectedId) {
    visibleNodeIds = new Set<string>()
    visibleNodeIds.add(selectedId)
    // 1-hop
    const hop1 = new Set<string>()
    for (const e of data.edges) {
      if (e.src === selectedId) hop1.add(e.dst)
      if (e.dst === selectedId) hop1.add(e.src)
    }
    hop1.forEach((id) => visibleNodeIds!.add(id))
    // 2-hop
    for (const e of data.edges) {
      if (hop1.has(e.src)) visibleNodeIds!.add(e.dst)
      if (hop1.has(e.dst)) visibleNodeIds!.add(e.src)
    }
  }

  // Collect scopes that actually have nodes (for parent generation)
  const usedScopes = new Set<string>()

  // Build node elements
  for (const n of data.nodes) {
    if (!allowedNodeIds.has(n.id)) continue
    if (hideSignals && n.kind === 'Signal') continue
    if (visibleNodeIds && !visibleNodeIds.has(n.id)) continue

    const nodeScope: string = (n as { scope?: string }).scope ?? 'user'
    if (n.kind === 'Skill') usedScopes.add(nodeScope)

    let bgColor: string
    let size: number
    let shape: string
    let borderColor: string
    let borderWidth: number

    if (n.kind === 'Skill') {
      bgColor = resolvedTheme === 'dark' ? '#1f2937' : '#f3f4f6'
      size = 30
      shape = 'round-rectangle'
      borderColor = SCOPE_BORDER[nodeScope] ?? '#6b7280'
      borderWidth = 2
    } else if (n.kind === 'Intent') {
      bgColor = '#86efac'
      size = 14
      shape = 'ellipse'
      borderColor = '#16a34a'
      borderWidth = 1
    } else {
      // Signal
      const sk = n.signal_kind ?? 'pattern'
      bgColor = SIGNAL_KIND_COLOR[sk] ?? '#d1d5db'
      size = 11
      shape = 'diamond'
      borderColor = '#9ca3af'
      borderWidth = 0
    }

    const parentAttr = clusterByScope && n.kind === 'Skill'
      ? { parent: scopeParentId(nodeScope) }
      : {}

    let displayLabel: string
    if (n.kind === 'Skill') {
      displayLabel = n.id
    } else if (n.kind === 'Intent') {
      displayLabel = n.label ?? n.id
    } else {
      // Signal: id is "kind::subkind::value" — show only the value part
      const parts = n.id.split('::')
      displayLabel = parts.length >= 3 ? parts[parts.length - 1] : n.id
    }

    els.push({
      data: {
        id: n.id,
        kind: n.kind,
        label: n.id,
        displayLabel,
        bgColor,
        size,
        shape,
        borderColor,
        borderWidth,
        signal_kind: n.signal_kind,
        ...parentAttr,
      },
    })
  }

  // Add compound parent nodes when clustering
  if (clusterByScope) {
    for (const scope of usedScopes) {
      els.push({
        data: {
          id: scopeParentId(scope),
          label: scope.charAt(0).toUpperCase() + scope.slice(1),
          bgColor: SCOPE_BG_TINT[scope] ?? '#f9fafb',
          borderColor: SCOPE_BORDER[scope] ?? '#6b7280',
        },
      })
    }
  }

  // Build edge elements
  for (const e of data.edges) {
    if (!allowedNodeIds.has(e.src) || !allowedNodeIds.has(e.dst)) continue
    if (hideSignals) {
      // Check if either endpoint is a Signal node
      const srcNode = data.nodes.find((n) => n.id === e.src)
      const dstNode = data.nodes.find((n) => n.id === e.dst)
      if (srcNode?.kind === 'Signal' || dstNode?.kind === 'Signal') continue
    }
    if (visibleNodeIds && (!visibleNodeIds.has(e.src) || !visibleNodeIds.has(e.dst))) continue

    let lineColor: string
    let lineStyle: string
    let edgeWidth: number
    let edgeOpacity: number

    if (e.etype === 'triggered_by') {
      lineColor = '#16a34a'
      lineStyle = 'solid'
      edgeWidth = 2
      edgeOpacity = 0.7
    } else if (e.etype === 'excluded_by') {
      lineColor = '#dc2626'
      lineStyle = 'dashed'
      edgeWidth = 2
      edgeOpacity = 0.7
    } else if (e.etype === 'relates_to') {
      lineColor = '#3b82f6'
      lineStyle = 'solid'
      edgeWidth = 4
      edgeOpacity = 0.4
    } else {
      lineColor = '#9ca3af'
      lineStyle = 'solid'
      edgeWidth = 1.5
      edgeOpacity = 0.5
    }

    els.push({
      data: {
        source: e.src,
        target: e.dst,
        etype: e.etype,
        lineColor,
        lineStyle,
        edgeWidth,
        edgeOpacity,
        id: `${e.src}__${e.etype}__${e.dst}`,
      },
    })
  }

  return els
}

function buildLayout(layoutName: LayoutName): Parameters<cytoscape.Core['layout']>[0] {
  if (layoutName === 'fcose') {
    return {
      name: 'fcose',
      quality: 'default',
      randomize: true,
      animate: true,
      animationDuration: 600,
      fit: true,
      padding: 32,
      nodeRepulsion: 8000,
      edgeElasticity: 0.45,
      idealEdgeLength: 80,
      gravity: 0.25,
      numIter: 2500,
    } as Parameters<cytoscape.Core['layout']>[0]
  }
  if (layoutName === 'cose-bilkent') {
    return {
      name: 'cose-bilkent',
      animate: true,
      animationDuration: 600,
      fit: true,
      padding: 32,
      nodeRepulsion: 8000,
      edgeElasticity: 0.45,
      idealEdgeLength: 80,
      gravity: 0.25,
      numIter: 2500,
      randomize: true,
    } as Parameters<cytoscape.Core['layout']>[0]
  }
  return {
    name: layoutName,
    animate: true,
    animationDuration: 600,
    fit: true,
    padding: 32,
  } as Parameters<cytoscape.Core['layout']>[0]
}

// ── Graph merge helper ────────────────────────────────────────────────────────

function mergeGraphData(existing: GraphData, newNodes: GraphNode[], newEdges: GraphEdge[]): GraphData {
  const nodeIds = new Set(existing.nodes.map(n => n.id))
  const edgeIds = new Set(existing.edges.map(e => `${e.src}__${e.etype}__${e.dst}`))
  return {
    nodes: [
      ...existing.nodes,
      ...newNodes.filter(n => !nodeIds.has(n.id)),
    ],
    edges: [
      ...existing.edges,
      ...newEdges.filter(e => !edgeIds.has(`${e.src}__${e.etype}__${e.dst}`)),
    ],
  }
}

// ── Legend ───────────────────────────────────────────────────────────────────

function Legend({ resolvedTheme }: { resolvedTheme: 'dark' | 'light' }) {
  const isDark = resolvedTheme === 'dark'
  const bg = isDark ? 'rgba(15,15,20,0.88)' : 'rgba(255,255,255,0.88)'
  const borderColor = isDark ? '#374151' : '#e5e7eb'
  const titleColor = isDark ? '#e5e7eb' : '#374151'
  const textColor = isDark ? '#9ca3af' : '#6b7280'
  const skillBg = isDark ? '#1f2937' : '#f3f4f6'

  return (
    <div
      style={{
        position: 'absolute',
        top: 56,
        right: 8,
        width: 200,
        background: bg,
        backdropFilter: 'blur(4px)',
        border: `1px solid ${borderColor}`,
        borderRadius: 8,
        padding: '10px 12px',
        fontSize: 11,
        zIndex: 20,
        pointerEvents: 'none',
      }}
    >
      <div style={{ fontWeight: 600, marginBottom: 6, color: titleColor }}>Legend</div>

      {/* Skill */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
        <div style={{ display: 'flex', gap: 2 }}>
          {(['#2563eb', '#16a34a', '#6b7280'] as const).map((c) => (
            <div
              key={c}
              style={{
                width: 14,
                height: 14,
                borderRadius: 2,
                border: `2px solid ${c}`,
                background: skillBg,
                flexShrink: 0,
              }}
            />
          ))}
        </div>
        <span style={{ color: textColor }}>Skill (proj/user/plugin)</span>
      </div>

      {/* Intent */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
        <div
          style={{
            width: 8,
            height: 8,
            borderRadius: '50%',
            background: '#86efac',
            border: '1px solid #16a34a',
            flexShrink: 0,
          }}
        />
        <span style={{ color: textColor }}>Intent</span>
      </div>

      {/* Signal */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
        <div
          style={{
            width: 7,
            height: 7,
            background: '#fbbf24',
            transform: 'rotate(45deg)',
            flexShrink: 0,
          }}
        />
        <span style={{ color: textColor }}>Signal (color varies)</span>
      </div>

      <div style={{ borderTop: `1px solid ${borderColor}`, paddingTop: 6 }}>
        {/* TRIGGERED_BY */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 3 }}>
          <div style={{ width: 24, height: 2, background: '#16a34a', flexShrink: 0 }} />
          <span style={{ color: textColor }}>TRIGGERED_BY</span>
        </div>
        {/* EXCLUDED_BY */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 3 }}>
          <div
            style={{
              width: 24,
              height: 2,
              background: 'repeating-linear-gradient(to right, #dc2626 0 4px, transparent 4px 8px)',
              flexShrink: 0,
            }}
          />
          <span style={{ color: textColor }}>EXCLUDED_BY</span>
        </div>
        {/* RELATES_TO */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <div style={{ width: 24, height: 4, background: 'rgba(59,130,246,0.4)', borderRadius: 2, flexShrink: 0 }} />
          <span style={{ color: textColor }}>RELATES_TO</span>
        </div>
      </div>

      <div style={{ borderTop: `1px solid ${borderColor}`, paddingTop: 6, marginTop: 4 }}>
        <span style={{ color: textColor, fontStyle: 'italic' }}>Hover node to see edge types</span>
      </div>
    </div>
  )
}

// ── Toolbar ───────────────────────────────────────────────────────────────────

interface ToolbarProps {
  layout: LayoutName
  onLayoutChange: (l: LayoutName) => void
  clusterByScope: boolean
  onClusterByScopeChange: (v: boolean) => void
  hideSignals: boolean
  onHideSignalsChange: (v: boolean) => void
  showOnlyRelated: boolean
  onShowOnlyRelatedChange: (v: boolean) => void
  resolvedTheme: 'dark' | 'light'
  projects: { path: string; name: string; skill_count: number }[]
  selectedProject: string | null
  onProjectChange: (path: string | null) => void
}

function Toolbar({
  layout,
  onLayoutChange,
  clusterByScope,
  onClusterByScopeChange,
  hideSignals,
  onHideSignalsChange,
  showOnlyRelated,
  onShowOnlyRelatedChange,
  resolvedTheme,
  projects,
  selectedProject,
  onProjectChange,
}: ToolbarProps) {
  const isDark = resolvedTheme === 'dark'
  const bg = isDark ? 'rgba(15,15,20,0.92)' : 'rgba(255,255,255,0.92)'
  const borderColor = isDark ? '#374151' : '#e5e7eb'
  const selectBg = isDark ? '#1f2937' : '#fff'
  const selectColor = isDark ? '#e5e7eb' : '#374151'
  const selectBorder = isDark ? '#374151' : '#d1d5db'
  const dividerColor = isDark ? '#374151' : '#e5e7eb'

  const selectStyle = {
    fontSize: 11,
    padding: '2px 6px',
    borderRadius: 4,
    border: `1px solid ${selectBorder}`,
    background: selectBg,
    cursor: 'pointer',
    color: selectColor,
  }

  return (
    <div
      style={{
        position: 'absolute',
        top: 8,
        left: 8,
        right: 8,
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        zIndex: 20,
        background: bg,
        backdropFilter: 'blur(4px)',
        border: `1px solid ${borderColor}`,
        borderRadius: 8,
        padding: '6px 10px',
        flexWrap: 'wrap',
      }}
    >
      {/* Project selector */}
      {projects.length > 0 && (
        <>
          <select
            value={selectedProject ?? ''}
            onChange={(e) => onProjectChange(e.target.value || null)}
            style={{ ...selectStyle, maxWidth: 160 }}
          >
            <option value="">All projects</option>
            {projects.map((p) => (
              <option key={p.path} value={p.path}>
                {p.name} ({p.skill_count})
              </option>
            ))}
          </select>
          <div style={{ width: 1, height: 16, background: dividerColor, flexShrink: 0 }} />
        </>
      )}

      {/* Layout selector */}
      <select
        value={layout}
        onChange={(e) => onLayoutChange(e.target.value as LayoutName)}
        style={selectStyle}
      >
        {LAYOUT_NAMES.map((l) => (
          <option key={l} value={l}>{l}</option>
        ))}
      </select>

      <div style={{ width: 1, height: 16, background: dividerColor, flexShrink: 0 }} />

      {/* Toggle buttons */}
      {([
        { label: 'Cluster by scope', value: clusterByScope, onChange: onClusterByScopeChange },
        { label: 'Hide signals', value: hideSignals, onChange: onHideSignalsChange },
        { label: 'Only related', value: showOnlyRelated, onChange: onShowOnlyRelatedChange },
      ] as const).map(({ label, value, onChange }) => (
        <button
          key={label}
          type="button"
          onClick={() => onChange(!value)}
          style={{
            fontSize: 11,
            padding: '2px 8px',
            borderRadius: 4,
            border: `1px solid ${value ? '#3b82f6' : selectBorder}`,
            background: value ? (isDark ? '#1e3a5f' : '#eff6ff') : selectBg,
            color: value ? '#60a5fa' : (isDark ? '#9ca3af' : '#6b7280'),
            cursor: 'pointer',
            fontWeight: value ? 600 : 400,
            transition: 'all 0.15s',
          }}
        >
          {label}
        </button>
      ))}
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

interface Props {
  filter: string
  scopeFilter: Scope | 'all'
  onSkillSelect: (name: string) => void
  selectedSkill: string | null
  projects?: { path: string; name: string; skill_count: number }[]
  selectedProject?: string | null
  onProjectChange?: (path: string | null) => void
  filteredSkillNames?: Set<string> | null
}

export default function SkillGraph({ filter, scopeFilter, onSkillSelect, selectedSkill, projects = [], selectedProject = null, onProjectChange, filteredSkillNames = null }: Props) {
  const cyRef = useRef<cytoscape.Core | null>(null)
  const lastLayoutSizeRef = useRef(0)
  const lastLayoutNameRef = useRef<LayoutName | null>(null)
  const selectedSkillRef = useRef<string | null>(selectedSkill)
  const [graphData, setGraphData] = useState<GraphData>({ nodes: [], edges: [] })
  const [elements, setElements] = useState<cytoscape.ElementDefinition[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  // Toolbar state
  const [layout, setLayout] = useState<LayoutName>('fcose')
  const [clusterByScope, setClusterByScope] = useState(false)
  const [hideSignals, setHideSignals] = useState(false)
  const [showOnlyRelated, setShowOnlyRelated] = useState(false)

  const { resolved: resolvedTheme } = useTheme()

  // Subscribe to SSE graph stream on mount
  useEffect(() => {
    const cleanup = subscribeGraph(
      (newNodes, newEdges) => {
        setGraphData(prev => mergeGraphData(prev, newNodes, newEdges))
        setError(null)
        setLoading(false)
      },
      () => {
        setError('Lost connection to graph stream')
        setLoading(false)
      },
    )
    return cleanup
  }, [])

  // Rebuild elements when data or any filter/toggle/theme changes
  useEffect(() => {
    setElements(
      buildElements(graphData, scopeFilter, clusterByScope, hideSignals, showOnlyRelated, selectedSkill, resolvedTheme, filteredSkillNames)
    )
  }, [graphData, scopeFilter, clusterByScope, hideSignals, showOnlyRelated, selectedSkill, resolvedTheme, filteredSkillNames])

  // Re-run layout when layout name changes or elements grow significantly
  useEffect(() => {
    const cy = cyRef.current
    if (!cy || elements.length === 0) return
    const layoutNameChanged = layout !== lastLayoutNameRef.current
    const sizeGrew = elements.length > lastLayoutSizeRef.current + 3
    if (!layoutNameChanged && !sizeGrew) return
    lastLayoutNameRef.current = layout
    lastLayoutSizeRef.current = elements.length
    const layoutInst = cy.layout(buildLayout(layout))
    layoutInst.one('layoutstop', () => {
      cy.fit(undefined, 100)
    })
    layoutInst.run()
  }, [layout, elements])

  // Filter dimming when text filter changes
  useEffect(() => {
    const cy = cyRef.current
    if (!cy || elements.length === 0) return

    if (!filter.trim()) {
      cy.elements().removeClass('dimmed highlighted')
      return
    }

    const q = filter.toLowerCase()
    const matchedSkills = cy.nodes().filter((n) => {
      const id = (n.data('id') as string).toLowerCase()
      const kind = n.data('kind') as string
      return kind === 'Skill' && id.includes(q)
    })

    if (matchedSkills.length === 0) {
      cy.elements().removeClass('dimmed highlighted')
      return
    }

    const highlighted = matchedSkills.union(matchedSkills.neighborhood())
    cy.elements().addClass('dimmed').removeClass('highlighted')
    highlighted.removeClass('dimmed').addClass('highlighted')
  }, [filter, elements])

  // Keep ref in sync so event handlers (set up once) can read current value
  useEffect(() => { selectedSkillRef.current = selectedSkill }, [selectedSkill])

  // Pan/zoom + highlight selected skill and its neighborhood
  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return

    if (!selectedSkill) {
      cy.elements().removeClass('dimmed highlighted')
      return
    }

    const node = cy.getElementById(selectedSkill)
    if (!node.length) return

    cy.animate(
      { center: { eles: node }, zoom: 1.4 } as Parameters<typeof cy.animate>[0],
      { duration: 400 }
    )

    cy.elements().addClass('dimmed').removeClass('highlighted')
    node.removeClass('dimmed').addClass('highlighted')
    node.connectedEdges().removeClass('dimmed').addClass('highlighted')
    node.openNeighborhood().nodes().removeClass('dimmed').addClass('highlighted')
  }, [selectedSkill, elements])

  // Hover dimming logic
  const handleCyInit = useCallback((cy: cytoscape.Core) => {
    cyRef.current = cy

    cy.on('tap', 'node', (evt) => {
      const node = evt.target as cytoscape.NodeSingular
      const id = node.data('id') as string
      // Skip compound/parent nodes
      if (!SCOPE_PARENT_IDS.includes(id as typeof SCOPE_PARENT_IDS[number])) {
        onSkillSelect(id)
      }
    })

    cy.on('mouseover', 'node', (evt) => {
      const target = evt.target as cytoscape.NodeSingular
      if (SCOPE_PARENT_IDS.includes(target.data('id') as typeof SCOPE_PARENT_IDS[number])) return
      cy.elements().addClass('dimmed').removeClass('highlighted')
      target.removeClass('dimmed').addClass('highlighted')
      target.connectedEdges().removeClass('dimmed').addClass('highlighted')
      target.openNeighborhood().nodes().removeClass('dimmed').addClass('highlighted')
    })

    cy.on('mouseout', 'node', () => {
      const sel = selectedSkillRef.current
      if (sel) {
        const selNode = cy.getElementById(sel)
        if (selNode.length) {
          cy.elements().addClass('dimmed').removeClass('highlighted')
          selNode.removeClass('dimmed').addClass('highlighted')
          selNode.connectedEdges().removeClass('dimmed').addClass('highlighted')
          selNode.openNeighborhood().nodes().removeClass('dimmed').addClass('highlighted')
          return
        }
      }
      cy.elements().removeClass('dimmed highlighted')
    })
  }, [onSkillSelect])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
        Loading graph…
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-full text-destructive text-sm p-4">
        {error}
      </div>
    )
  }

  const canvasBg = resolvedTheme === 'dark' ? '#0a0a0a' : '#ffffff'

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%', background: canvasBg }}>
      <Toolbar
        layout={layout}
        onLayoutChange={setLayout}
        clusterByScope={clusterByScope}
        onClusterByScopeChange={setClusterByScope}
        hideSignals={hideSignals}
        onHideSignalsChange={setHideSignals}
        showOnlyRelated={showOnlyRelated}
        onShowOnlyRelatedChange={setShowOnlyRelated}
        resolvedTheme={resolvedTheme}
        projects={projects}
        selectedProject={selectedProject}
        onProjectChange={onProjectChange ?? (() => {})}
      />

      <Legend resolvedTheme={resolvedTheme} />

      <CytoscapeComponent
        elements={elements}
        stylesheet={buildStylesheet(resolvedTheme)}
        layout={buildLayout(layout)}
        cy={handleCyInit}
        style={{ width: '100%', height: '100%', background: canvasBg }}
        wheelSensitivity={0.3}
        minZoom={0.05}
        maxZoom={5}
      />
    </div>
  )
}
