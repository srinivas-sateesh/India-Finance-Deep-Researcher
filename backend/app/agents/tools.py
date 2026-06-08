import json
import logging
import os
import re
from typing import Annotated

import openai
from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from app.agents.state import Evaluation, Note, ResearchPlan
from app.agents.utils import invoke_with_retry
from app.log_context import get_logger
from app.llm.prompts.evaluation import EVALUATION_PROMPT
from app.llm.prompts.planning import PLANNING_PROMPT
from app.connectors import bse, imf, rbi, sebi

logger = get_logger()

o3_planner = ChatOpenAI(model=os.environ.get("PLANNER_MODEL", "o3"))
gpt4o_evaluator = ChatOpenAI(model=os.environ.get("EVALUATOR_MODEL", "gpt-4o"))


def _extract_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{", text)
    if match:
        depth, start = 0, match.start()
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
    cleaned = re.sub(r",\s*([}\]])", r"\1", text)
    cleaned = cleaned.replace("'", '"')
    return json.loads(cleaned)


@tool
def create_research_plan(
    question: str,
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Break a research question into a structured plan of sub-questions.

    Call this FIRST before doing any searches. It uses a reasoning model to
    decompose the question into prioritized, dependency-aware sub-questions.
    """
    logger.info(f"\n--- create_research_plan called ---")
    logger.info(f"  question: {question}")

    try:
        response = invoke_with_retry(
            o3_planner,
            [SystemMessage(content=PLANNING_PROMPT), {"role": "user", "content": question}],
            context="create_research_plan",
        )
    except (openai.RateLimitError, openai.APIError) as exc:
        logger.error(f"  create_research_plan failed: {exc}")
        return Command(update={
            "messages": [ToolMessage(
                content=f"Error creating research plan (OpenAI API error): {exc}. "
                        "The agent should try again or proceed without a formal plan.",
                tool_call_id=tool_call_id,
            )],
        })
    except Exception as exc:
        logger.error(f"  create_research_plan unexpected error: {type(exc).__name__}: {exc}")
        return Command(update={
            "messages": [ToolMessage(
                content=f"Error creating research plan ({type(exc).__name__}: {exc}). "
                        "The agent should try again.",
                tool_call_id=tool_call_id,
            )],
        })

    raw = response.content.strip()
    logger.info(f"  raw response ({len(raw)} chars): {raw[:500]}")

    try:
        plan_data = _extract_json(raw)
    except json.JSONDecodeError as exc:
        logger.warning(f"  JSON parse failed ({exc}), asking LLM to repair...")
        try:
            repair_response = invoke_with_retry(
                o3_planner,
                [
                    SystemMessage(content="Fix the following broken JSON so it is valid. Return ONLY the corrected JSON, nothing else."),
                    {"role": "user", "content": raw},
                ],
                context="create_research_plan (JSON repair)",
            )
            raw = repair_response.content.strip()
            plan_data = _extract_json(raw)
        except (json.JSONDecodeError, openai.RateLimitError, openai.APIError) as repair_exc:
            logger.error(f"  JSON repair also failed: {repair_exc}")
            return Command(update={
                "messages": [ToolMessage(
                    content=f"Error creating research plan: could not parse LLM response as JSON ({repair_exc}). "
                            "The agent should try again.",
                    tool_call_id=tool_call_id,
                )],
            })

    try:
        plan: ResearchPlan = {
            "original_question": question,
            "thesis": plan_data["thesis"],
            "sub_questions": plan_data["sub_questions"],
        }
    except (KeyError, TypeError) as exc:
        logger.error(f"  Parsed JSON missing required fields: {exc}")
        return Command(update={
            "messages": [ToolMessage(
                content=f"Error creating research plan: LLM response was valid JSON "
                        f"but missing required fields ({exc}). The agent should try again.",
                tool_call_id=tool_call_id,
            )],
        })

    logger.info(f"  plan thesis: {plan['thesis']}")
    logger.info(f"  sub-questions: {len(plan['sub_questions'])}")
    for sq in plan["sub_questions"]:
        logger.info(f"    {sq['id']} (p{sq['priority']}): {sq['question']}")
        logger.info(f"      deps: {sq['dependencies']}")
        logger.info(f"      queries: {sq['expected_search_queries']}")

    plan_summary_lines = [f"Research plan created — {plan['thesis']}"]
    for sq in plan["sub_questions"]:
        deps = f" (depends on: {', '.join(sq['dependencies'])})" if sq["dependencies"] else ""
        queries = ", ".join(f'"{str(q)}"' for q in sq.get("expected_search_queries", []))
        plan_summary_lines.append(
            f"  [{sq['id']}] (priority {sq['priority']}) {sq['question']}{deps}"
        )
        if queries:
            plan_summary_lines.append(f"    suggested queries: {queries}")
    plan_summary = "\n".join(plan_summary_lines)

    return Command(update={
        "research_plan": plan,
        "messages": [ToolMessage(content=plan_summary, tool_call_id=tool_call_id)],
    })


@tool
def take_notes(
    finding: str,
    source_url: str,
    confidence: float,
    sub_question_id: str,
    marks_complete: bool,
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Record an important research finding in the notes list.

    Set marks_complete=true when this is the final note for a sub-question,
    which transitions it from in_progress to answered. Use false for
    intermediate notes where you plan to add more findings.
    """
    logger.info(f"\n--- take_notes called ---")
    logger.info(f"  sub_question_id: {sub_question_id}")
    logger.info(f"  marks_complete: {marks_complete}")
    logger.info(f"  finding: {finding[:120]}")
    logger.info(f"  source_url: {source_url}")
    logger.info(f"  confidence: {confidence}")

    note: Note = {
        "finding": finding,
        "source_url": source_url,
        "confidence": confidence,
        "sub_question_id": sub_question_id,
    }

    new_status = "answered" if marks_complete else "in_progress"
    status_label = " [COMPLETED]" if marks_complete else ""
    confirmation = f"Recorded note for {sub_question_id}{status_label}: {finding}"

    update: dict = {
        "notes": [note],
        "messages": [ToolMessage(content=confirmation, tool_call_id=tool_call_id)],
    }

    plan = state.get("research_plan")
    if plan:
        updated_sqs = []
        for sq in plan["sub_questions"]:
            if sq["id"] == sub_question_id:
                updated_sqs.append({**sq, "status": new_status})
            else:
                updated_sqs.append(sq)
        update["research_plan"] = {**plan, "sub_questions": updated_sqs}

    logger.info(f"\nSTATE [take_notes — RETURNING UPDATE]")
    logger.info(f"  new note: {note}")
    logger.info(f"  status transition: -> {new_status}")
    logger.info(f"  existing notes in state: {len(state.get('notes', []))}")

    return Command(update=update)


@tool
def evaluate_progress(
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Evaluate whether accumulated research is sufficient to write the report.

    Call this after you've researched several sub-questions or when you
    believe you have enough evidence. The evaluation checks coverage,
    conflicts, source quality, and gaps, then returns a verdict:
    "continue", "sufficient", or "insufficient_sources".
    Use the verdict to decide whether to keep researching or write the report.
    """
    logger.info("\n--- evaluate_progress called ---")

    plan = state.get("research_plan")
    notes = state.get("notes", [])

    if not plan:
        return Command(update={
            "messages": [ToolMessage(
                content="Cannot evaluate: no research plan exists yet. "
                        "Call create_research_plan first.",
                tool_call_id=tool_call_id,
            )],
        })

    notes_by_sq: dict[str, list[dict]] = {}
    for note in notes:
        notes_by_sq.setdefault(note["sub_question_id"], []).append(note)

    context_lines = [
        f"Original question: {plan['original_question']}",
        f"Thesis: {plan['thesis']}",
        "",
        "Sub-questions:",
    ]
    for sq in plan["sub_questions"]:
        sq_notes = notes_by_sq.get(sq["id"], [])
        context_lines.append(
            f"  [{sq['id']}] status={sq['status']} | {sq['question']}"
        )
        if sq_notes:
            for j, n in enumerate(sq_notes, 1):
                context_lines.append(
                    f"    note {j}: (confidence={n['confidence']}) {n['finding']}"
                    f"\n           source: {n['source_url']}"
                )
        else:
            context_lines.append("    (no notes)")

    context = "\n".join(context_lines)
    logger.info(f"  evaluation context length: {len(context)} chars")
    logger.info(f"  total notes: {len(notes)}")

    try:
        response = invoke_with_retry(
            gpt4o_evaluator,
            [SystemMessage(content=EVALUATION_PROMPT), {"role": "user", "content": context}],
            context="evaluate_progress",
        )
    except (openai.RateLimitError, openai.APIError) as exc:
        logger.error(f"  evaluate_progress failed: {exc}")
        return Command(update={
            "messages": [ToolMessage(
                content=f"Evaluation failed due to OpenAI API error: {exc}. "
                        "Proceed to write the final synthesis with whatever "
                        "research has been gathered so far.",
                tool_call_id=tool_call_id,
            )],
        })
    except Exception as exc:
        logger.error(f"  evaluate_progress unexpected error: {type(exc).__name__}: {exc}")
        return Command(update={
            "messages": [ToolMessage(
                content=f"Evaluation failed ({type(exc).__name__}: {exc}). "
                        "Proceed to write the final synthesis with whatever "
                        "research has been gathered so far.",
                tool_call_id=tool_call_id,
            )],
        })

    raw = response.content.strip()
    logger.info(f"  raw response ({len(raw)} chars): {raw[:500]}")

    try:
        eval_data = _extract_json(raw)
    except json.JSONDecodeError as exc:
        logger.warning(f"  JSON parse failed ({exc}), asking LLM to repair...")
        try:
            repair_response = invoke_with_retry(
                gpt4o_evaluator,
                [
                    SystemMessage(content="Fix the following broken JSON so it is valid. Return ONLY the corrected JSON, nothing else."),
                    {"role": "user", "content": raw},
                ],
                context="evaluate_progress (JSON repair)",
            )
            raw = repair_response.content.strip()
            eval_data = _extract_json(raw)
        except (json.JSONDecodeError, openai.RateLimitError, openai.APIError) as repair_exc:
            logger.error(f"  JSON repair also failed: {repair_exc}. Returning fallback evaluation.")
            return Command(update={
                "messages": [ToolMessage(
                    content="Evaluation could not parse the LLM response. "
                            "Proceed to write the final synthesis with whatever "
                            "research has been gathered so far.",
                    tool_call_id=tool_call_id,
                )],
            })

    try:
        evaluation: Evaluation = {
            "coverage": eval_data["coverage"],
            "conflicts": eval_data.get("conflicts", []),
            "source_quality": eval_data["source_quality"],
            "gaps": eval_data.get("gaps", []),
            "verdict": eval_data["verdict"],
            "guidance": eval_data["guidance"],
        }
    except (KeyError, TypeError) as exc:
        logger.error(f"  Parsed JSON missing required fields: {exc}")
        return Command(update={
            "messages": [ToolMessage(
                content=f"Evaluation returned valid JSON but missing required fields ({exc}). "
                        "Proceed to write the final synthesis with whatever "
                        "research has been gathered so far.",
                tool_call_id=tool_call_id,
            )],
        })

    logger.info(f"  verdict: {evaluation['verdict']}")
    logger.info(f"  source_quality: {evaluation['source_quality']}")
    logger.info(f"  coverage: {evaluation['coverage']}")
    logger.info(f"  conflicts: {evaluation['conflicts']}")
    logger.info(f"  gaps: {evaluation['gaps']}")
    logger.info(f"  guidance: {evaluation['guidance']}")

    update: dict = {
        "evaluation_history": [evaluation],
        "messages": [ToolMessage(
            content=(
                f"Evaluation verdict: {evaluation['verdict']}\n"
                f"Source quality: {evaluation['source_quality']}\n"
                f"Coverage: {json.dumps(evaluation['coverage'])}\n"
                f"Conflicts: {evaluation['conflicts'] or 'None'}\n"
                f"Gaps: {evaluation['gaps'] or 'None'}\n"
                f"Guidance: {evaluation['guidance']}"
            ),
            tool_call_id=tool_call_id,
        )],
    }

    if plan:
        updated_sqs = []
        for sq in plan["sub_questions"]:
            sq_coverage = evaluation["coverage"].get(sq["id"], "missing")
            if sq_coverage == "strong" and sq["status"] != "answered":
                updated_sqs.append({**sq, "status": "answered"})
            elif sq_coverage in ("weak", "missing") and sq["status"] == "answered":
                updated_sqs.append({**sq, "status": "needs_more_research"})
            else:
                updated_sqs.append(sq)
        update["research_plan"] = {**plan, "sub_questions": updated_sqs}

    logger.info(f"\nSTATE [evaluate_progress — RETURNING UPDATE]")
    logger.info(f"  evaluation_history entry added")
    logger.info(f"  verdict: {evaluation['verdict']}")

    return Command(update=update)


# ── domain connector tools ────────────────────────────────────────────────────

@tool
async def search_bse_filings(company: str, filing_type: str, quarters: int = 4) -> str:
    """Fetch BSE exchange filings or analyst estimates for a listed Indian company.

    filing_type options:
      "results"       — quarterly financial results (revenue, PAT, EPS) for the last N quarters
      "shareholding"  — promoter/FII/DII/public holding percentages by quarter
      "announcements" — recent exchange announcements (board meetings, dividends, buybacks)
      "estimates"     — analyst consensus: EPS/revenue estimates for next 1-2 quarters and years,
                        EPS growth forecasts, price targets, buy/hold/sell recommendation counts.
                        Use this for forward-looking questions about expected company performance.
                        Note: these are analyst estimates, not company guidance or reported data.

    company: company name (e.g. "HDFC Bank", "Reliance Industries") or NSE ticker.
    quarters: number of recent quarters to return (default 4, max 8). Ignored for "estimates".

    Source identifier format: BSE:{ticker}:{filing_type}
    """
    return await bse.fetch(company, filing_type, quarters)


@tool
async def fetch_rbi_indicator(indicator: str, periods: int = 8) -> str:
    """Fetch a macroeconomic time series from the RBI Database of Indian Economy (DBIE).

    Supported indicators:
      "repo_rate"          — RBI policy repo rate (%)
      "cpi"                — Consumer Price Index, combined (YoY %)
      "wpi"                — Wholesale Price Index (YoY %)
      "bank_credit_growth" — Scheduled commercial bank credit growth (YoY %)
      "bank_deposits"      — Bank deposit growth (YoY %)
      "forex_reserves"     — Foreign exchange reserves (USD billion)
      "npa_ratio"          — Gross NPA ratio, scheduled commercial banks (%)
      "gdp_growth"         — GDP growth rate (%)

    periods: number of most recent data points to return (default 8).
    Returns a formatted time-series table.
    Source identifier format: RBI:{indicator}
    """
    return await rbi.fetch(indicator, periods)


@tool
async def search_sebi_disclosures(
    company: str,
    disclosure_type: str = "insider",
    lookback_days: int = 180,
) -> str:
    """Search SEBI regulatory filings for a company.

    disclosure_type options:
      "insider" — insider trading disclosures (PIT regulations): buying/selling by promoters, directors, KMPs
      "sast"    — Substantial Acquisition of Shares and Takeovers (>5% threshold crossings)
      "pledge"  — promoter pledge creation or invocation disclosures

    lookback_days: how far back to search (default 180 days).
    Returns a table: date, person/entity, transaction type, shares, % of total capital.
    Source identifier format: SEBI:{company}:{disclosure_type}
    """
    return await sebi.fetch(company, disclosure_type, lookback_days)


@tool
async def fetch_imf_outlook(indicator: str, periods: int = 10) -> str:
    """Fetch India GDP growth outlook combining historical actuals and forward projections.

    Returns a single time series with [actual] rows (World Bank WDI) and [forecast] rows
    (World Bank Global Economic Prospects). Use when the question asks about India's
    economic growth outlook or projected GDP trajectory.

    Supported indicators:
      "gdp_growth"      — Real GDP growth, annual % (actuals + 2-3yr forecasts) ✓ LIVE
      "inflation"       — Returns guidance: use fetch_rbi_indicator("cpi") for historical data
      "current_account" — Returns guidance: forward projections not accessible
      "fiscal_balance"  — Returns guidance: forward projections not accessible
      "unemployment"    — Returns guidance: forward projections not accessible
      "gdp_per_capita"  — Returns guidance: forward projections not accessible

    periods: total rows to return (default 10 = ~7 historical + ~3 forecast).
    Always note in findings that [forecast] rows are projections, not reported data.
    Source identifier format: IMF:{indicator}
    """
    return await imf.fetch(indicator, periods)


tools = [
    create_research_plan,
    search_bse_filings,
    fetch_rbi_indicator,
    search_sebi_disclosures,
    fetch_imf_outlook,
    take_notes,
    evaluate_progress,
]
