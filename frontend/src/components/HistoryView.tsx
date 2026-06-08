import type { JobSummary } from '../types'
import { useResearchHistory } from '../hooks/useJob'

interface Props {
  onNewResearch: () => void
  onViewReport: (jobId: string) => void
  onWatchProgress: (jobId: string) => void
}

const statusStyles = {
  completed: 'bg-green-500/15 text-green-400 border border-green-500/30',
  running: 'bg-violet-500/15 text-violet-400 border border-violet-500/30',
  failed: 'bg-red-500/15 text-red-400 border border-red-500/30',
}

const confidenceStyles = {
  high: 'bg-green-500/15 text-green-400 border border-green-500/30',
  medium: 'bg-yellow-500/15 text-yellow-400 border border-yellow-500/30',
  low: 'bg-red-500/15 text-red-400 border border-red-500/30',
}

function formatDate(iso: string): string {
  const d = new Date(iso)
  const now = new Date()
  const diffMs = now.getTime() - d.getTime()
  const diffMins = Math.floor(diffMs / 60000)
  const diffHours = Math.floor(diffMins / 60)
  const diffDays = Math.floor(diffHours / 24)

  if (diffMins < 1) return 'just now'
  if (diffMins < 60) return `${diffMins}m ago`
  if (diffHours < 24) return `${diffHours}h ago`
  if (diffDays < 7) return `${diffDays}d ago`
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

function JobCard({ job, onViewReport, onWatchProgress }: {
  job: JobSummary
  onViewReport: (id: string) => void
  onWatchProgress: (id: string) => void
}) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl p-5 flex flex-col gap-3 hover:border-gray-700 transition-colors">
      <div className="flex items-start justify-between gap-3">
        <span className={`text-xs font-medium px-2.5 py-1 rounded-full shrink-0 ${statusStyles[job.status]}`}>
          {job.status === 'running' ? (
            <span className="flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-violet-400 animate-pulse" />
              running · iter {job.iteration}
            </span>
          ) : job.status}
        </span>
        <span className="text-xs text-gray-600 shrink-0">{formatDate(job.created_at)}</span>
      </div>

      <div className="space-y-1">
        <p className="text-sm text-white font-medium leading-snug line-clamp-2">{job.question}</p>
        {job.title && job.title !== job.question && (
          <p className="text-xs text-gray-500 line-clamp-1">{job.title}</p>
        )}
        {job.status === 'failed' && job.error && (
          <p className="text-xs text-red-400 line-clamp-2">Error: {job.error}</p>
        )}
      </div>

      <div className="flex items-center justify-between gap-2 pt-1">
        <div>
          {job.overall_confidence && (
            <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${confidenceStyles[job.overall_confidence]}`}>
              {job.overall_confidence} confidence
            </span>
          )}
        </div>

        <div className="flex items-center gap-2">
          {job.status === 'completed' && (
            <>
              <a
                href={`/research/${job.job_id}/pdf`}
                target="_blank"
                rel="noreferrer"
                className="flex items-center gap-1.5 text-xs text-gray-400 hover:text-white border border-gray-700 hover:border-gray-500 px-3 py-1.5 rounded-lg transition-colors"
                title="Open PDF view"
              >
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                </svg>
                PDF
              </a>
              <button
                onClick={() => onViewReport(job.job_id)}
                className="text-xs text-white bg-violet-600 hover:bg-violet-500 px-3 py-1.5 rounded-lg transition-colors font-medium"
              >
                View Report
              </button>
            </>
          )}
          {job.status === 'running' && (
            <button
              onClick={() => onWatchProgress(job.job_id)}
              className="text-xs text-white bg-violet-600 hover:bg-violet-500 px-3 py-1.5 rounded-lg transition-colors font-medium flex items-center gap-1.5"
            >
              <span className="w-1.5 h-1.5 rounded-full bg-white animate-pulse" />
              Watch Progress
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

export function HistoryView({ onNewResearch, onViewReport, onWatchProgress }: Props) {
  const { data, isLoading, error, refetch } = useResearchHistory()
  const jobs = data?.jobs ?? []

  return (
    <div className="min-h-screen bg-gray-950 px-4 py-10">
      <div className="max-w-3xl mx-auto">
        <div className="flex items-center justify-between mb-8">
          <div>
            <div className="inline-flex items-center gap-2 bg-violet-500/10 border border-violet-500/20 rounded-full px-3 py-1 text-violet-400 text-xs font-medium mb-3">
              <span className="w-1.5 h-1.5 rounded-full bg-violet-400" />
              India Finance Research
            </div>
            <h1 className="text-2xl font-semibold text-white tracking-tight">Research History</h1>
            <p className="text-gray-500 text-sm mt-1">All your past and ongoing research reports</p>
          </div>
          <button
            onClick={onNewResearch}
            className="bg-violet-600 hover:bg-violet-500 text-white text-sm font-medium px-4 py-2.5 rounded-xl transition-colors flex items-center gap-2"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
            New Research
          </button>
        </div>

        {isLoading && (
          <div className="flex items-center justify-center py-20 text-gray-500">
            <span className="w-5 h-5 border-2 border-gray-700 border-t-violet-500 rounded-full animate-spin mr-3" />
            Loading…
          </div>
        )}

        {error && (
          <div className="bg-red-500/10 border border-red-500/20 rounded-xl px-5 py-4 flex items-center justify-between">
            <p className="text-sm text-red-400">Failed to load research history.</p>
            <button onClick={() => refetch()} className="text-sm text-red-400 hover:text-red-300 underline">Retry</button>
          </div>
        )}

        {!isLoading && !error && jobs.length === 0 && (
          <div className="text-center py-24 text-gray-600">
            <svg className="w-12 h-12 mx-auto mb-4 opacity-30" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
            <p className="text-base font-medium text-gray-500">No research yet</p>
            <p className="text-sm mt-1 text-gray-600">Submit a topic and the agent will build a full report.</p>
            <button
              onClick={onNewResearch}
              className="mt-6 bg-violet-600 hover:bg-violet-500 text-white text-sm font-medium px-5 py-2.5 rounded-xl transition-colors"
            >
              Start your first research
            </button>
          </div>
        )}

        {jobs.length > 0 && (
          <div className="grid gap-3">
            {jobs.map(job => (
              <JobCard
                key={job.job_id}
                job={job}
                onViewReport={onViewReport}
                onWatchProgress={onWatchProgress}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
