import { useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { HistoryView } from './components/HistoryView'
import { SubmitView } from './components/SubmitView'
import { ProgressView } from './components/ProgressView'
import { ReportView } from './components/ReportView'
import { useJobResult } from './hooks/useJob'
import type { ResearchReport } from './types'

const queryClient = new QueryClient()

type Phase = 'history' | 'submit' | 'progress' | 'report'

function Research() {
  const [phase, setPhase] = useState<Phase>('history')
  const [jobId, setJobId] = useState<string | null>(null)
  const [report, setReport] = useState<ResearchReport | null>(null)

  const { refetch: fetchReport } = useJobResult(jobId, false)

  function handleSubmit(id: string) {
    setJobId(id)
    setPhase('progress')
    queryClient.invalidateQueries({ queryKey: ['research-history'] })
  }

  async function handleComplete() {
    if (!jobId) return
    const result = await fetchReport()
    if (result.data) {
      setReport(result.data)
      setPhase('report')
      queryClient.invalidateQueries({ queryKey: ['research-history'] })
    }
  }

  function handleError(msg: string) {
    console.error('Research job error:', msg)
    setPhase('history')
    setJobId(null)
    queryClient.invalidateQueries({ queryKey: ['research-history'] })
  }

  async function handleViewReport(id: string) {
    setJobId(id)
    setReport(null)
    // Fetch result directly then show report view
    try {
      const res = await fetch(`/research/${id}/result`)
      if (!res.ok) throw new Error('Failed to fetch report')
      const data: ResearchReport = await res.json()
      setReport(data)
      setPhase('report')
    } catch (err) {
      console.error('Failed to load report:', err)
    }
  }

  function handleWatchProgress(id: string) {
    setJobId(id)
    setPhase('progress')
  }

  function handleHistory() {
    setPhase('history')
    setJobId(null)
    setReport(null)
    queryClient.invalidateQueries({ queryKey: ['research-history'] })
  }

  function handleNewResearch() {
    setPhase('submit')
    setJobId(null)
    setReport(null)
  }

  if (phase === 'history') {
    return (
      <HistoryView
        onNewResearch={handleNewResearch}
        onViewReport={handleViewReport}
        onWatchProgress={handleWatchProgress}
      />
    )
  }

  if (phase === 'submit') {
    return <SubmitView onSubmit={handleSubmit} onHistory={handleHistory} />
  }

  if (phase === 'progress' && jobId) {
    return (
      <ProgressView
        jobId={jobId}
        onComplete={handleComplete}
        onError={handleError}
      />
    )
  }

  if (phase === 'report' && jobId && report) {
    return (
      <ReportView
        jobId={jobId}
        report={report}
        onReset={handleNewResearch}
        onHistory={handleHistory}
      />
    )
  }

  return null
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <Research />
    </QueryClientProvider>
  )
}
