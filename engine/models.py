"""
Data models and bot classification for GitHub AI-Radar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from config import get_config

# ────────────────────────────────────────────────────────────────
# Layer 1 & 2 — bot lists (loaded from config.toml)
# ────────────────────────────────────────────────────────────────

_cfg_bots = get_config().bots
SYSTEM_BOT_LIST: set[str] = set(_cfg_bots.system)
AI_BOT_LIST: set[str] = set(_cfg_bots.ai)


class ActorKind(str, Enum):
    SYSTEM_BOT = "system_bot"
    AI_BOT = "ai_bot"
    HUMAN = "human"


def classify_actor(login: str) -> ActorKind:
    low = login.lower()
    if low in AI_BOT_LIST:
        return ActorKind.AI_BOT
    if low in SYSTEM_BOT_LIST or (low.endswith("[bot]") and low not in AI_BOT_LIST):
        return ActorKind.SYSTEM_BOT
    return ActorKind.HUMAN


# ────────────────────────────────────────────────────────────────
# Data models
# ────────────────────────────────────────────────────────────────

@dataclass
class EventRecord:
    kind: str  # "commit" | "pr" | "issue"
    title: str
    actor: str
    actor_kind: ActorKind
    ai_score: float = 0.0
    reason: str = ""
    url: str = ""
    created_at: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMLogEntry:
    """One LLM call log entry for display."""
    event_title: str
    event_kind: str
    score: float
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    model: str = ""
    raw_response: str = ""
    error: str = ""


@dataclass
class AnalysisResult:
    repo_name: str
    events: list[EventRecord] = field(default_factory=list)
    llm_logs: list[LLMLogEntry] = field(default_factory=list)
    # Legacy average scores (kept for AII computation)
    s_commit: float = 0.0
    s_pr: float = 0.0
    s_review: float = 0.0
    s_issue: float = 0.0
    bot_rate: float = 0.0
    aii: float = 0.0
    # Count-based metrics — AI participation from multiple angles
    commit_total: int = 0
    commit_ai: int = 0       # ai_score >= threshold
    pr_total: int = 0
    pr_ai: int = 0
    review_total: int = 0
    review_ai: int = 0
    issue_comment_total: int = 0
    issue_comment_ai: int = 0


@dataclass
class SingleItemResult:
    """Result of analysing a single PR / issue / commit."""
    item_type: str   # "pr" | "issue" | "commit"
    item_title: str
    item_url: str
    repo_name: str
    events: list[EventRecord] = field(default_factory=list)
    llm_logs: list[LLMLogEntry] = field(default_factory=list)
    participants: list[dict] = field(default_factory=list)  # [{login, kind, role}]
