import type { ResearchReport } from '../types'

const WB_CODES: Record<string, string> = {
  repo_rate:          'FR.INR.LEND',
  cpi:                'FP.CPI.TOTL.ZG',
  wpi:                'NY.GDP.DEFL.KD.ZG',
  bank_credit_growth: 'FS.AST.PRVT.GD.ZS',
  bank_deposits:      'FS.AST.PRVT.GD.ZS',
  forex_reserves:     'FI.RES.TOTL.CD',
  npa_ratio:          'FB.AST.NPER.ZS',
  gdp_growth:         'NY.GDP.MKTP.KD.ZG',
}

function sourceUrl(id: string): string {
  const parts = id.split(':')
  const provider = parts[0]
  if (provider === 'RBI') {
    const code = WB_CODES[parts[1]]
    return code
      ? `https://data.worldbank.org/indicator/${code}?locations=IN`
      : 'https://data.worldbank.org/?locations=IN'
  }
  if (provider === 'BSE') {
    const ticker = parts[1] ?? ''
    const type   = parts[2] ?? 'results'
    if (type === 'shareholding')  return `https://finance.yahoo.com/quote/${ticker}/holders/`
    if (type === 'announcements') return `https://finance.yahoo.com/quote/${ticker}/news/`
    return `https://finance.yahoo.com/quote/${ticker}/financials/`
  }
  if (provider === 'SEBI') return 'https://www.nseindia.com/companies-listing/corporate-filings-insider-trading'
  try { return new URL(id).href } catch { return '#' }
}

function sourceLabel(id: string): string {
  const parts = id.split(':')
  if (parts[0] === 'RBI') return `RBI · ${parts[1]}`
  if (parts[0] === 'BSE') return `BSE · ${parts[1]} · ${parts[2]}`
  if (parts[0] === 'SEBI') return `SEBI · ${parts[1]}`
  try { return new URL(id).hostname } catch { return id }
}

const CITE_RE = /\[([A-Z]+:[^\]]+)\]/g

function CitedText({ text }: { text: string }) {
  const parts: React.ReactNode[] = []
  let last = 0
  let m: RegExpExecArray | null
  CITE_RE.lastIndex = 0
  while ((m = CITE_RE.exec(text)) !== null) {
    if (m.index > last) parts.push(text.slice(last, m.index))
    const id = m[1]
    parts.push(
      <a
        key={m.index}
        href={sourceUrl(id)}
        target="_blank"
        rel="noreferrer"
        title={id}
        className="inline-block text-[10px] font-semibold text-violet-400 bg-violet-500/10 border border-violet-500/25 rounded px-1 py-0 mx-0.5 align-super leading-none hover:bg-violet-500/20 transition-colors"
      >
        {id}
      </a>
    )
    last = m.index + m[0].length
  }
  if (last < text.length) parts.push(text.slice(last))
  return <>{parts}</>
}

interface Props {
  jobId: string
  report: ResearchReport
  onReset: () => void
  onHistory: () => void
}

const confidenceStyles = {
  high: 'bg-green-500/15 text-green-400 border border-green-500/30',
  medium: 'bg-yellow-500/15 text-yellow-400 border border-yellow-500/30',
  low: 'bg-red-500/15 text-red-400 border border-red-500/30',
}

export function ReportView({ jobId, report, onReset, onHistory }: Props) {
  return (
    <div className="min-h-screen bg-gray-950 px-4 py-10">
      <div className="max-w-3xl mx-auto space-y-8">

        {/* Header bar */}
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div className="flex items-center gap-4">
            <button
              onClick={onHistory}
              className="text-sm text-gray-400 hover:text-white transition-colors flex items-center gap-1.5"
            >
              ← All research
            </button>
            <button
              onClick={onReset}
              className="text-sm text-violet-400 hover:text-violet-300 transition-colors"
            >
              + New research
            </button>
          </div>
          <div className="flex items-center gap-3">
            <span className={`text-xs font-medium px-2.5 py-1 rounded-full ${confidenceStyles[report.overall_confidence]}`}>
              {report.overall_confidence} confidence
            </span>
            <a
              href={`/research/${jobId}/pdf`}
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-2 bg-violet-600 hover:bg-violet-500 text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
              </svg>
              Save as PDF
            </a>
          </div>
        </div>

        {/* Title + executive summary */}
        <div>
          <h1 className="text-2xl font-semibold text-white leading-snug mb-4">{report.title}</h1>
          <div className="bg-gray-900 border border-gray-800 rounded-xl px-6 py-5">
            <p className="text-xs font-medium text-violet-400 uppercase tracking-wider mb-2">Executive Summary</p>
            <p className="text-gray-300 text-sm leading-relaxed">{report.executive_summary}</p>
            <p className="text-gray-500 text-sm mt-3 italic">"{report.thesis}"</p>
          </div>
        </div>

        {/* Findings */}
        <div className="space-y-4">
          <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wider">Findings</h2>
          {report.findings.map((finding, i) => (
            <div key={i} className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
              <div className="px-6 py-4 border-b border-gray-800 flex items-start justify-between gap-3">
                <p className="text-sm font-medium text-white leading-snug">{finding.sub_question}</p>
                <span className={`text-xs font-medium px-2 py-0.5 rounded-full shrink-0 ${confidenceStyles[finding.confidence]}`}>
                  {finding.confidence}
                </span>
              </div>
              <div className="px-6 py-4 space-y-3">
                <p className="text-sm text-gray-300 leading-relaxed">
                  <CitedText text={finding.answer} />
                </p>

                {finding.evidence.length > 0 && (
                  <div>
                    <p className="text-xs font-medium text-gray-500 mb-1.5">Evidence</p>
                    <ul className="space-y-1">
                      {finding.evidence.map((e, j) => (
                        <li key={j} className="text-xs text-gray-400 flex gap-2">
                          <span className="text-violet-500 shrink-0">–</span>
                          <span><CitedText text={e} /></span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {finding.conflicting_evidence.length > 0 && (
                  <div className="bg-orange-500/5 border border-orange-500/20 rounded-lg px-4 py-3">
                    <p className="text-xs font-medium text-orange-400 mb-1.5">Conflicting evidence</p>
                    {finding.conflicting_evidence.map((c, j) => (
                      <p key={j} className="text-xs text-gray-400">{c}</p>
                    ))}
                  </div>
                )}

                {finding.sources.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 pt-1">
                    {finding.sources.map((src, j) => (
                      <a
                        key={j}
                        href={sourceUrl(src)}
                        target="_blank"
                        rel="noreferrer"
                        className="text-xs text-violet-400 hover:text-violet-300 bg-violet-500/10 px-2 py-0.5 rounded truncate max-w-xs transition-colors"
                      >
                        {sourceLabel(src)}
                      </a>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>

        {/* Cross-cutting themes */}
        {report.cross_cutting_themes.length > 0 && (
          <div className="bg-gray-900 border border-gray-800 rounded-xl px-6 py-5">
            <p className="text-xs font-medium text-violet-400 uppercase tracking-wider mb-3">Cross-cutting Themes</p>
            <ul className="space-y-2">
              {report.cross_cutting_themes.map((theme, i) => (
                <li key={i} className="text-sm text-gray-300 flex gap-2">
                  <span className="text-violet-500 shrink-0">→</span>
                  <span>{theme}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Conclusion */}
        <div className="bg-gray-900 border border-gray-800 rounded-xl px-6 py-5">
          <p className="text-xs font-medium text-violet-400 uppercase tracking-wider mb-3">Conclusion</p>
          <div className="space-y-3">
            {report.conclusion.split('\n').filter(l => l.trim()).map((para, i) => (
              <p key={i} className="text-gray-300 text-sm leading-relaxed">
                <CitedText text={para} />
              </p>
            ))}
          </div>
        </div>

        {/* Limitations */}
        {report.limitations.length > 0 && (
          <div className="bg-gray-900 border border-gray-800 rounded-xl px-6 py-5">
            <p className="text-xs font-medium text-gray-500 uppercase tracking-wider mb-3">Limitations</p>
            <ul className="space-y-1.5">
              {report.limitations.map((lim, i) => (
                <li key={i} className="text-sm text-gray-400 flex gap-2">
                  <span className="text-gray-600 shrink-0">·</span>
                  <span>{lim}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        <p className="text-xs text-gray-700 text-center pb-4">Job ID: {jobId}</p>
      </div>
    </div>
  )
}
