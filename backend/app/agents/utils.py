import asyncio
import logging
import time

import openai

from app.log_context import get_logger

logger = get_logger()

MAX_RETRIES = 2
RETRY_BASE_DELAY = 5


async def ainvoke_with_retry(llm, messages, *, context: str = "LLM call"):
    """Async version of invoke_with_retry — uses llm.ainvoke() and asyncio.sleep()."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return await llm.ainvoke(messages)
        except openai.RateLimitError as exc:
            error_body = getattr(exc, "body", {}) or {}
            error_msg = error_body.get("error", {}).get("message", str(exc))

            if "Requested" in error_msg and "Limit" in error_msg:
                logger.error(
                    f"[{context}] Request size exceeds token limit — "
                    f"not retryable: {error_msg}"
                )
                raise

            delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
            logger.warning(
                f"[{context}] Rate limited (attempt {attempt}/{MAX_RETRIES}), "
                f"retrying in {delay}s: {error_msg}"
            )
            await asyncio.sleep(delay)
        except openai.APIError as exc:
            delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
            logger.warning(
                f"[{context}] OpenAI API error (attempt {attempt}/{MAX_RETRIES}), "
                f"retrying in {delay}s: {exc}"
            )
            await asyncio.sleep(delay)

    logger.error(f"[{context}] All {MAX_RETRIES} retries exhausted")
    raise


def invoke_with_retry(llm, messages, *, context: str = "LLM call"):
    """Invoke an LLM with retry logic for transient rate-limit errors.

    Returns the response on success, or raises the last exception if all
    retries are exhausted or the error is non-retryable (e.g. request too large).
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return llm.invoke(messages)
        except openai.RateLimitError as exc:
            error_body = getattr(exc, "body", {}) or {}
            error_msg = error_body.get("error", {}).get("message", str(exc))

            if "Requested" in error_msg and "Limit" in error_msg:
                logger.error(
                    f"[{context}] Request size exceeds token limit — "
                    f"not retryable: {error_msg}"
                )
                raise

            delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
            logger.warning(
                f"[{context}] Rate limited (attempt {attempt}/{MAX_RETRIES}), "
                f"retrying in {delay}s: {error_msg}"
            )
            time.sleep(delay)
        except openai.APIError as exc:
            delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
            logger.warning(
                f"[{context}] OpenAI API error (attempt {attempt}/{MAX_RETRIES}), "
                f"retrying in {delay}s: {exc}"
            )
            time.sleep(delay)

    logger.error(f"[{context}] All {MAX_RETRIES} retries exhausted")
    raise
