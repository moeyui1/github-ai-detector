"""
LLM Provider abstraction layer.
Supports OpenAI and GitHub Models (Copilot) backends.
"""

from providers.base import BaseProvider, LLMCallResult
from providers.github_provider import GitHubModelsProvider
from providers.openai_provider import OpenAIProvider

__all__ = [
    "BaseProvider",
    "LLMCallResult",
    "OpenAIProvider",
    "GitHubModelsProvider",
    "get_provider",
]


def get_provider(name: str, **kwargs) -> BaseProvider:
    """Factory helper."""
    providers = {
        "openai": OpenAIProvider,
        "github": GitHubModelsProvider,
    }
    cls = providers.get(name.lower())
    if cls is None:
        raise ValueError(f"Unknown provider: {name}. Choose from: {list(providers)}")
    return cls(**kwargs)
