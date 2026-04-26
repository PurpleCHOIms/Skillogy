import ReactMarkdown from 'react-markdown'
import type { SkillDetail as SkillDetailData } from '@/lib/api'
import type { GraphNode } from '@/lib/api'

interface Props {
  detail: SkillDetailData | null
  graphNode: GraphNode | null
  loading: boolean
  error: string | null
  onClose?: () => void
}

export default function SkillDetail({ detail, graphNode, loading, error, onClose }: Props) {
  if (loading) {
    return (
      <div className="flex flex-col flex-1 min-h-0">
        <CloseHeader onClose={onClose} />
        <p className="text-sm text-muted-foreground p-6">Loading…</p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex flex-col flex-1 min-h-0">
        <CloseHeader onClose={onClose} />
        <p className="text-sm text-destructive p-6">{error}</p>
      </div>
    )
  }

  // Non-Skill graph node (Intent / Signal)
  if (!detail && graphNode && graphNode.kind !== 'Skill') {
    return (
      <div className="flex flex-col flex-1 min-h-0 overflow-hidden">
        <CloseHeader onClose={onClose} />
        <div className="flex-1 overflow-y-auto min-h-0 px-6 py-4">
          <h1 className="text-xl font-semibold break-all">
            {graphNode.label || graphNode.value || graphNode.id}
          </h1>
          <p className="text-xs text-muted-foreground mt-1">
            Graph node — <span className="font-medium">{graphNode.kind}</span>
            {graphNode.signal_kind && <> · {graphNode.signal_kind}</>}
          </p>
          <div className="mt-4 space-y-1 text-sm">
            {graphNode.value !== undefined && (
              <p>
                <span className="font-medium text-muted-foreground">Value: </span>
                {graphNode.value}
              </p>
            )}
            {graphNode.label && graphNode.kind !== 'Intent' && (
              <p>
                <span className="font-medium text-muted-foreground">Label: </span>
                {graphNode.label}
              </p>
            )}
            <p className="text-xs text-muted-foreground break-all mt-2">id: {graphNode.id}</p>
          </div>
        </div>
      </div>
    )
  }

  if (!detail) {
    return null
  }

  return (
    <div className="flex flex-col flex-1 min-h-0 overflow-hidden">
      <CloseHeader onClose={onClose} title={detail.name} />
      <div className="flex-1 overflow-y-auto min-h-0 px-6 py-4">
        {/* Name */}
        <h1 className="text-xl font-semibold break-words leading-tight">{detail.name}</h1>

        {/* Description */}
        {detail.description && (
          <p className="mt-2 text-sm text-muted-foreground leading-relaxed">
            {detail.description}
          </p>
        )}

        {/* Source path */}
        <div className="mt-3 text-xs text-muted-foreground border-l-2 border-border pl-3">
          <p className="break-all">
            <span className="font-medium">source: </span>
            <a
              href={`file://${detail.source_path}`}
              className="underline underline-offset-2 hover:text-foreground transition-colors"
              title="Open file (local dev)"
            >
              {detail.source_path}
            </a>
          </p>
        </div>

        {/* Body — markdown */}
        <div className="mt-6 prose prose-sm dark:prose-invert max-w-none
                        prose-headings:text-foreground prose-headings:font-semibold
                        prose-p:text-foreground prose-p:leading-relaxed
                        prose-li:text-foreground
                        prose-strong:text-foreground
                        prose-code:text-foreground prose-code:bg-muted
                        prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:before:content-none prose-code:after:content-none
                        prose-pre:bg-muted prose-pre:text-foreground prose-pre:border prose-pre:border-border
                        prose-a:text-blue-600 dark:prose-a:text-blue-400 prose-a:no-underline hover:prose-a:underline
                        prose-blockquote:border-border prose-blockquote:text-muted-foreground
                        prose-hr:border-border">
          <ReactMarkdown>{detail.body.replace(/<!--[\s\S]*?-->/g, '')}</ReactMarkdown>
        </div>
      </div>
    </div>
  )
}

// ── helper ──────────────────────────────────────────────────────────────────

function CloseHeader({ onClose, title }: { onClose?: () => void; title?: string }) {
  if (!onClose) return null
  return (
    <div className="flex items-center justify-between border-b border-border px-4 py-2 shrink-0 bg-background">
      <span className="text-xs uppercase tracking-wide text-muted-foreground truncate">
        {title || 'Detail'}
      </span>
      <button
        type="button"
        onClick={onClose}
        className="ml-2 inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
        title="Close panel"
        aria-label="Close detail panel"
      >
        ✕
      </button>
    </div>
  )
}
