"""
GitHub AI-Radar analysis engine.
Implements three‑layer identification and multi‑dimensional scoring.
"""

from engine.analysis import analyze_repo, analyze_single, parse_item_url, parse_repo_url
from engine.cache import CacheData, CacheRepo, load_cache, save_cache
from engine.models import (
    ActorKind,
    AnalysisResult,
    EventRecord,
    LLMLogEntry,
    SingleItemResult,
    classify_actor,
)

__all__ = [
    "ActorKind",
    "AnalysisResult",
    "EventRecord",
    "LLMLogEntry",
    "SingleItemResult",
    "analyze_repo",
    "analyze_single",
    "classify_actor",
    "parse_item_url",
    "parse_repo_url",
]
