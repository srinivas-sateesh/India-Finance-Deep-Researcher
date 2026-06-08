"""Per-job log isolation via contextvars.ContextVar.

Each async job task calls bind_job_logger() at the start of its run, which
creates a dedicated FileHandler and stores it in a ContextVar. All
logger.xxx() calls anywhere in that task — including those in graph.py,
tools.py, and synthesize.py, and sync tools running via asyncio.to_thread()
— are automatically routed to the job-specific file.

ContextVar is used instead of threading.local() because async tasks all run
on the same thread; ContextVar values are properly isolated per-task and are
also copied into threads spawned by asyncio.to_thread().

Falls back to logging.getLogger("research") when no job logger is bound
(CLI / main.py usage).
"""
import contextvars
import logging
from pathlib import Path

_job_logger_var: contextvars.ContextVar[logging.Logger | None] = contextvars.ContextVar(
    "job_logger", default=None
)


class _ContextVarLogger:
    """Proxy that resolves to the per-job logger for the current async task."""

    def __getattr__(self, name: str):
        bound = _job_logger_var.get()
        target = bound if bound is not None else logging.getLogger("research")
        return getattr(target, name)


def get_logger() -> _ContextVarLogger:
    """Return a module-level logger proxy. Assign once at module level:

        logger = get_logger()

    All subsequent logger.info(...) calls resolve to the correct per-job
    logger at call time — no changes to call sites needed.
    """
    return _ContextVarLogger()


def bind_job_logger(job_id: str, log_dir: Path) -> logging.Logger:
    """Create a per-job file logger and bind it to the current async task context."""
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"{job_id}.log"

    job_logger = logging.getLogger(f"research.{job_id}")
    job_logger.setLevel(logging.INFO)
    job_logger.propagate = False
    job_logger.handlers.clear()

    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    job_logger.addHandler(handler)

    _job_logger_var.set(job_logger)
    return job_logger


def unbind_job_logger() -> None:
    """Close the per-job file handler and clear the context binding."""
    bound = _job_logger_var.get()
    if bound:
        for handler in bound.handlers[:]:
            handler.flush()
            handler.close()
            bound.removeHandler(handler)
    _job_logger_var.set(None)
