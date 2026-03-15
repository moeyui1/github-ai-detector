"""
LLM Provider base class and shared call logic.
"""

from __future__ import annotations

import json
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

from openai import OpenAI

from config import get_config
from log import get_logger
from prompts import load_prompt

_log = get_logger("providers.base")

_SYSTEM_PROMPT: str | None = None
_BATCH_SYSTEM_PROMPT: str | None = None


def _get_system_prompt() -> str:
    global _SYSTEM_PROMPT
    if _SYSTEM_PROMPT is None:
        _SYSTEM_PROMPT = load_prompt("detect_ai")
    return _SYSTEM_PROMPT


def _get_batch_system_prompt() -> str:
    global _BATCH_SYSTEM_PROMPT
    if _BATCH_SYSTEM_PROMPT is None:
        _BATCH_SYSTEM_PROMPT = load_prompt("detect_ai_batch")
    return _BATCH_SYSTEM_PROMPT


@dataclass
class LLMCallResult:
    """Result of a single LLM call including token usage."""
    score: float = 0.5
    reason: str = ""
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    raw_response: str = ""
    error: str = ""


class BaseProvider(ABC):
    """Abstract base for LLM providers."""

    @abstractmethod
    def analyze_text(self, text: str) -> LLMCallResult:
        """Return an LLMCallResult with score and token usage."""

    def analyze_batch(self, texts: list[str]) -> list[LLMCallResult]:
        """Score multiple texts in a single LLM call. Default: call analyze_text individually."""
        return [self.analyze_text(t) for t in texts]

    # ── message building ───────────────────────────────────────
    def _build_messages(self, text: str) -> list[dict[str, str]]:
        truncated = text[:1000] if text else ""
        return [
            {"role": "system", "content": _get_system_prompt()},
            {"role": "user", "content": truncated},
        ]

    def _build_batch_messages(self, texts: list[str]) -> list[dict[str, str]]:
        parts: list[str] = []
        for i, text in enumerate(texts, 1):
            truncated = text[:800] if text else ""
            parts.append(f"[{i}]\n{truncated}")
        combined = "\n\n---\n\n".join(parts)
        return [
            {"role": "system", "content": _get_batch_system_prompt()},
            {"role": "user", "content": combined},
        ]

    # ── score parsing ──────────────────────────────────────────
    @staticmethod
    def _parse_response(raw: str) -> tuple[float, str]:
        """Extract score and reason from the LLM JSON response."""
        # Strip <think>...</think> tags from reasoning models
        cleaned = re.sub(r"<think>[\s\S]*?</think>", "", raw).strip()
        # Strip markdown code fences if present
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```\w*\s*", "", cleaned)
            cleaned = re.sub(r"\s*```\s*$", "", cleaned)
            cleaned = cleaned.strip()

        try:
            data = json.loads(cleaned)
            score = float(data.get("score", 0.5))
            reason = str(data.get("reason", ""))
            return score, reason
        except (json.JSONDecodeError, ValueError, TypeError):
            # Fallback: regex extraction from raw text
            m = re.search(r'"score"\s*:\s*([\d.]+)', raw)
            score = float(m.group(1)) if m else 0.5
            # Match reason with possible escaped quotes inside
            m2 = re.search(r'"reason"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
            reason = m2.group(1) if m2 else ""
            return score, reason

    # ── shared LLM call with retries ──────────────────────────
    def _call_llm(self, client: OpenAI, model: str, text: str) -> LLMCallResult:
        """Shared implementation using the openai SDK with exponential backoff."""
        if not text or not text.strip():
            _log.debug("LLM call skipped: empty text")
            return LLMCallResult(score=0.0)

        from openai import APIStatusError, RateLimitError

        input_preview = text[:120].replace("\n", " ")
        _log.info("LLM request  | model=%s | input_len=%d | preview: %s", model, len(text), input_preview)

        max_retries = 5
        base_delay = 1.0  # seconds

        # Optional params that some models may not support
        extra_params: dict = {"max_completion_tokens": 4096}

        for attempt in range(max_retries):
            try:
                raw_resp = client.chat.completions.with_raw_response.create(
                    model=model,
                    messages=self._build_messages(text),  # type: ignore[arg-type]
                    **extra_params,
                )
                break  # success
            except RateLimitError as exc:
                delay = base_delay * (2 ** attempt)
                _log.warning(
                    "Rate limited (attempt %d/%d), retrying in %.1fs",
                    attempt + 1, max_retries, delay,
                )
                if attempt == max_retries - 1:
                    raise
                time.sleep(delay)
            except APIStatusError as exc:
                if exc.status_code == 429:
                    delay = base_delay * (2 ** attempt)
                    _log.warning(
                        "HTTP 429 (attempt %d/%d), retrying in %.1fs",
                        attempt + 1, max_retries, delay,
                    )
                    if attempt == max_retries - 1:
                        raise
                    time.sleep(delay)
                elif exc.status_code == 400 and "does not support" in str(exc):
                    # Remove the unsupported parameter and retry immediately
                    body = str(exc)
                    for param in ("temperature", "max_completion_tokens"):
                        if param in body and param in extra_params:
                            _log.warning("Model does not support '%s', removing and retrying", param)
                            del extra_params[param]
                    continue
                else:
                    raise

        resp = raw_resp.parse()
        msg = resp.choices[0].message
        content = msg.content or ""

        # If content is empty, the model may have exhausted tokens on reasoning.
        # Log raw message for debugging, but do NOT use reasoning_content as the answer.
        if not content:
            try:
                raw_data_msg = json.loads(raw_resp.text)
                msg_dict = raw_data_msg.get("choices", [{}])[0].get("message", {})
                has_reasoning = bool(msg_dict.get("reasoning_content"))
                _log.warning(
                    "LLM returned empty content (has_reasoning=%s, finish_reason=%s). "
                    "Model may need higher max_completion_tokens.",
                    has_reasoning,
                    raw_data_msg.get("choices", [{}])[0].get("finish_reason", "?"),
                )
            except Exception:
                _log.warning("LLM returned empty content")

        usage = resp.usage
        if usage and usage.total_tokens:
            prompt_tok = usage.prompt_tokens or 0
            completion_tok = usage.completion_tokens or 0
            total_tok = usage.total_tokens or 0
        else:
            raw_data = json.loads(raw_resp.text)
            u = raw_data.get("usage") or {}
            prompt_tok = u.get("prompt_tokens") or u.get("input_tokens") or 0
            completion_tok = u.get("completion_tokens") or u.get("output_tokens") or 0
            total_tok = u.get("total_tokens") or (prompt_tok + completion_tok)

        score, reason = self._parse_response(content)
        _log.info(
            "LLM response | model=%s | score=%.2f | reason=%s | prompt_tok=%d | completion_tok=%d | total_tok=%d | raw=%s",
            model, score, reason[:80], prompt_tok, completion_tok, total_tok,
            content.replace("\n", " ")[:300],
        )

        return LLMCallResult(
            score=score,
            reason=reason,
            model=model,
            prompt_tokens=prompt_tok,
            completion_tokens=completion_tok,
            total_tokens=total_tok,
            raw_response=content,
        )

    # ── batch scoring ─────────────────────────────────────────
    @staticmethod
    def _parse_batch_response(raw: str, expected: int) -> list[tuple[float, str]]:
        """Parse a batch JSON array response into (score, reason) pairs."""
        cleaned = re.sub(r"<think>[\s\S]*?</think>", "", raw).strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```\w*\s*", "", cleaned)
            cleaned = re.sub(r"\s*```\s*$", "", cleaned)
            cleaned = cleaned.strip()
        try:
            data = json.loads(cleaned)
            if isinstance(data, list):
                results: list[tuple[float, str]] = []
                for item in data:
                    score = float(item.get("score", 0.5))
                    reason = str(item.get("reason", ""))
                    results.append((score, reason))
                if len(results) == expected:
                    return results
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
        # Fallback: try to find individual JSON objects
        results = []
        for m in re.finditer(r'\{[^}]*"score"\s*:\s*([\d.]+)[^}]*"reason"\s*:\s*"((?:[^"\\]|\\.)*)"[^}]*\}', raw):
            results.append((float(m.group(1)), m.group(2)))
        if len(results) >= expected:
            return results[:expected]
        return []  # signal parse failure

    def _call_llm_batch(self, client: OpenAI, model: str, texts: list[str]) -> list[LLMCallResult]:
        """Score multiple texts in a single LLM call."""
        from openai import APIStatusError, RateLimitError

        _log.info("LLM batch request | model=%s | items=%d", model, len(texts))

        max_retries = 5
        base_delay = 1.0
        extra_params: dict = {"max_completion_tokens": 4096}

        for attempt in range(max_retries):
            try:
                raw_resp = client.chat.completions.with_raw_response.create(
                    model=model,
                    messages=self._build_batch_messages(texts),  # type: ignore[arg-type]
                    **extra_params,
                )
                break
            except RateLimitError as exc:
                delay = base_delay * (2 ** attempt)
                _log.warning("Rate limited (attempt %d/%d), retrying in %.1fs",
                             attempt + 1, max_retries, delay)
                if attempt == max_retries - 1:
                    raise
                time.sleep(delay)
            except APIStatusError as exc:
                if exc.status_code == 429:
                    delay = base_delay * (2 ** attempt)
                    _log.warning("HTTP 429 (attempt %d/%d), retrying in %.1fs",
                                 attempt + 1, max_retries, delay)
                    if attempt == max_retries - 1:
                        raise
                    time.sleep(delay)
                elif exc.status_code == 400 and "does not support" in str(exc):
                    body = str(exc)
                    for param in ("temperature", "max_completion_tokens"):
                        if param in body and param in extra_params:
                            _log.warning("Model does not support '%s', removing and retrying", param)
                            del extra_params[param]
                    continue
                else:
                    raise

        resp = raw_resp.parse()
        content = resp.choices[0].message.content or ""

        usage = resp.usage
        if usage and usage.total_tokens:
            prompt_tok = usage.prompt_tokens or 0
            completion_tok = usage.completion_tokens or 0
            total_tok = usage.total_tokens or 0
        else:
            raw_data = json.loads(raw_resp.text)
            u = raw_data.get("usage") or {}
            prompt_tok = u.get("prompt_tokens") or u.get("input_tokens") or 0
            completion_tok = u.get("completion_tokens") or u.get("output_tokens") or 0
            total_tok = u.get("total_tokens") or (prompt_tok + completion_tok)

        parsed = self._parse_batch_response(content, len(texts))
        if not parsed:
            _log.warning("Batch parse failed, content=%s", content[:300])
            raise ValueError("Failed to parse batch LLM response")

        # Distribute tokens evenly across items for logging
        per_prompt = prompt_tok // len(texts)
        per_completion = completion_tok // len(texts)
        per_total = total_tok // len(texts)

        results: list[LLMCallResult] = []
        for score, reason in parsed:
            results.append(LLMCallResult(
                score=score, reason=reason, model=model,
                prompt_tokens=per_prompt, completion_tokens=per_completion,
                total_tokens=per_total, raw_response=content[:200],
            ))

        _log.info("LLM batch response | model=%s | items=%d | scores=%s | prompt_tok=%d | total_tok=%d",
                   model, len(results), [r.score for r in results], prompt_tok, total_tok)
        return results
