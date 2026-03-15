"""
GitHub Models (Copilot) provider.
"""

from __future__ import annotations

import os

from openai import OpenAI

from config import get_config
from log import get_logger
from providers.base import BaseProvider, LLMCallResult

_log = get_logger("providers.github")


class GitHubModelsProvider(BaseProvider):
    """GitHub Models endpoint (uses GITHUB_TOKEN for auth)."""

    BASE_URL = "https://models.github.ai/inference"

    def __init__(
        self,
        token: str | None = None,
        model: str | None = None,
    ):
        _cfg = get_config()
        self.model = model or _cfg.llm.model
        self.client = OpenAI(
            api_key=token or _cfg.github.token or os.environ.get("GITHUB_TOKEN", ""),
            base_url=self.BASE_URL,
        )
        _log.info("GitHubModelsProvider init | model=%s | base_url=%s", self.model, self.BASE_URL)

    def analyze_text(self, text: str) -> LLMCallResult:
        return self._call_llm(self.client, self.model, text)

    def analyze_batch(self, texts: list[str]) -> list[LLMCallResult]:
        return self._call_llm_batch(self.client, self.model, texts)
