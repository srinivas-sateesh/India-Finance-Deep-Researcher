import { useQuery } from '@tanstack/react-query'
import type { JobStatus, JobSummary, ResearchReport } from '../types'

async function fetchStatus(jobId: string): Promise<JobStatus> {
  const res = await fetch(`/research/${jobId}`)
  if (!res.ok) throw new Error('Failed to fetch job status')
  return res.json()
}

async function fetchResult(jobId: string): Promise<ResearchReport> {
  const res = await fetch(`/research/${jobId}/result`)
  if (res.status === 202) throw new Error('still_running')
  if (!res.ok) throw new Error('Failed to fetch result')
  return res.json()
}

export function useJobStatus(jobId: string | null) {
  return useQuery({
    queryKey: ['job-status', jobId],
    queryFn: () => fetchStatus(jobId!),
    enabled: !!jobId,
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status === 'running' ? 5000 : false
    },
  })
}

export function useJobResult(jobId: string | null, enabled: boolean) {
  return useQuery({
    queryKey: ['job-result', jobId],
    queryFn: () => fetchResult(jobId!),
    enabled: !!jobId && enabled,
    retry: false,
  })
}

async function fetchHistory(): Promise<{ jobs: JobSummary[] }> {
  const res = await fetch('/research')
  if (!res.ok) throw new Error('Failed to fetch research history')
  return res.json()
}

export function useResearchHistory() {
  return useQuery({
    queryKey: ['research-history'],
    queryFn: fetchHistory,
    refetchInterval: 15000,
  })
}
