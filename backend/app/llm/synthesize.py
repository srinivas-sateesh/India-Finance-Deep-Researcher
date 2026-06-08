import logging
from typing import Literal

import openai
from pydantic import BaseModel, Field

from app.log_context import get_logger
from app.llm.prompts.synthesis import SYNTHESIS_PROMPT

logger = get_logger()


class Finding(BaseModel):
    sub_question_id: str
    sub_question: str
    answer: str = Field(
        description=(
            "Direct answer to the sub-question. "
            "REQUIRED: embed the source identifier in square brackets IMMEDIATELY after every "
            "specific number, percentage, date, or named statistic — not at the end of the sentence. "
            "Use the exact source string from the notes (e.g. RBI:npa_ratio, BSE:HDFCBANK.NS:results, SEBI:Infosys:insider). "
            "CORRECT: 'The NPA ratio fell from 9.23% in 2019 [RBI:npa_ratio] to 1.72% in 2023 [RBI:npa_ratio].' "
            "WRONG: 'The NPA ratio fell from 9.23% in 2019 to 1.72% in 2023.' (missing inline citations)"
        )
    )
    evidence: list[str] = Field(
        description=(
            "Key facts from the notes supporting the answer. "
            "Each evidence item must end with its source in square brackets, e.g. '[RBI:cpi]'."
        )
    )
    sources: list[str]
    confidence: Literal["high", "medium", "low"]
    conflicting_evidence: list[str]


class ResearchReport(BaseModel):
    title: str
    executive_summary: str
    thesis: str
    findings: list[Finding]
    cross_cutting_themes: list[str]
    limitations: list[str]
    conclusion: str = Field(
        description=(
            "3-4 paragraphs directly answering the original question. "
            "Embed inline source citations [source_id] after every specific statistic, "
            "exactly as required in the Finding.answer field."
        )
    )
    overall_confidence: Literal["high", "medium", "low"]


def _build_synthesis_context(
    notes: list[dict],
    plan: dict,
    evaluation_history: list[dict],
) -> str:
    lines = [
        f"Original question: {plan['original_question']}",
        f"Research thesis: {plan['thesis']}",
        "",
        "## Research Notes by Sub-question",
    ]

    by_sq: dict[str, list[dict]] = {}
    for note in notes:
        by_sq.setdefault(note["sub_question_id"], []).append(note)

    for sq in plan["sub_questions"]:
        sq_notes = by_sq.get(sq["id"], [])
        lines.append(f"\n### [{sq['id']}] {sq['question']}")
        lines.append(f"Status: {sq['status']}")
        if sq_notes:
            for note in sq_notes:
                lines.append(f"\nFinding (confidence={note['confidence']}):")
                lines.append(note["finding"])
                lines.append(f"Source: {note['source_url']}")
        else:
            lines.append("(no notes collected for this sub-question)")

    if evaluation_history:
        latest = evaluation_history[-1]
        if latest.get("conflicts"):
            lines.append("\n## Conflicts Identified by Evaluator")
            for c in latest["conflicts"]:
                lines.append(f"- {c}")
        if latest.get("gaps"):
            lines.append("\n## Research Gaps Identified by Evaluator")
            for g in latest["gaps"]:
                lines.append(f"- {g}")

    return "\n".join(lines)


async def synthesize_report(
    notes: list[dict],
    plan: dict,
    evaluation_history: list[dict],
) -> ResearchReport:
    context = _build_synthesis_context(notes, plan, evaluation_history)

    client = openai.AsyncOpenAI()

    completion = await client.beta.chat.completions.parse(
        model="gpt-4o",
        max_tokens=8096,
        messages=[
            {"role": "system", "content": SYNTHESIS_PROMPT},
            {"role": "user", "content": context},
        ],
        response_format=ResearchReport,
    )

    usage = completion.usage
    logger.info(
        f"synthesize_report: input_tokens={usage.prompt_tokens} "
        f"output_tokens={usage.completion_tokens}"
    )

    report = completion.choices[0].message.parsed
    if report is None:
        raise ValueError("Model returned a refusal or unparseable response")
    return report


def render_to_markdown(report: ResearchReport) -> str:
    lines = [
        f"# {report.title}",
        "",
        "## Executive Summary",
        "",
        report.executive_summary,
        "",
        f"> **Thesis:** {report.thesis}",
        "",
        "## Findings",
        "",
    ]

    for finding in report.findings:
        lines.append(f"### {finding.sub_question}")
        lines.append("")
        lines.append(finding.answer)
        lines.append("")

        if finding.evidence:
            lines.append("**Evidence:**")
            for e in finding.evidence:
                lines.append(f"- {e}")
            lines.append("")

        if finding.conflicting_evidence:
            lines.append("**Conflicting evidence:**")
            for c in finding.conflicting_evidence:
                lines.append(f"- {c}")
            lines.append("")

        lines.append(f"*Confidence: {finding.confidence.capitalize()}*")
        lines.append("")

    if report.cross_cutting_themes:
        lines.append("## Cross-cutting Themes")
        lines.append("")
        for theme in report.cross_cutting_themes:
            lines.append(f"- {theme}")
        lines.append("")

    lines.append("## Conclusion")
    lines.append("")
    lines.append(report.conclusion)
    lines.append("")

    if report.limitations:
        lines.append("## Limitations")
        lines.append("")
        for lim in report.limitations:
            lines.append(f"- {lim}")
        lines.append("")

    all_sources: list[str] = []
    seen: set[str] = set()
    for finding in report.findings:
        for src in finding.sources:
            if src not in seen:
                all_sources.append(src)
                seen.add(src)

    if all_sources:
        lines.append("## Sources")
        lines.append("")
        for src in all_sources:
            lines.append(f"- {src}")
        lines.append("")

    lines.append("---")
    lines.append(f"*Overall confidence: {report.overall_confidence.capitalize()}*")

    return "\n".join(lines)
