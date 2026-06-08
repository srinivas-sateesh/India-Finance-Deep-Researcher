export interface SubQuestion {
  id: string
  question: string
  priority: number
  status: 'pending' | 'in_progress' | 'answered' | 'needs_more_research' | 'gap'
}

export interface Finding {
  sub_question_id: string
  sub_question: string
  answer: string
  evidence: string[]
  sources: string[]
  confidence: 'high' | 'medium' | 'low'
  conflicting_evidence: string[]
}

export interface ResearchReport {
  title: string
  executive_summary: string
  thesis: string
  findings: Finding[]
  cross_cutting_themes: string[]
  limitations: string[]
  conclusion: string
  overall_confidence: 'high' | 'medium' | 'low'
}

export interface JobStatus {
  status: 'running' | 'completed' | 'failed'
  iteration: number
  error?: string
}

export interface JobSummary {
  job_id: string
  question: string
  status: 'running' | 'completed' | 'failed'
  iteration: number
  title?: string
  overall_confidence?: 'high' | 'medium' | 'low'
  error?: string
  created_at: string
  updated_at: string
}

// SSE event types
export type SSEEvent =
  | { type: 'iteration'; iteration: number }
  | { type: 'plan_created'; thesis: string; sub_questions: SubQuestion[] }
  | { type: 'sub_question_updated'; id: string; question: string; status: SubQuestion['status'] }
  | { type: 'note_recorded'; sub_question_id: string; finding: string; confidence: number }
  | { type: 'evaluation'; verdict: 'continue' | 'sufficient' | 'insufficient_sources'; source_quality: string; guidance: string }
  | { type: 'done'; status: string }
  | { type: 'error'; status: string; error: string }
