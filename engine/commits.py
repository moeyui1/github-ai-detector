"""
Commit processing — batch event building and single-commit analysis.
"""

from __future__ import annotations

import re
from typing import Callable

from log import get_logger
from providers.base import BaseProvider

# Git trailers that indicate explicit AI assistance in commit messages.
# Matches lines like "Assisted-by: Gemini:gemini-3.1-pro", "Co-authored-by: ... Copilot",
# "Generated-by: ChatGPT", etc.
_AI_TRAILER_RE = re.compile(
    r"(?mi)"
    r"^(?:assisted|co-authored|generated|created|authored|produced|aided)"
    r"-by:\s*.*?"
    r"(?:AI\b|copilot|GPT|gemini|claude|codex|cursor|aider|devin|LLM|chatgpt|assistant|agent)"
)

from engine.github_api import fetch_single_commit
from engine.models import (
    ActorKind,
    EventRecord,
    SingleItemResult,
    classify_actor,
)
from engine.scoring import _run_llm_tasks, _safe_llm_score

_log = get_logger("engine.commits")


def _make_participant(login: str, role: str) -> dict:
    kind = classify_actor(login)
    return {"login": login, "kind": kind.value, "role": role}


def build_commit_events(
    raw_commits: list[dict],
    provider: BaseProvider | None,
    commit_scores: list[float],
    events: list[EventRecord],
    llm_tasks: list[tuple[EventRecord, str, list[float], dict | None]],
) -> tuple[int, int]:
    """Process raw commits into events. Returns (total, bot) counts."""
    total = 0
    bots = 0
    for c in raw_commits:
        author_login = (c.get("author") or {}).get("login", "unknown")
        msg = (c.get("commit") or {}).get("message", "")

        kind = classify_actor(author_login)
        total += 1

        ev = EventRecord(
            kind="commit",
            title=msg.split("\n")[0][:120],
            actor=author_login,
            actor_kind=kind,
            url=c.get("html_url", ""),
            created_at=(c.get("commit", {}).get("author", {}).get("date", "")),
        )

        if kind == ActorKind.SYSTEM_BOT:
            ev.ai_score = 0.0
            bots += 1
        elif kind == ActorKind.AI_BOT:
            ev.ai_score = 1.0
            bots += 1
        elif _AI_TRAILER_RE.search(msg):
            # Explicit AI assistance trailer in commit message → definite AI
            ev.ai_score = 1.0
            ev.reason = "Commit message contains explicit AI assistance trailer"
            _log.info("Commit %s: explicit AI trailer detected", c.get("sha", "")[:8])
        elif provider:
            llm_tasks.append((ev, msg, commit_scores, c))
        else:
            ev.ai_score = 0.0

        commit_scores.append(ev.ai_score)
        events.append(ev)

    return total, bots


def analyze_single_commit(
    owner: str, repo: str, sha: str, token: str,
    provider: BaseProvider | None, update: Callable[[str], None],
    concurrency: int,
) -> SingleItemResult:
    update("Fetching commit …")
    c = fetch_single_commit(owner, repo, sha, token)

    author_login = (c.get("author") or {}).get("login", "unknown")
    committer_login = (c.get("committer") or {}).get("login", "unknown")
    msg = (c.get("commit") or {}).get("message", "")
    kind = classify_actor(author_login)

    ev = EventRecord(
        kind="commit", title=msg.split("\n")[0][:120],
        actor=author_login, actor_kind=kind,
        url=c.get("html_url", ""),
        created_at=(c.get("commit", {}).get("author", {}).get("date", "")),
    )

    participants = [
        _make_participant(author_login, "author"),
    ]
    if committer_login != author_login:
        participants.append(_make_participant(committer_login, "committer"))

    llm_tasks: list[tuple[EventRecord, str, dict | None]] = []
    if kind == ActorKind.SYSTEM_BOT:
        ev.ai_score = 0.0
    elif kind == ActorKind.AI_BOT:
        ev.ai_score = 1.0
    elif provider:
        llm_tasks.append((ev, msg, c))
    else:
        ev.ai_score = 0.0

    logs = _run_llm_tasks(llm_tasks, provider, concurrency, update) if provider else []

    result = SingleItemResult(
        item_type="commit",
        item_title=ev.title,
        item_url=ev.url,
        repo_name=f"{owner}/{repo}",
        events=[ev],
        llm_logs=logs,
        participants=participants,
    )
    _log.info("analyze_single DONE | commit %s | score=%.2f", sha[:8], ev.ai_score)
    return result
