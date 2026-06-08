import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from pprint import pformat

import openai
from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

load_dotenv()

from app.agents.graph import build_graph
from app.llm.synthesize import render_to_markdown, synthesize_report

LOG_FILE = Path(__file__).parent / "logs" / "research.log"
OUTPUT_DIR = Path(__file__).parent / "output"


def setup_logging() -> logging.Logger:
    LOG_FILE.parent.mkdir(exist_ok=True)
    logger = logging.getLogger("research")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    return logger



def log_message(logger: logging.Logger, i: int, msg) -> None:
    role = type(msg).__name__
    logger.info(f"\n---- step {i}: {role} ----")

    if hasattr(msg, "content") and msg.content:
        logger.info(msg.content)

    if hasattr(msg, "tool_calls") and msg.tool_calls:
        for tc in msg.tool_calls:
            logger.info(f"  tool_call: {tc['name']}({tc['args']}) id={tc['id']}")

    if isinstance(msg, ToolMessage):
        logger.info(f"  tool_call_id={msg.tool_call_id}")


def verify_tool_call_pairing(messages: list) -> list[str]:
    issues: list[str] = []
    pending: dict[str, str] = {}

    for msg in messages:
        if isinstance(msg, AIMessage):
            for tc in msg.tool_calls or []:
                pending[tc["id"]] = tc["name"]
        elif isinstance(msg, ToolMessage):
            tool_name = pending.pop(msg.tool_call_id, None)
            if tool_name is None:
                issues.append(
                    f"Unpaired ToolMessage: tool_call_id={msg.tool_call_id!r}"
                )

    for tool_call_id, tool_name in pending.items():
        issues.append(
            f"Missing ToolMessage for {tool_name} tool_call_id={tool_call_id!r}"
        )

    return issues


def analyze_tool_pattern(logger: logging.Logger, messages: list) -> None:
    ai_tool_batches: list[list[str]] = []

    for msg in messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            ai_tool_batches.append([tc["name"] for tc in msg.tool_calls])

    logger.info("\n---- Tool batches (per agent turn) ----")
    for i, batch in enumerate(ai_tool_batches, start=1):
        logger.info(f"  turn {i}: {', '.join(batch)}")

    if not ai_tool_batches:
        logger.info("Pattern: no tool calls.")
        return

    first_batch = ai_tool_batches[0]
    if all(name == "search_web" for name in first_batch) and len(first_batch) > 1:
        if len(ai_tool_batches) > 1 and all(
            name == "take_notes" for name in ai_tool_batches[1]
        ):
            logger.info(
                "Pattern: agent batched all searches first, "
                "then batched take_notes."
            )
            return

    if len(ai_tool_batches) >= 2:
        for search_batch, note_batch in zip(ai_tool_batches, ai_tool_batches[1:]):
            if search_batch == ["search_web"] and note_batch == ["take_notes"]:
                logger.info("Pattern: agent alternates single search then take_notes.")
                return

    logger.info("Pattern: mixed tool usage across turns.")


def _extract_inline_output(messages: list) -> str:
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content and not msg.tool_calls:
            return msg.content
    return ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a deep research query.")
    parser.add_argument("question", help="The research question to investigate")
    args = parser.parse_args()

    logger = setup_logging()
    started_at = datetime.now(timezone.utc).isoformat()
    logger.info(f"==== run started at {started_at} ====")
    logger.info(f"  question: {args.question}")

    graph = build_graph()
    initial_state = {
        "messages": [HumanMessage(content=args.question)],
        "notes": [],
        "research_plan": None,
        "evaluation_history": [],
        "iteration_count": 0,
    }

    logger.info("\n" + "="*60)
    logger.info("STATE [main — INITIAL STATE]")
    logger.info(f"  iteration_count: {initial_state['iteration_count']}")
    logger.info(f"  notes: {initial_state['notes']}")
    logger.info(f"  messages: {initial_state['messages']}")
    logger.info("="*60 + "\n")

    logger.info("Invoking graph...\n")
    try:
        result = graph.invoke(initial_state)
    except openai.RateLimitError as exc:
        error_body = getattr(exc, "body", {}) or {}
        error_msg = error_body.get("error", {}).get("message", str(exc))
        logger.error(
            f"\nFATAL: OpenAI rate limit exceeded — {error_msg}\n"
            "The research could not be completed. Consider:\n"
            "  1. Waiting a few minutes before retrying\n"
            "  2. Reducing the complexity of the research query\n"
            "  3. Upgrading your OpenAI API tier at "
            "https://platform.openai.com/account/rate-limits"
        )
        print(
            f"\nError: OpenAI rate limit exceeded — {error_msg}\n"
            "See logs for details. Run complete (no output produced).\n"
            f"Log: {LOG_FILE.resolve()}"
        )
        sys.exit(1)
    except openai.APIError as exc:
        logger.error(f"\nFATAL: OpenAI API error — {exc}")
        print(
            f"\nError: OpenAI API error — {exc}\n"
            "See logs for details. Run complete (no output produced).\n"
            f"Log: {LOG_FILE.resolve()}"
        )
        sys.exit(1)
    except Exception as exc:
        logger.error(f"\nFATAL: Unexpected error — {type(exc).__name__}: {exc}")
        print(
            f"\nError: {type(exc).__name__}: {exc}\n"
            "See logs for details. Run complete (no output produced).\n"
            f"Log: {LOG_FILE.resolve()}"
        )
        sys.exit(1)

    logger.info("\n" + "="*60)
    logger.info("STATE [main — FINAL STATE]")
    logger.info(f"  iteration_count: {result['iteration_count']}")
    logger.info(f"  total messages: {len(result['messages'])}")
    logger.info(f"  total notes: {len(result['notes'])}")
    logger.info(f"  notes: {pformat(result['notes'])}")
    evals = result.get("evaluation_history", [])
    logger.info(f"  evaluations: {len(evals)}")
    for idx, ev in enumerate(evals, 1):
        logger.info(f"    eval {idx}: verdict={ev['verdict']} source_quality={ev['source_quality']}")
    plan = result.get("research_plan")
    if plan:
        logger.info(f"  plan thesis: {plan['thesis']}")
        for sq in plan["sub_questions"]:
            logger.info(f"    {sq['id']} [{sq['status']}]: {sq['question']}")
    logger.info("="*60 + "\n")

    for i, msg in enumerate(result["messages"], start=1):
        log_message(logger, i, msg)

    pairing_issues = verify_tool_call_pairing(result["messages"])
    logger.info("\n---- Tool call pairing ----")
    if pairing_issues:
        for issue in pairing_issues:
            logger.info(f"FAIL: {issue}")
    else:
        logger.info("PASS: every ToolMessage matches an AIMessage tool_call id.")

    analyze_tool_pattern(logger, result["messages"])

    if result["notes"]:
        logger.info("\n---- Notes ----")
        for i, note in enumerate(result["notes"], start=1):
            logger.info(
                f"{i}. [{note['sub_question_id']}] (confidence={note['confidence']}) "
                f"{note['finding']}\n   source: {note['source_url']}"
            )
    else:
        logger.info("\n---- Notes ----")
        logger.info("No notes recorded.")

    OUTPUT_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H%M%S")
    output_file = OUTPUT_DIR / f"output {ts}.md"

    plan = result.get("research_plan")
    notes = result.get("notes", [])
    evaluation_history = result.get("evaluation_history", [])

    if plan and notes:
        try:
            logger.info("\n---- Running post-graph synthesis ----")
            report = asyncio.run(synthesize_report(notes, plan, evaluation_history))
            output_content = render_to_markdown(report)
            logger.info(f"  synthesis complete: {len(report.findings)} findings, "
                        f"overall_confidence={report.overall_confidence}")
        except Exception as exc:
            logger.error(f"  synthesis failed ({type(exc).__name__}: {exc}), "
                         "falling back to inline agent output")
            output_content = _extract_inline_output(result["messages"])
    else:
        logger.warning("  no plan or notes — falling back to inline agent output")
        output_content = _extract_inline_output(result["messages"])

    if output_content:
        output_file.write_text(output_content, encoding="utf-8")
        logger.info(f"\n---- Output written to {output_file.resolve()} ----")
        print(f"Output written to {output_file.resolve()}")
    else:
        logger.info("\n---- No output produced ----")
        print("Warning: no output produced.")

    print(f"Run complete. Log written to {LOG_FILE.resolve()}")


if __name__ == "__main__":
    main()
