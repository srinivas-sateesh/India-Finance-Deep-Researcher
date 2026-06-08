import operator
from typing import Annotated, Literal, TypedDict

from langgraph.graph import add_messages


class Note(TypedDict):
    finding: str
    source_url: str
    confidence: float
    sub_question_id: str


class SubQuestion(TypedDict):
    id: str
    question: str
    priority: int
    status: Literal["pending", "in_progress", "answered", "needs_more_research", "gap"]
    reasoning: str
    expected_search_queries: list[str]
    dependencies: list[str]


class ResearchPlan(TypedDict):
    original_question: str
    thesis: str
    sub_questions: list[SubQuestion]


class Evaluation(TypedDict):
    coverage: dict[str, str]
    conflicts: list[str]
    source_quality: str
    gaps: list[str]
    verdict: Literal["continue", "sufficient", "insufficient_sources"]
    guidance: str


_STATUS_RANK = {
    "pending": 0,
    "in_progress": 1,
    "needs_more_research": 2,
    "answered": 3,
    "gap": 4,
}


def merge_research_plan(
    existing: ResearchPlan | None, update: ResearchPlan | None
) -> ResearchPlan | None:
    """Reducer that merges concurrent writes to research_plan.

    When multiple tool calls (e.g. parallel take_notes) each update a
    different sub-question's status, this keeps the most-advanced status
    for every sub-question instead of discarding all but the last write.
    """
    if update is None:
        return existing
    if existing is None:
        return update
    merged = {sq["id"]: sq for sq in existing["sub_questions"]}
    for sq in update["sub_questions"]:
        sid = sq["id"]
        if sid not in merged or _STATUS_RANK.get(
            sq["status"], 0
        ) >= _STATUS_RANK.get(merged[sid]["status"], 0):
            merged[sid] = sq
    return {**update, "sub_questions": list(merged.values())}


class ResearchState(TypedDict):
    messages: Annotated[list, add_messages]
    notes: Annotated[list[Note], operator.add]
    research_plan: Annotated[ResearchPlan | None, merge_research_plan]
    evaluation_history: Annotated[list[Evaluation], operator.add]
    iteration_count: int
