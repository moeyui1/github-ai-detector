"""
LLM scoring helpers — shared retry/penalty logic and concurrent runner.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

import re

from log import get_logger
from providers.base import BaseProvider, LLMCallResult

from engine.models import EventRecord, LLMLogEntry

_log = get_logger("engine.scoring")

# Pattern to strip Bearer/token values from exception messages
_SENSITIVE_RE = re.compile(
    r"(Bearer\s+|api[_-]?key[\"'\s:=]+|token[\"'\s:=]+)[A-Za-z0-9_\-./+=]{8,}",
    re.IGNORECASE,
)


def _sanitize_exc(exc: Exception) -> str:
    """Return exception message with sensitive tokens redacted."""
    return _SENSITIVE_RE.sub(r"\1***", str(exc))


def _rebase_penalty(commit: dict) -> float:
    """Return a weight reduction (0‑0.3) when authored_date != committed_date."""
    try:
        c_info = commit.get("commit", {})
        authored = c_info.get("author", {}).get("date", "")
        committed = c_info.get("committer", {}).get("date", "")
        if authored and committed and authored != committed:
            return 0.3
    except Exception:
        pass
    return 0.0


def _safe_llm_score(provider: BaseProvider, text: str) -> LLMCallResult:
    try:
        return provider.analyze_text(text)
    except Exception as exc:
        _log.warning("LLM call failed: %s: %s", type(exc).__name__, _sanitize_exc(exc))
        return LLMCallResult(score=0.0, error=str(type(exc).__name__))


def _safe_llm_score_batch(provider: BaseProvider, texts: list[str]) -> list[LLMCallResult]:
    """Score multiple texts in a single LLM call. Returns zero scores on failure."""
    if len(texts) == 1:
        return [_safe_llm_score(provider, texts[0])]
    try:
        return provider.analyze_batch(texts)
    except Exception as exc:
        _log.warning("Batch LLM call failed (%s), returning zero scores for %d items", _sanitize_exc(exc), len(texts))
        return [LLMCallResult(score=0.0, error=type(exc).__name__) for _ in texts]


def _run_llm_tasks(
    llm_tasks: list[tuple[EventRecord, str, dict | None]],
    provider: BaseProvider,
    concurrency: int,
    update: Callable[[str], None],
) -> list[LLMLogEntry]:
    """Run LLM scoring on a list of tasks; returns log entries."""
    logs: list[LLMLogEntry] = []
    if not llm_tasks:
        return logs

    update(f"Running LLM audit on {len(llm_tasks)} items …")

    def _score_one(task: tuple[EventRecord, str, dict | None]) -> LLMLogEntry:
        ev, text, commit = task
        llm_result = _safe_llm_score(provider, text)
        raw_score = llm_result.score
        if commit is not None:
            penalty = _rebase_penalty(commit)
            raw_score = max(0.0, raw_score - penalty)
        ev.ai_score = raw_score
        ev.reason = llm_result.reason
        return LLMLogEntry(
            event_title=ev.title,
            event_kind=ev.kind,
            score=raw_score,
            prompt_tokens=llm_result.prompt_tokens,
            completion_tokens=llm_result.completion_tokens,
            total_tokens=llm_result.total_tokens,
            model=llm_result.model,
            raw_response=llm_result.raw_response,
            error=llm_result.error,
        )

    done_count = 0
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(_score_one, t): t for t in llm_tasks}
        for future in as_completed(futures):
            logs.append(future.result())
            done_count += 1
            update(f"LLM audit {done_count}/{len(llm_tasks)} …")
    return logs
