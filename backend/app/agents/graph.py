import logging
from pprint import pformat

import openai
from langchain_core.messages import AIMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from app.agents.state import ResearchState
from app.agents.tools import tools
from app.agents.utils import ainvoke_with_retry
from app.log_context import get_logger
from app.llm.prompts.system import SYSTEM_PROMPT

logger = get_logger()

MAX_ITERATIONS = 40
RECENT_TURNS = 4

llm = ChatOpenAI(model="gpt-4o")
llm_with_tools = llm.bind_tools(tools)


def _log_state(label: str, state: dict) -> None:
    logger.info(f"\n{'='*60}")
    logger.info(f"STATE [{label}]")
    logger.info(f"  iteration_count: {state.get('iteration_count')}")
    logger.info(f"  research_plan: {'set' if state.get('research_plan') else 'None'}")
    logger.info(f"  notes ({len(state.get('notes', []))}): {pformat(state.get('notes', []))}")
    logger.info(f"  messages ({len(state.get('messages', []))})")
    for i, m in enumerate(state.get("messages", [])):
        logger.info(f"    [{i}] {type(m).__name__}: {str(m.content)[:120]}")
    logger.info(f"{'='*60}\n")


def _build_memory_context(state: ResearchState) -> str:
    """Build a compact memory block from the research plan, notes, and status.

    Injected into the system prompt so the agent retains full awareness of
    everything discovered so far, even after older messages are trimmed.
    """
    sections: list[str] = []

    plan = state.get("research_plan")
    if plan:
        lines = [f"\n--- RESEARCH PLAN ---", f"Thesis: {plan['thesis']}"]
        answered = 0
        total = len(plan["sub_questions"])
        for sq in plan["sub_questions"]:
            lines.append(f"  [{sq['id']}] status={sq['status']} | {sq['question']}")
            if sq["status"] != "answered":
                queries = sq.get("expected_search_queries", [])
                if queries:
                    quoted = ", ".join('"' + str(q) + '"' for q in queries)
                    lines.append(f"    suggested queries: {quoted}")
            if sq["status"] == "answered":
                answered += 1
        remaining = total - answered
        if remaining > 0:
            lines.append(
                f"\n>>> {answered}/{total} sub-questions answered. "
                f"{remaining} REMAINING — you MUST research these before "
                f"producing any final answer. <<<"
            )
        else:
            lines.append(
                f"\n>>> ALL {total} sub-questions answered. "
                f"You may now produce your final synthesis. <<<"
            )
        sections.append("\n".join(lines))

    notes = state.get("notes", [])
    if notes:
        notes_lines = [f"\n--- ACCUMULATED NOTES ({len(notes)}) ---"]
        grouped: dict[str, list[dict]] = {}
        for note in notes:
            grouped.setdefault(note["sub_question_id"], []).append(note)
        for sq_id, sq_notes in sorted(grouped.items()):
            notes_lines.append(f"  [{sq_id}]")
            for n in sq_notes:
                notes_lines.append(
                    f"    - (confidence={n['confidence']}) {n['finding']}"
                    f"\n      source: {n['source_url']}"
                )
        sections.append("\n".join(notes_lines))

    iteration = state.get("iteration_count", 0)
    remaining_iters = MAX_ITERATIONS - iteration
    eval_history = state.get("evaluation_history", [])
    status = f"\n--- STATUS ---\nIteration: {iteration}/{MAX_ITERATIONS} | Notes collected: {len(notes)}"

    if plan:
        answered = sum(1 for sq in plan["sub_questions"] if sq["status"] == "answered")
        expected_evals = answered // 2
        if answered >= 2 and len(eval_history) < expected_evals:
            status += (
                f"\n>>> MANDATORY: {answered} sub-questions answered but "
                f"evaluate_progress called only {len(eval_history)} time(s) "
                f"(expected {expected_evals}). "
                f"CALL evaluate_progress NOW before any further searches. <<<"
            )

    if remaining_iters <= 3:
        status += (
            f"\n>>> WARNING: Only {remaining_iters} iterations left. "
            "You MUST write your final synthesis NOW using whatever notes "
            "you have. Do NOT call any more tools. <<<"
        )
    sections.append(status)

    return "\n".join(sections)


def _trim_messages(messages: list, recent_turns: int) -> list:
    """Keep the first human message plus the last N logical turns.

    A "turn" is an AIMessage together with all of its following ToolMessages.
    Trimming at turn boundaries guarantees every tool_call_id in an AIMessage
    has its matching ToolMessage — which the OpenAI API requires.
    """
    if not messages:
        return messages

    first_human = [messages[0]]
    remaining = messages[1:]

    turns: list[list] = []
    current_turn: list = []
    for msg in remaining:
        if isinstance(msg, AIMessage):
            if current_turn:
                turns.append(current_turn)
            current_turn = [msg]
        else:
            current_turn.append(msg)
    if current_turn:
        turns.append(current_turn)

    if len(turns) <= recent_turns:
        return messages

    kept = [msg for turn in turns[-recent_turns:] for msg in turn]
    return first_human + kept


def _build_partial_summary(state: ResearchState) -> str:
    """Build a best-effort summary from whatever research has been gathered."""
    notes = state.get("notes", [])
    plan = state.get("research_plan")

    lines = [
        "# Research Summary (Partial — ended early due to API limits)\n",
        "The research process was interrupted by an OpenAI API rate limit. "
        "Below is a summary of findings gathered before the interruption.\n",
    ]

    if plan:
        lines.append(f"**Research question:** {plan['original_question']}")
        lines.append(f"**Thesis:** {plan['thesis']}\n")

        answered = [sq for sq in plan["sub_questions"] if sq["status"] == "answered"]
        pending = [sq for sq in plan["sub_questions"] if sq["status"] != "answered"]

        if answered:
            lines.append(f"**Completed sub-questions ({len(answered)}):**")
            for sq in answered:
                lines.append(f"- {sq['question']}")
            lines.append("")

        if pending:
            lines.append(f"**Incomplete sub-questions ({len(pending)}):**")
            for sq in pending:
                lines.append(f"- {sq['question']} (status: {sq['status']})")
            lines.append("")

    if notes:
        lines.append(f"## Findings ({len(notes)} notes)\n")
        grouped: dict[str, list] = {}
        for note in notes:
            grouped.setdefault(note["sub_question_id"], []).append(note)

        for sq_id, sq_notes in grouped.items():
            lines.append(f"### {sq_id}\n")
            for n in sq_notes:
                lines.append(
                    f"- {n['finding']} "
                    f"(confidence: {n['confidence']}, source: {n['source_url']})"
                )
            lines.append("")
    else:
        lines.append(
            "*No research notes were collected before the interruption.*"
        )

    return "\n".join(lines)


async def agent(state: ResearchState) -> dict:
    _log_state("agent node — RECEIVED", state)

    memory_context = _build_memory_context(state)
    system_content = SYSTEM_PROMPT + memory_context

    all_messages = state["messages"]
    recent = _trim_messages(all_messages, RECENT_TURNS)
    messages = [SystemMessage(content=system_content), *recent]

    logger.info(
        f"  context window: {len(all_messages)} total messages -> "
        f"{len(recent)} after trimming (keeping last {RECENT_TURNS} turns)"
    )

    try:
        response = await ainvoke_with_retry(llm_with_tools, messages, context="agent")
    except openai.RateLimitError as exc:
        error_body = getattr(exc, "body", {}) or {}
        error_msg = error_body.get("error", {}).get("message", str(exc))
        logger.error(
            f"Agent LLM call failed due to rate limiting: {error_msg}. "
            f"Ending research gracefully with {len(state.get('notes', []))} "
            f"notes collected so far."
        )
        response = AIMessage(content=_build_partial_summary(state))
    except openai.APIError as exc:
        logger.error(f"Agent LLM call failed due to OpenAI API error: {exc}. Ending research gracefully.")
        response = AIMessage(content=_build_partial_summary(state))

    update = {
        "messages": [response],
        "iteration_count": state["iteration_count"] + 1,
    }
    logger.info(f"\nSTATE [agent node — RETURNING UPDATE]")
    logger.info(f"  iteration_count: {update['iteration_count']}")
    logger.info(f"  new message: {type(response).__name__}: {str(response.content)[:120]}")
    if hasattr(response, "tool_calls") and response.tool_calls:
        for tc in response.tool_calls:
            logger.info(f"    tool_call: {tc['name']}({tc['args']}) id={tc['id']}")
    return update


def should_continue(state: ResearchState) -> str:
    _log_state("should_continue — CHECKING", state)

    iteration = state.get("iteration_count", 0)
    if iteration >= MAX_ITERATIONS:
        logger.warning(f"  -> routing to 'end' (hit MAX_ITERATIONS={MAX_ITERATIONS})")
        return "end"

    last_message = state["messages"][-1]
    if last_message.tool_calls:
        logger.info("  -> routing to 'continue' (tools)")
        return "continue"
    logger.info("  -> routing to 'end'")
    return "end"


def build_graph():
    """
    Graph topology:

    START --> agent --[tool_calls?]--> tools --> agent
                    --[no tool calls]--> END
    """
    graph_builder = StateGraph(ResearchState)
    graph_builder.add_node("agent", agent)
    graph_builder.add_node("tools", ToolNode(tools))

    graph_builder.add_edge(START, "agent")
    graph_builder.add_conditional_edges(
        "agent",
        should_continue,
        {
            "continue": "tools",
            "end": END,
        },
    )
    graph_builder.add_edge("tools", "agent")
    return graph_builder.compile()
