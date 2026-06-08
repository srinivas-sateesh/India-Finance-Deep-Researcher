import { useEffect, useState } from 'react'
import { useSSE } from '../hooks/useSSE'
import type { SSEEvent, SubQuestion } from '../types'

interface Note {
  sub_question_id: string
  finding: string
  confidence: number
}

interface Evaluation {
  verdict: string
  source_quality: string
  guidance: string
}

interface Props {
  jobId: string
  onComplete: () => void
  onError: (msg: string) => void
}

const statusColors: Record<SubQuestion['status'], string> = {
  pending: 'bg-gray-700 text-gray-400',
  in_progress: 'bg-yellow-500/20 text-yellow-400 border border-yellow-500/30',
  answered: 'bg-green-500/20 text-green-400 border border-green-500/30',
  needs_more_research: 'bg-orange-500/20 text-orange-400 border border-orange-500/30',
  gap: 'bg-red-500/20 text-red-400 border border-red-500/30',
}

const statusLabel: Record<SubQuestion['status'], string> = {
  pending: 'Pending',
  in_progress: 'Researching',
  answered: 'Answered',
  needs_more_research: 'Needs more',
  gap: 'Gap',
}

const verdictColors: Record<string, string> = {
  continue: 'bg-yellow-500/20 text-yellow-400 border border-yellow-500/30',
  sufficient: 'bg-green-500/20 text-green-400 border border-green-500/30',
  insufficient_sources: 'bg-orange-500/20 text-orange-400 border border-orange-500/30',
}

function useElapsed() {
  const [seconds, setSeconds] = useState(0)
  useEffect(() => {
    const t = setInterval(() => setSeconds((s) => s + 1), 1000)
    return () => clearInterval(t)
  }, [])
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return m > 0 ? `${m}m ${s}s` : `${s}s`
}

export function ProgressView({ jobId, onComplete, onError }: Props) {
  const elapsed = useElapsed()
  const [iteration, setIteration] = useState(0)
  const [thesis, setThesis] = useState<string | null>(null)
  const [subQuestions, setSubQuestions] = useState<SubQuestion[]>([])
  const [notes, setNotes] = useState<Note[]>([])
  const [evaluations, setEvaluations] = useState<Evaluation[]>([])

  useSSE(jobId, (event: SSEEvent) => {
    switch (event.type) {
      case 'iteration':
        setIteration(event.iteration)
        break
      case 'plan_created':
        setThesis(event.thesis)
        setSubQuestions(event.sub_questions)
        break
      case 'sub_question_updated':
        setSubQuestions((prev) =>
          prev.map((sq) => sq.id === event.id ? { ...sq, status: event.status } : sq)
        )
        break
      case 'note_recorded':
        setNotes((prev) => [
          { sub_question_id: event.sub_question_id, finding: event.finding, confidence: event.confidence },
          ...prev,
        ].slice(0, 50))
        break
      case 'evaluation':
        setEvaluations((prev) => [
          { verdict: event.verdict, source_quality: event.source_quality, guidance: event.guidance },
          ...prev,
        ])
        break
      case 'done':
        onComplete()
        break
      case 'error':
        onError(event.error)
        break
    }
  })

  const answeredCount = subQuestions.filter((sq) => sq.status === 'answered').length

  return (
    <div className="min-h-screen bg-gray-950 px-4 py-10">
      <div className="max-w-3xl mx-auto space-y-6">

        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="w-2.5 h-2.5 rounded-full bg-violet-400 animate-pulse" />
            <span className="text-white font-medium">Researching…</span>
          </div>
          <div className="flex items-center gap-4 text-sm text-gray-400">
            <span>Iteration <span className="text-white font-mono">{iteration}</span></span>
            <span>Elapsed <span className="text-white font-mono">{elapsed}</span></span>
          </div>
        </div>

        {/* Thesis */}
        {thesis && (
          <div className="bg-gray-900 border border-gray-800 rounded-xl px-5 py-4">
            <p className="text-xs font-medium text-violet-400 uppercase tracking-wider mb-1.5">Research Thesis</p>
            <p className="text-gray-200 text-sm leading-relaxed">{thesis}</p>
          </div>
        )}

        {/* Sub-questions */}
        {subQuestions.length > 0 && (
          <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
            <div className="px-5 py-3.5 border-b border-gray-800 flex items-center justify-between">
              <p className="text-sm font-medium text-white">Research Plan</p>
              <span className="text-xs text-gray-400">{answeredCount} / {subQuestions.length} answered</span>
            </div>
            <div className="divide-y divide-gray-800">
              {subQuestions.map((sq) => (
                <div key={sq.id} className="px-5 py-3.5 flex items-start gap-3">
                  <span className="text-xs font-mono text-gray-500 mt-0.5 shrink-0">{sq.id}</span>
                  <p className="text-sm text-gray-300 flex-1 leading-relaxed">{sq.question}</p>
                  <span className={`text-xs font-medium px-2 py-0.5 rounded-full shrink-0 ${statusColors[sq.status]}`}>
                    {statusLabel[sq.status]}
                  </span>
                </div>
              ))}
            </div>
            {/* Progress bar */}
            <div className="h-1 bg-gray-800">
              <div
                className="h-full bg-violet-500 transition-all duration-500"
                style={{ width: subQuestions.length ? `${(answeredCount / subQuestions.length) * 100}%` : '0%' }}
              />
            </div>
          </div>
        )}

        {/* Evaluations */}
        {evaluations.length > 0 && (
          <div className="space-y-2">
            <p className="text-xs font-medium text-gray-500 uppercase tracking-wider">Evaluations</p>
            {evaluations.map((ev, i) => (
              <div key={i} className="bg-gray-900 border border-gray-800 rounded-xl px-5 py-3.5">
                <div className="flex items-center gap-2 mb-2">
                  <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${verdictColors[ev.verdict] ?? 'bg-gray-700 text-gray-400'}`}>
                    {ev.verdict}
                  </span>
                  <span className="text-xs text-gray-500">source quality: {ev.source_quality}</span>
                </div>
                <p className="text-sm text-gray-400 leading-relaxed">{ev.guidance}</p>
              </div>
            ))}
          </div>
        )}

        {/* Notes feed */}
        {notes.length > 0 && (
          <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
            <div className="px-5 py-3.5 border-b border-gray-800">
              <p className="text-sm font-medium text-white">Live Notes <span className="text-gray-500 font-normal">({notes.length})</span></p>
            </div>
            <div className="divide-y divide-gray-800 max-h-72 overflow-y-auto">
              {notes.map((note, i) => (
                <div key={i} className="px-5 py-3 flex items-start gap-3">
                  <span className="text-xs font-mono text-violet-400 mt-0.5 shrink-0">{note.sub_question_id}</span>
                  <p className="text-sm text-gray-400 leading-relaxed flex-1 line-clamp-2">{note.finding}</p>
                  <span className="text-xs text-gray-600 shrink-0">{Math.round(note.confidence * 100)}%</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Skeleton when nothing yet */}
        {subQuestions.length === 0 && (
          <div className="space-y-3">
            {[80, 60, 90, 50].map((w, i) => (
              <div key={i} className="h-4 bg-gray-800 rounded-full animate-pulse" style={{ width: `${w}%` }} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
