import { useEffect, useState } from 'react'
import { listSkills, getScopes, getProjects } from '@/lib/api'
import type { SkillSummary, SkillDetail as SkillDetailData, GraphNode, Scope, ScopeCounts, Project } from '@/lib/api'
import { Input } from '@/components/ui/input'
import { Separator } from '@/components/ui/separator'
import SkillGraph from '@/components/SkillGraph'
import SkillDetailPanel from '@/components/SkillDetail'
import { ThemeToggle } from '@/components/theme-toggle'

const SCOPE_CHIPS: { label: string; value: Scope | 'all' }[] = [
  { label: 'All', value: 'all' },
  { label: 'Project', value: 'project' },
  { label: 'User', value: 'user' },
  { label: 'Plugin', value: 'plugin' },
]

const SCOPE_BADGE_CLASS: Record<Scope, string> = {
  project: 'bg-blue-100 text-blue-700',
  user: 'bg-green-100 text-green-700',
  plugin: 'bg-gray-100 text-gray-600',
}

export default function App() {
  const [skills, setSkills] = useState<SkillSummary[]>([])
  const [projects, setProjects] = useState<Project[]>([])
  const [scopeCounts, setScopeCounts] = useState<ScopeCounts | null>(null)
  const [selectedScope, setSelectedScope] = useState<Scope | 'all'>('all')
  const [selectedProject, setSelectedProject] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const [detail, setDetail] = useState<SkillDetailData | null>(null)
  const [graphNode, setGraphNode] = useState<GraphNode | null>(null)
  const [loadingList, setLoadingList] = useState(true)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [listError, setListError] = useState<string | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [rightPanelOpen, setRightPanelOpen] = useState(true)

  useEffect(() => {
    setLoadingList(true)
    Promise.all([
      listSkills(),
      getScopes().catch(() => null),
      getProjects().catch(() => [] as Project[]),
    ])
      .then(([data, counts, projs]) => {
        setSkills(data)
        setScopeCounts(counts)
        setProjects(projs ?? [])
        setListError(null)
      })
      .catch((err: unknown) => {
        setListError(err instanceof Error ? err.message : 'Failed to load skills')
      })
      .finally(() => setLoadingList(false))
  }, [])

  useEffect(() => {
    if (!selected) {
      setDetail(null)
      setGraphNode(null)
      return
    }
    const isSkill = skills.some((s) => s.name === selected)
    if (isSkill) {
      setLoadingDetail(true)
      setDetailError(null)
      setGraphNode(null)
      import('@/lib/api').then(({ getSkill }) =>
        getSkill(selected)
          .then((data) => setDetail(data))
          .catch((err: unknown) => {
            setDetailError(err instanceof Error ? err.message : 'Failed to load skill detail')
            setDetail(null)
          })
          .finally(() => setLoadingDetail(false))
      )
    } else {
      setDetail(null)
      setLoadingDetail(false)
      setDetailError(null)
      setGraphNode(parseNodeId(selected))
    }
  }, [selected, skills])

  const filtered = skills.filter((s) => {
    if (!s.name.toLowerCase().includes(query.toLowerCase())) return false
    if (selectedProject) return s.project_path === selectedProject
    if (selectedScope !== 'all' && s.scope !== selectedScope) return false
    return true
  })

  const handleSkillSelect = (name: string) => {
    if (selected === name) {
      setSelected(null)
      return
    }
    setSelected(name)
    if (!rightPanelOpen) setRightPanelOpen(true)
  }

  const handleProjectSelect = (path: string | null) => {
    setSelectedProject(path)
    if (path) setSelectedScope('all') // reset scope chip when project is active
  }

  return (
    <div className="h-screen flex flex-col bg-background text-foreground overflow-hidden">
      {/* Header */}
      <header className="border-b px-4 py-2 flex items-center justify-between shrink-0">
        <span className="font-semibold text-sm">Skill Router</span>
        <div className="flex items-center gap-1">
          {!rightPanelOpen && (
            <button
              type="button"
              onClick={() => setRightPanelOpen(true)}
              className="inline-flex h-8 px-2 items-center rounded-md text-xs text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
            >
              Detail »
            </button>
          )}
          <ThemeToggle />
        </div>
      </header>

      <main
        className="flex-1 grid overflow-hidden transition-[grid-template-columns] duration-200 ease-out"
        style={{ gridTemplateColumns: rightPanelOpen ? '280px 1fr 360px' : '280px 1fr 0px' }}
      >

      {/* Left panel */}
      <aside className="border-r flex flex-col overflow-hidden">
        <div className="p-4 pb-2 shrink-0">
          <h1 className="text-lg font-semibold mb-3">Skill Catalog</h1>

          {/* Project selector */}
          {projects.length > 0 && (
            <div className="mb-2">
              <select
                value={selectedProject ?? ''}
                onChange={(e) => handleProjectSelect(e.target.value || null)}
                className="w-full text-xs rounded-md border border-border bg-background px-2 py-1.5 text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
              >
                <option value="">All projects</option>
                {projects.map((p) => (
                  <option key={p.path} value={p.path}>
                    {p.name} ({p.skill_count})
                  </option>
                ))}
              </select>
            </div>
          )}

          <Input
            placeholder="Search skills…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="mb-2"
          />

          {/* Scope chips — hidden when a project is selected */}
          {!selectedProject && (
            <div className="flex flex-wrap gap-1 mb-2">
              {SCOPE_CHIPS.map(({ label, value }) => {
                const count = scopeCounts
                  ? value === 'all' ? scopeCounts.total : scopeCounts[value as Scope]
                  : null
                return (
                  <button
                    key={value}
                    type="button"
                    onClick={() => setSelectedScope(value)}
                    className={[
                      'px-2 py-0.5 rounded-full text-xs font-medium border transition-colors',
                      selectedScope === value
                        ? 'bg-primary text-primary-foreground border-primary'
                        : 'bg-muted text-muted-foreground border-border hover:bg-muted/80',
                    ].join(' ')}
                  >
                    {label}{count !== null ? ` (${count})` : ''}
                  </button>
                )
              })}
            </div>
          )}

          <p className="text-xs text-muted-foreground">
            {loadingList ? 'Loading…' : `${filtered.length} skill${filtered.length !== 1 ? 's' : ''}`}
          </p>
        </div>
        <Separator />
        <div className="flex-1 overflow-y-auto min-h-0">
          {listError && <p className="p-4 text-sm text-destructive">{listError}</p>}
          {!loadingList && filtered.length === 0 && !listError && (
            <p className="p-4 text-sm text-muted-foreground">No skills found.</p>
          )}
          <div className="flex flex-col">
            {filtered.map((skill) => (
              <button
                key={skill.name}
                type="button"
                onClick={() => handleSkillSelect(skill.name)}
                className={[
                  'text-left px-4 py-3 border-b border-border transition-colors',
                  'hover:bg-muted focus:outline-none focus:bg-muted',
                  selected === skill.name ? 'bg-muted font-medium' : '',
                ].join(' ')}
              >
                <div className="flex items-center gap-1.5">
                  <span className="text-sm font-medium truncate flex-1">{skill.name}</span>
                  {!selectedProject && (
                    <span className={[
                      'text-[10px] px-1.5 py-0.5 rounded font-medium shrink-0',
                      SCOPE_BADGE_CLASS[skill.scope] ?? 'bg-gray-100 text-gray-600',
                    ].join(' ')}>
                      {skill.scope}
                    </span>
                  )}
                </div>
                {skill.description && (
                  <div className="text-xs text-muted-foreground mt-0.5 line-clamp-2">
                    {skill.description}
                  </div>
                )}
              </button>
            ))}
          </div>
        </div>
      </aside>

      {/* Center — graph */}
      <section className="overflow-hidden relative">
        <SkillGraph
          filter={query}
          scopeFilter={selectedScope}
          onSkillSelect={handleSkillSelect}
          selectedSkill={selected}
          projects={projects}
          selectedProject={selectedProject}
          onProjectChange={handleProjectSelect}
          filteredSkillNames={selectedProject ? new Set(filtered.map((s) => s.name)) : null}
        />
      </section>

      {/* Right panel — detail */}
      <aside
        className="border-l overflow-hidden flex flex-col"
        style={{ display: rightPanelOpen ? 'flex' : 'none' }}
      >
        {!selected && !loadingDetail && (
          <>
            <div className="flex items-center justify-between border-b border-border px-4 py-2 shrink-0">
              <span className="text-xs uppercase tracking-wide text-muted-foreground">Detail</span>
              <button
                type="button"
                onClick={() => setRightPanelOpen(false)}
                className="ml-2 inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
                aria-label="Close detail panel"
              >
                ✕
              </button>
            </div>
            <div className="flex items-center justify-center flex-1 text-muted-foreground p-6 text-center">
              <p className="text-sm">Select a skill or click a graph node to view details.</p>
            </div>
          </>
        )}
        {(selected || loadingDetail) && (
          <SkillDetailPanel
            detail={detail}
            graphNode={graphNode}
            loading={loadingDetail}
            error={detailError}
            onClose={() => setRightPanelOpen(false)}
          />
        )}
      </aside>

      </main>
    </div>
  )
}

function parseNodeId(id: string): GraphNode {
  if (id.startsWith('intent::')) {
    return { id, kind: 'Intent', label: id.slice('intent::'.length) }
  }
  if (id.startsWith('signal::')) {
    const parts = id.split('::')
    return { id, kind: 'Signal', signal_kind: parts[1], value: parts.slice(2).join('::') }
  }
  return { id, kind: 'Skill' }
}
