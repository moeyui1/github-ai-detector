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
# AI tool identification
# ────────────────────────────────────────────────────────────────

# Maps bot login (lowercase) → human-readable AI tool name.
AI_TOOL_NAMES: dict[str, str] = {
    "github-copilot[bot]": "GitHub Copilot",
    "copilot[bot]": "GitHub Copilot",
    "copilot-swe-agent[bot]": "GitHub Copilot",
    "copilot-pull-request-reviewer[bot]": "GitHub Copilot",
    "coderabbitai[bot]": "CodeRabbit",
    "coderabbitai": "CodeRabbit",
    "codiumai-pr-agent-pro[bot]": "CodiumAI PR-Agent",
    "pr-agent[bot]": "PR-Agent",
    "sourcery-ai[bot]": "Sourcery",
    "ellipsis-dev[bot]": "Ellipsis",
    "sweep-ai[bot]": "Sweep AI",
    "deepsource-autofix[bot]": "DeepSource",
    "claude-by-anthropic[bot]": "Claude",
    "devin-ai[bot]": "Devin",
    "cursor[bot]": "Cursor",
    "aider-bot[bot]": "Aider",
    "greptileai[bot]": "Greptile",
    "korbit-ai[bot]": "Korbit",
    "bito-ai[bot]": "Bito",
    "tabnine[bot]": "Tabnine",
    "chatgpt-codex-connector[bot]": "ChatGPT Codex",
    "openai-codex[bot]": "OpenAI Codex",
    "codex[bot]": "OpenAI Codex",
}

# Patterns to extract AI tool names from commit trailers or PR body text.
import re as _re
_TOOL_EXTRACT_RE = _re.compile(
    r"(?i)\b(copilot|github\s*copilot|claude|chatgpt|gpt-?4|gpt-?3|gemini|codex"
    r"|cursor|aider|devin|sweep|coderabbit|tabnine|sourcery|bito)\b"
)


def get_ai_tool(login: str, reason: str = "", title: str = "") -> str | None:
    """Return the AI tool name for an event, or None if not AI-related.

    Priority: bot login mapping > keyword extraction from reason/title.
    """
    low = login.lower()
    if low in AI_TOOL_NAMES:
        return AI_TOOL_NAMES[low]
    # Try to extract tool from reason or title (for trailer-detected / LLM-detected)
    for text in (reason, title):
        m = _TOOL_EXTRACT_RE.search(text)
        if m:
            raw = m.group(1).strip()
            return _normalise_tool(raw)
    return None


def _normalise_tool(raw: str) -> str:
    """Normalise a raw tool mention to a consistent display name."""
    key = raw.lower().replace(" ", "")
    mapping = {
        "copilot": "GitHub Copilot",
        "githubcopilot": "GitHub Copilot",
        "claude": "Claude",
        "chatgpt": "ChatGPT",
        "gpt4": "ChatGPT",
        "gpt-4": "ChatGPT",
        "gpt3": "ChatGPT",
        "gpt-3": "ChatGPT",
        "gemini": "Gemini",
        "codex": "OpenAI Codex",
        "cursor": "Cursor",
        "aider": "Aider",
        "devin": "Devin",
        "sweep": "Sweep AI",
        "coderabbit": "CodeRabbit",
        "tabnine": "Tabnine",
        "sourcery": "Sourcery",
        "bito": "Bito",
    }
    return mapping.get(key, raw.title())


# Maps AI tool display name → GitHub app/user slug for avatar & profile link.
# Format: tool_name → (github_slug, is_app)
# is_app=True → https://github.com/apps/{slug}, avatar from /in/{app_id}
# is_app=False → https://github.com/{slug}, avatar from github.com/{slug}.png
AI_TOOL_GITHUB: dict[str, tuple[str, str]] = {
    "GitHub Copilot":    ("https://github.com/apps/copilot",      "https://avatars.githubusercontent.com/in/29110?s=80"),
    "Claude":            ("https://github.com/apps/claude",       "https://avatars.githubusercontent.com/in/1167075?s=80"),
    "ChatGPT":           ("https://github.com/apps/chatgpt-codex-connector", "https://avatars.githubusercontent.com/in/1144995?s=80"),
    "ChatGPT Codex":     ("https://github.com/apps/chatgpt-codex-connector", "https://avatars.githubusercontent.com/in/1144995?s=80"),
    "OpenAI Codex":      ("https://github.com/apps/chatgpt-codex-connector", "https://avatars.githubusercontent.com/in/1144995?s=80"),
    "Cursor":            ("https://github.com/getcursor",         "https://avatars.githubusercontent.com/u/217993994?s=80"),
    "Devin":             ("https://github.com/apps/devin-ai",     "https://avatars.githubusercontent.com/in/886141?s=80"),
    "Sweep AI":          ("https://github.com/apps/sweep-ai",     "https://avatars.githubusercontent.com/in/526031?s=80"),
    "CodeRabbit":        ("https://github.com/apps/coderabbitai", "https://avatars.githubusercontent.com/in/480193?s=80"),
    "CodiumAI PR-Agent": ("https://github.com/apps/codiumai-pr-agent-pro", "https://avatars.githubusercontent.com/in/665708?s=80"),
    "PR-Agent":          ("https://github.com/apps/pr-agent",     "https://avatars.githubusercontent.com/in/382459?s=80"),
    "Sourcery":          ("https://github.com/apps/sourcery-ai",  "https://avatars.githubusercontent.com/in/30730?s=80"),
    "Greptile":          ("https://github.com/apps/greptile-apps","https://avatars.githubusercontent.com/in/867647?s=80"),
    "DeepSource":        ("https://github.com/apps/deepsource-autofix", "https://avatars.githubusercontent.com/in/57905?s=80"),
    "Tabnine":           ("https://github.com/tabnine",           "https://avatars.githubusercontent.com/u/73937337?s=80"),
    "Aider":             ("https://github.com/Aider-AI/aider",   "https://avatars.githubusercontent.com/u/172139148?s=80"),
    "Gemini":            ("https://github.com/google-gemini",     "https://avatars.githubusercontent.com/u/161781182?s=80"),
    "Bito":              ("https://github.com/apps/bito-ai",      "https://avatars.githubusercontent.com/in/382826?s=80"),
    "Korbit":            ("https://github.com/apps/korbit-ai",    "https://avatars.githubusercontent.com/in/453844?s=80"),
    "Ellipsis":          ("https://github.com/apps/ellipsis-dev", "https://avatars.githubusercontent.com/in/538928?s=80"),
}


def get_ai_tool_profile(tool_name: str) -> tuple[str, str] | None:
    """Return (github_url, avatar_url) for a known AI tool, or None."""
    return AI_TOOL_GITHUB.get(tool_name)


# ────────────────────────────────────────────────────────────────
# Data models
# ────────────────────────────────────────────────────────────────

@dataclass
class EventRecord:
    kind: str  # "commit" | "pr"
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
    bot_rate: float = 0.0
    aii: float = 0.0
    # Count-based metrics — AI participation from multiple angles
    commit_total: int = 0
    commit_ai: int = 0       # ai_score >= threshold
    pr_total: int = 0
    pr_ai: int = 0
    review_total: int = 0
    review_ai: int = 0


@dataclass
class SingleItemResult:
    """Result of analysing a single PR / commit."""
    item_type: str   # "pr" | "commit"
    item_title: str
    item_url: str
    repo_name: str
    events: list[EventRecord] = field(default_factory=list)
    llm_logs: list[LLMLogEntry] = field(default_factory=list)
    participants: list[dict] = field(default_factory=list)  # [{login, kind, role}]
