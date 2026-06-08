import { useState } from 'react'

interface Props {
  onSubmit: (jobId: string) => void
  onHistory?: () => void
}

export function SubmitView({ onSubmit, onHistory }: Props) {
  const [question, setQuestion] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!question.trim()) return
    setLoading(true)
    setError(null)
    try {
      const res = await fetch('/research', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: question.trim() }),
      })
      if (!res.ok) throw new Error('Failed to start research job')
      const { job_id } = await res.json()
      onSubmit(job_id)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong')
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-gray-950 flex items-center justify-center px-4">
      {onHistory && (
        <button
          onClick={onHistory}
          className="absolute top-6 left-6 text-sm text-gray-500 hover:text-white transition-colors flex items-center gap-1.5"
        >
          ← All research
        </button>
      )}
      <div className="w-full max-w-2xl">
        <div className="text-center mb-10">
          <div className="inline-flex items-center gap-2 bg-violet-500/10 border border-violet-500/20 rounded-full px-4 py-1.5 text-violet-400 text-sm font-medium mb-6">
            <span className="w-1.5 h-1.5 rounded-full bg-violet-400 animate-pulse" />
            India Finance Research
          </div>
          <h1 className="text-4xl font-semibold text-white tracking-tight mb-3">
            What do you want to research?
          </h1>
          <p className="text-gray-400 text-base">
            The agent will plan, search, evaluate, and synthesise a full report — usually in 10–20 minutes.
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <textarea
            className="w-full bg-gray-900 border border-gray-700 rounded-xl px-5 py-4 text-white text-base placeholder-gray-500 resize-none focus:outline-none focus:border-violet-500 focus:ring-1 focus:ring-violet-500 transition-colors"
            rows={4}
            placeholder="e.g. What are the best programming languages for AI in 2026?"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            disabled={loading}
          />

          {error && (
            <p className="text-red-400 text-sm">{error}</p>
          )}

          <button
            type="submit"
            disabled={loading || !question.trim()}
            className="w-full bg-violet-600 hover:bg-violet-500 disabled:bg-gray-700 disabled:text-gray-500 text-white font-medium rounded-xl px-6 py-3.5 text-base transition-colors flex items-center justify-center gap-2"
          >
            {loading ? (
              <>
                <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                Starting research…
              </>
            ) : (
              'Start Research'
            )}
          </button>
        </form>
      </div>
    </div>
  )
}
