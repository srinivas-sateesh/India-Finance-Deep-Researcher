import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

import markdown as md_lib
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

load_dotenv()

from app import db
from app.agents.graph import build_graph
from app.log_context import bind_job_logger, get_logger, unbind_job_logger
from app.llm.synthesize import render_to_markdown, synthesize_report

LOG_DIR = Path(__file__).parent.parent.parent / "logs"
OUTPUT_DIR = Path(__file__).parent.parent.parent / "output"

logger = get_logger()


# ---------------------------------------------------------------------------
# SSE broadcaster
# ---------------------------------------------------------------------------

class _EventBroadcaster:
    """Fan-out broadcaster for per-job SSE events.

    New subscribers receive the full event history so reconnecting clients
    and late joiners see everything from the start of the run.
    Removed from _broadcasters when the job finishes; SSE clients that
    connect after removal get the terminal state directly from SQLite.
    """

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue] = []
        self._history: list[dict] = []
        self._done: bool = False

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        for event in self._history:
            q.put_nowait(event)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    async def broadcast(self, event: dict) -> None:
        self._history.append(event)
        for q in self._subscribers:
            await q.put(event)

    @property
    def done(self) -> bool:
        return self._done

    def mark_done(self) -> None:
        self._done = True


# Active-job broadcasters (in-memory; cleared when each job finishes)
_broadcasters: dict[str, _EventBroadcaster] = {}


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init()
    yield
    await db.close()


app = FastAPI(title="Deep Research API", lifespan=lifespan)


class ResearchRequest(BaseModel):
    question: str


# ---------------------------------------------------------------------------
# Job worker
# ---------------------------------------------------------------------------

async def _run_research(job_id: str, question: str) -> None:
    """Async job worker.

    Streams the graph via astream(), derives SSE events by diffing consecutive
    state snapshots, broadcasts them to subscribers, and persists status changes
    to SQLite. Per-job log isolation is handled by the ContextVar logger.
    """
    broadcaster = _broadcasters[job_id]
    bind_job_logger(job_id, LOG_DIR)
    await db.update_job(job_id, log_file=str(LOG_DIR / f"{job_id}.log"))

    started_at = datetime.now(timezone.utc).isoformat()
    logger.info(f"\n==== job {job_id} started at {started_at} ====")
    logger.info(f"  question: {question}")

    try:
        graph = build_graph()
        initial_state = {
            "messages": [HumanMessage(content=question)],
            "notes": [],
            "research_plan": None,
            "evaluation_history": [],
            "iteration_count": 0,
        }

        final_state = None
        prev_notes = 0
        prev_evals = 0
        prev_plan = None
        last_iteration = 0

        async for snapshot in graph.astream(initial_state, stream_mode="values"):
            final_state = snapshot
            iteration = snapshot.get("iteration_count", 0)

            if iteration != last_iteration:
                last_iteration = iteration
                await db.update_job(job_id, iteration=iteration)
                await broadcaster.broadcast({"type": "iteration", "iteration": iteration})

            plan = snapshot.get("research_plan")
            if plan is not None and prev_plan is None:
                await broadcaster.broadcast({
                    "type": "plan_created",
                    "thesis": plan["thesis"],
                    "sub_questions": [
                        {"id": sq["id"], "question": sq["question"], "priority": sq["priority"]}
                        for sq in plan["sub_questions"]
                    ],
                })
            elif plan is not None and prev_plan is not None:
                old_statuses = {sq["id"]: sq["status"] for sq in prev_plan["sub_questions"]}
                for sq in plan["sub_questions"]:
                    if old_statuses.get(sq["id"]) != sq["status"]:
                        await broadcaster.broadcast({
                            "type": "sub_question_updated",
                            "id": sq["id"],
                            "question": sq["question"],
                            "status": sq["status"],
                        })
            prev_plan = plan

            notes = snapshot.get("notes", [])
            for note in notes[prev_notes:]:
                await broadcaster.broadcast({
                    "type": "note_recorded",
                    "sub_question_id": note["sub_question_id"],
                    "finding": note["finding"][:300],
                    "confidence": note["confidence"],
                })
            prev_notes = len(notes)

            evals = snapshot.get("evaluation_history", [])
            for ev in evals[prev_evals:]:
                await broadcaster.broadcast({
                    "type": "evaluation",
                    "verdict": ev["verdict"],
                    "source_quality": ev["source_quality"],
                    "guidance": ev["guidance"],
                })
            prev_evals = len(evals)

        if final_state is None:
            raise RuntimeError("Graph produced no output")

        notes = final_state.get("notes", [])
        plan = final_state.get("research_plan")
        eval_history = final_state.get("evaluation_history", [])

        if plan and notes:
            logger.info(f"\n  job {job_id}: running post-graph synthesis")
            report = await synthesize_report(notes, plan, eval_history)
            output_content = render_to_markdown(report)
            logger.info(
                f"  job {job_id}: synthesis complete "
                f"({len(report.findings)} findings, confidence={report.overall_confidence})"
            )
            OUTPUT_DIR.mkdir(exist_ok=True)
            output_file = OUTPUT_DIR / f"{job_id}.md"
            output_file.write_text(output_content, encoding="utf-8")
            await db.update_job(
                job_id,
                status="completed",
                result=report.model_dump(),
                output_file=str(output_file),
            )
        else:
            logger.warning(f"  job {job_id}: no plan or notes — no structured result")
            await db.update_job(job_id, status="completed")

        logger.info(f"  job {job_id}: completed")
        await broadcaster.broadcast({"type": "done", "status": "completed"})
        broadcaster.mark_done()

    except Exception as exc:
        error_msg = f"{type(exc).__name__}: {exc}"
        logger.error(f"  job {job_id}: failed — {error_msg}")
        await db.update_job(job_id, status="failed", error=error_msg)
        await broadcaster.broadcast({"type": "error", "status": "failed", "error": error_msg})
        broadcaster.mark_done()
    finally:
        unbind_job_logger()
        _broadcasters.pop(job_id, None)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/research")
async def list_jobs() -> dict:
    """List all research jobs, newest first (up to 100)."""
    async with db._conn.execute(
        "SELECT id, question, status, iteration, result, error, created_at, updated_at"
        " FROM jobs ORDER BY created_at DESC LIMIT 100"
    ) as cur:
        rows = await cur.fetchall()

    jobs = []
    for row in rows:
        d = dict(row)
        result = None
        if d.get("result"):
            try:
                result = json.loads(d["result"])
            except Exception:
                pass
        jobs.append({
            "job_id": d["id"],
            "question": d["question"],
            "status": d["status"],
            "iteration": d["iteration"],
            "title": result.get("title") if result else None,
            "overall_confidence": result.get("overall_confidence") if result else None,
            "error": d.get("error"),
            "created_at": d["created_at"],
            "updated_at": d["updated_at"],
        })
    return {"jobs": jobs}


@app.post("/research", status_code=202)
async def start_research(body: ResearchRequest) -> dict:
    """Start a research job. Returns a job_id immediately; research runs as an async task."""
    job_id = str(uuid.uuid4())
    _broadcasters[job_id] = _EventBroadcaster()
    await db.create_job(job_id, body.question)
    asyncio.create_task(_run_research(job_id, body.question))
    return {"job_id": job_id}


@app.get("/research/{job_id}")
async def get_job_status(job_id: str) -> dict:
    """Poll a job's status and current iteration count."""
    job = await db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    response: dict = {"status": job["status"], "iteration": job["iteration"]}
    if job["status"] == "failed" and job.get("error"):
        response["error"] = job["error"]
    return response


@app.get("/research/{job_id}/result")
async def get_job_result(job_id: str) -> dict:
    """Retrieve the full ResearchReport JSON once the job is completed."""
    job = await db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] == "running":
        raise HTTPException(status_code=202, detail="Job still running")
    if job["status"] == "failed":
        raise HTTPException(status_code=500, detail=job.get("error", "Job failed"))
    if not job.get("result"):
        raise HTTPException(status_code=500, detail="Job completed but produced no structured result")
    return job["result"]


@app.get("/research/{job_id}/stream")
async def stream_job(job_id: str) -> StreamingResponse:
    """Stream job progress as Server-Sent Events.

    Event types emitted during a run:
      iteration          — agent turn completed; carries current iteration count
      plan_created       — research plan first set; carries thesis + sub-questions
      sub_question_updated — a sub-question changed status (e.g. → answered)
      note_recorded      — a finding was saved; carries sub_question_id + snippet
      evaluation         — evaluator fired; carries verdict + guidance
      done               — job completed successfully (terminal)
      error              — job failed (terminal); carries error message

    New subscribers receive the full event history, so a client that
    reconnects after a disconnect replays everything from the start.
    Clients that connect after the job finishes get the terminal event
    immediately from SQLite.

    A SSE comment (": heartbeat") is sent every 15 s of inactivity to
    keep the connection alive through proxies.
    """
    job = await db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    broadcaster = _broadcasters.get(job_id)

    async def event_stream():
        # Job already finished (or orphaned by a restart) — no live broadcaster
        if broadcaster is None or broadcaster.done:
            status = job["status"]
            if status == "completed":
                terminal = {"type": "done", "status": "completed"}
            else:
                terminal = {"type": "error", "status": status, "error": job.get("error", "")}
            yield f"data: {json.dumps(terminal)}\n\n"
            return

        # Job is running — subscribe to get history replay + live events
        queue = broadcaster.subscribe()
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {json.dumps(event)}\n\n"
                    if event["type"] in ("done", "error"):
                        break
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            broadcaster.unsubscribe(queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


MAX_ITERATIONS = 40  # mirrors graph.py — used by /metrics


def _dir_size_mb(path: Path) -> float:
    if not path.exists():
        return 0.0
    total = 0
    for dirpath, _, filenames in os.walk(path):
        # Cap traversal depth at 2 to avoid slow deep trees
        depth = len(Path(dirpath).relative_to(path).parts)
        if depth > 2:
            continue
        for fname in filenames:
            try:
                total += (Path(dirpath) / fname).stat().st_size
            except OSError:
                pass
    return round(total / 1_048_576, 1)


@app.get("/health")
async def health_check() -> dict:
    """Liveness + DB reachability. Always returns 200 with a structured body."""
    db_status = "ok"
    try:
        async with db._conn.execute("SELECT 1"):
            pass
    except Exception as exc:
        db_status = f"error: {exc}"

    active_jobs = len(_broadcasters)
    return {
        "status": "ok" if db_status == "ok" else "degraded",
        "db": db_status,
        "active_jobs": active_jobs,
        "broadcaster_count": active_jobs,
    }


@app.get("/metrics")
async def get_metrics() -> dict:
    """Job statistics for external monitoring. Requires DB connection."""
    now = datetime.now(timezone.utc)
    cutoff_1h = (now - timedelta(hours=1)).isoformat()
    cutoff_30min = (now - timedelta(minutes=30)).isoformat()

    try:
        async with db._conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE status = 'running'"
        ) as cur:
            jobs_running = (await cur.fetchone())[0]

        async with db._conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE status = 'completed' AND updated_at >= ?",
            (cutoff_1h,),
        ) as cur:
            jobs_completed_1h = (await cur.fetchone())[0]

        async with db._conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE status = 'failed' AND updated_at >= ?",
            (cutoff_1h,),
        ) as cur:
            jobs_failed_1h = (await cur.fetchone())[0]

        async with db._conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE status = 'running' AND created_at <= ?",
            (cutoff_30min,),
        ) as cur:
            jobs_stuck = (await cur.fetchone())[0]

        async with db._conn.execute(
            """SELECT iteration,
                      ROUND((julianday(updated_at) - julianday(created_at)) * 86400, 1) AS duration_sec
               FROM jobs
               WHERE status IN ('completed', 'failed')
               ORDER BY updated_at DESC
               LIMIT 10""",
        ) as cur:
            rows = await cur.fetchall()

    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"DB unavailable: {exc}")

    total_1h = jobs_completed_1h + jobs_failed_1h
    failure_rate = round(jobs_failed_1h / total_1h, 3) if total_1h > 0 else 0.0

    iterations = [r[0] for r in rows if r[0] is not None]
    durations = [r[1] for r in rows if r[1] is not None]

    avg_iter = round(sum(iterations) / len(iterations), 1) if iterations else None
    max_iter = max(iterations) if iterations else None
    avg_dur = round(sum(durations) / len(durations), 1) if durations else None
    max_dur = max(durations) if durations else None

    return {
        "jobs_running": jobs_running,
        "jobs_completed_1h": jobs_completed_1h,
        "jobs_failed_1h": jobs_failed_1h,
        "failure_rate_1h": failure_rate,
        "avg_iterations_last_10": avg_iter,
        "max_iterations_last_10": max_iter,
        "avg_duration_sec": avg_dur,
        "max_duration_sec": max_dur,
        "jobs_stuck_over_30min": jobs_stuck,
        "broadcaster_count": len(_broadcasters),
        "iteration_limit": MAX_ITERATIONS,
        "disk_mb": {
            "logs": _dir_size_mb(LOG_DIR),
            "output": _dir_size_mb(OUTPUT_DIR),
            "db": round(db.DB_PATH.stat().st_size / 1_048_576, 1) if db.DB_PATH.exists() else 0.0,
        },
    }


_PDF_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap');

@media print {
  .no-print { display: none !important; }
  body { padding: 0; }
  .finding { page-break-inside: avoid; }
}

@page { margin: 2cm; }

* { box-sizing: border-box; margin: 0; padding: 0; }

body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-size: 11pt;
    line-height: 1.65;
    color: #1a1a2e;
    background: #ffffff;
    padding: 40pt 48pt;
}

h1 { font-size: 22pt; font-weight: 600; color: #1a1a2e; margin-bottom: 8pt; line-height: 1.3; }
h2 { font-size: 13pt; font-weight: 600; color: #2d2d5e; margin-top: 20pt; margin-bottom: 8pt; text-transform: uppercase; letter-spacing: 0.05em; }
h3 { font-size: 11pt; font-weight: 500; color: #1a1a2e; margin-top: 12pt; margin-bottom: 4pt; }

p { margin-bottom: 8pt; color: #374151; }
ul, ol { margin-left: 18pt; margin-bottom: 8pt; }
li { margin-bottom: 3pt; color: #374151; }

a.cite {
    display: inline-block;
    font-size: 7.5pt;
    font-weight: 600;
    color: #7c3aed;
    background: #f5f3ff;
    border: 1pt solid #ddd6fe;
    border-radius: 3pt;
    padding: 0pt 4pt;
    margin: 0 1pt;
    vertical-align: super;
    text-decoration: none;
    white-space: nowrap;
    line-height: 1.6;
}
a.cite:hover { background: #ede9fe; }

.section {
    background: #f9fafb;
    border: 1pt solid #e5e7eb;
    border-radius: 6pt;
    padding: 14pt 18pt;
    margin-bottom: 12pt;
}

.label {
    font-size: 8pt;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: #7c3aed;
    margin-bottom: 6pt;
}

.thesis { font-style: italic; color: #6b7280; font-size: 10pt; margin-top: 6pt; }

.finding {
    border: 1pt solid #e5e7eb;
    border-radius: 6pt;
    margin-bottom: 10pt;
    overflow: hidden;
    page-break-inside: avoid;
}

.finding-header {
    background: #f3f4f6;
    border-bottom: 1pt solid #e5e7eb;
    padding: 10pt 14pt;
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
}

.finding-question { font-weight: 500; font-size: 10.5pt; flex: 1; margin-right: 12pt; }

.badge {
    font-size: 8pt;
    font-weight: 600;
    padding: 2pt 7pt;
    border-radius: 20pt;
    white-space: nowrap;
}
.badge-high { background: #d1fae5; color: #065f46; }
.badge-medium { background: #fef3c7; color: #92400e; }
.badge-low { background: #fee2e2; color: #991b1b; }

.finding-body { padding: 10pt 14pt; }
.evidence-label { font-size: 8pt; font-weight: 600; color: #9ca3af; text-transform: uppercase; margin-bottom: 4pt; margin-top: 8pt; }
.evidence-item { font-size: 9.5pt; color: #6b7280; margin-bottom: 2pt; padding-left: 10pt; border-left: 2pt solid #7c3aed; }
.conflict { background: #fff7ed; border: 1pt solid #fed7aa; border-radius: 4pt; padding: 8pt 10pt; margin-top: 8pt; }
.conflict-label { font-size: 8pt; font-weight: 600; color: #c2410c; margin-bottom: 3pt; }
.conflict p { font-size: 9.5pt; color: #6b7280; }
.sources { margin-top: 8pt; }
.source-url { font-size: 8pt; color: #7c3aed; margin-bottom: 2pt; word-break: break-all; }

.theme-item { padding-left: 10pt; border-left: 2pt solid #7c3aed; margin-bottom: 4pt; font-size: 10.5pt; }

.limitation-item { color: #6b7280; font-size: 10pt; margin-bottom: 3pt; }

.footer { margin-top: 24pt; font-size: 8pt; color: #d1d5db; text-align: center; }
"""


def _build_pdf_html(job_id: str, report: dict) -> str:
    confidence_badge = {
        "high": "badge-high", "medium": "badge-medium", "low": "badge-low"
    }

    findings_html = ""
    for finding in report.get("findings", []):
        conf = finding.get("confidence", "medium")
        badge_cls = confidence_badge.get(conf, "badge-medium")

        evidence_html = ""
        evidence = finding.get("evidence", [])
        if evidence:
            items = "".join(f'<div class="evidence-item">{e}</div>' for e in evidence)
            evidence_html = f'<div class="evidence-label">Evidence</div>{items}'

        conflict_html = ""
        conflicts = finding.get("conflicting_evidence", [])
        if conflicts:
            items = "".join(f"<p>{c}</p>" for c in conflicts)
            conflict_html = f'<div class="conflict"><div class="conflict-label">Conflicting evidence</div>{items}</div>'

        sources_html = ""
        sources = finding.get("sources", [])
        if sources:
            items = "".join(
                f'<div class="source-url">{src}</div>' for src in sources
            )
            sources_html = f'<div class="sources">{items}</div>'

        findings_html += f"""
        <div class="finding">
          <div class="finding-header">
            <div class="finding-question">{finding.get("sub_question", "")}</div>
            <span class="badge {badge_cls}">{conf}</span>
          </div>
          <div class="finding-body">
            <p>{finding.get("answer", "")}</p>
            {evidence_html}
            {conflict_html}
            {sources_html}
          </div>
        </div>"""

    themes = report.get("cross_cutting_themes", [])
    themes_html = ""
    if themes:
        items = "".join(f'<div class="theme-item">{t}</div>' for t in themes)
        themes_html = f"""
        <div class="section">
          <div class="label">Cross-cutting Themes</div>
          {items}
        </div>"""

    limitations = report.get("limitations", [])
    limitations_html = ""
    if limitations:
        items = "".join(f'<p class="limitation-item">· {lim}</p>' for lim in limitations)
        limitations_html = f"""
        <div class="section">
          <div class="label">Limitations</div>
          {items}
        </div>"""

    conclusion_md = report.get("conclusion", "")
    conclusion_html = md_lib.markdown(conclusion_md, extensions=["tables", "fenced_code"])

    overall_conf = report.get("overall_confidence", "medium")
    overall_badge_cls = confidence_badge.get(overall_conf, "badge-medium")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{report.get("title", "Research Report")}</title>
<style>{_PDF_CSS}</style>
</head>
<body>
  <div class="no-print" style="position:fixed;top:16px;right:16px;z-index:100;display:flex;gap:8px;">
    <button onclick="window.print()"
      style="background:#7c3aed;color:#fff;border:none;border-radius:8px;padding:8px 18px;font-size:13px;font-weight:600;cursor:pointer;">
      Save as PDF
    </button>
    <button onclick="history.length > 1 ? history.back() : window.close()"
      style="background:#e5e7eb;color:#374151;border:none;border-radius:8px;padding:8px 14px;font-size:13px;cursor:pointer;">
      ← Back
    </button>
  </div>

  <h1>{report.get("title", "Research Report")}</h1>
  <span class="badge {overall_badge_cls}" style="margin-bottom:14pt;display:inline-block;">
    {overall_conf} confidence
  </span>

  <div class="section">
    <div class="label">Executive Summary</div>
    <p>{report.get("executive_summary", "")}</p>
    <p class="thesis">"{report.get("thesis", "")}"</p>
  </div>

  <h2>Findings</h2>
  {findings_html}

  {themes_html}

  <div class="section">
    <div class="label">Conclusion</div>
    {conclusion_html}
  </div>

  {limitations_html}

  <div class="footer">Job ID: {job_id}</div>

<script>
// Map source IDs to public data URLs
const SOURCE_URLS = {{
  'RBI:repo_rate':          'https://data.worldbank.org/indicator/FR.INR.LEND?locations=IN',
  'RBI:cpi':                'https://data.worldbank.org/indicator/FP.CPI.TOTL.ZG?locations=IN',
  'RBI:wpi':                'https://data.worldbank.org/indicator/NY.GDP.DEFL.KD.ZG?locations=IN',
  'RBI:bank_credit_growth': 'https://data.worldbank.org/indicator/FS.AST.PRVT.GD.ZS?locations=IN',
  'RBI:bank_deposits':      'https://data.worldbank.org/indicator/FS.AST.PRVT.GD.ZS?locations=IN',
  'RBI:forex_reserves':     'https://data.worldbank.org/indicator/FI.RES.TOTL.CD?locations=IN',
  'RBI:npa_ratio':          'https://data.worldbank.org/indicator/FB.AST.NPER.ZS?locations=IN',
  'RBI:gdp_growth':         'https://data.worldbank.org/indicator/NY.GDP.MKTP.KD.ZG?locations=IN',
}};

function sourceUrl(id) {{
  if (SOURCE_URLS[id]) return SOURCE_URLS[id];
  const parts = id.split(':');
  if (parts[0] === 'BSE') {{
    const ticker = parts[1] || '';
    const type   = parts[2] || 'results';
    if (type === 'shareholding') return `https://finance.yahoo.com/quote/${{ticker}}/holders/`;
    if (type === 'announcements') return `https://finance.yahoo.com/quote/${{ticker}}/news/`;
    return `https://finance.yahoo.com/quote/${{ticker}}/financials/`;
  }}
  if (parts[0] === 'SEBI') return 'https://www.nseindia.com/companies-listing/corporate-filings-insider-trading';
  return '#';
}}

// Replace [SOURCE:ID] patterns with clickable citation badges
const CITE_RE = /\[([A-Z]+:[^\]]+)\]/g;
function linkCitations(node) {{
  if (node.nodeType === 3) {{  // Text node
    const text = node.nodeValue;
    if (!CITE_RE.test(text)) return;
    CITE_RE.lastIndex = 0;
    const frag = document.createDocumentFragment();
    let last = 0, m;
    while ((m = CITE_RE.exec(text)) !== null) {{
      if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
      const a = document.createElement('a');
      a.className = 'cite';
      a.href = sourceUrl(m[1]);
      a.target = '_blank';
      a.rel = 'noreferrer';
      a.textContent = m[1];
      frag.appendChild(a);
      last = m.index + m[0].length;
    }}
    if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
    node.parentNode.replaceChild(frag, node);
  }} else if (node.nodeType === 1 && !['SCRIPT','STYLE','A'].includes(node.tagName)) {{
    Array.from(node.childNodes).forEach(linkCitations);
  }}
}}
document.addEventListener('DOMContentLoaded', () => linkCitations(document.body));
</script>
</body>
</html>"""


@app.get("/research/{job_id}/pdf")
async def print_report(job_id: str) -> HTMLResponse:
    """Return a print-optimised HTML page. The browser's Ctrl/Cmd+P prints it as PDF."""
    job = await db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "completed":
        raise HTTPException(status_code=400, detail="Report not ready yet")
    if not job.get("result"):
        raise HTTPException(status_code=500, detail="No structured result available")

    html = _build_pdf_html(job_id, job["result"])
    return HTMLResponse(content=html)
