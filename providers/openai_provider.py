"""
OpenAI-compatible chat completions provider.
"""

from __future__ import annotations

import os

from openai import OpenAI

from config import get_config
from log import get_logger
from providers.base import BaseProvider, LLMCallResult

_log = get_logger("providers.openai")


class OpenAIProvider(BaseProvider):
    """OpenAI‑compatible chat completions."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ):
        _llm = get_config().llm
        self.model = model or _llm.model
        self.client = OpenAI(
            api_key=api_key or _llm.api_key or os.environ.get("OPENAI_API_KEY", ""),
            base_url=base_url or _llm.base_url,
        )
        _log.info("OpenAIProvider init | model=%s | base_url=%s", self.model, self.client.base_url)

    def analyze_text(self, text: str) -> LLMCallResult:
        return self._call_llm(self.client, self.model, text)

    def analyze_batch(self, texts: list[str]) -> list[LLMCallResult]:
        return self._call_llm_batch(self.client, self.model, texts)
