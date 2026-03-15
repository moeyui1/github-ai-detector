"""
Issue processing — batch event building and single-issue analysis.
"""

from __future__ import annotations

from typing import Callable

from log import get_logger
from providers.base import BaseProvider

from engine.github_api import fetch_issue_comments, fetch_single_issue
from engine.models import (
    ActorKind,
    EventRecord,
    SingleItemResult,
    classify_actor,
)
from engine.scoring import _run_llm_tasks

_log = get_logger("engine.issues")


def _make_participant(login: str, role: str) -> dict:
    kind = classify_actor(login)
    return {"login": login, "kind": kind.value, "role": role}


def build_issue_events(
    raw_issues: list[dict],
    provider: BaseProvider | None,
    issue_scores: list[float],
    events: list[EventRecord],
    llm_tasks: list[tuple[EventRecord, str, list[float], dict | None]],
    template: str = "",
    comments_by_issue: dict[int, list[dict]] | None = None,
) -> tuple[int, int, int, int]:
    """Count issue comment AI participation by author-name matching (L1/L2).

    Issues themselves are NOT analysed as events — only their comments are
    inspected, and purely by account name (no LLM).

    Returns (0, 0, issue_comment_total, issue_comment_ai).
    """
    comment_total = 0
    comment_ai = 0

    for iss in raw_issues:
        if "pull_request" in iss:
            continue
        issue_number = iss.get("number")
        if comments_by_issue and issue_number in comments_by_issue:
            for cm in comments_by_issue[issue_number]:
                cm_login = (cm.get("user") or {}).get("login", "unknown")
                cm_body = cm.get("body") or ""
                if not cm_body.strip():
                    continue
                comment_total += 1
                cm_kind = classify_actor(cm_login)
                if cm_kind == ActorKind.AI_BOT:
                    comment_ai += 1

    return 0, 0, comment_total, comment_ai


def analyze_single_issue(
    owner: str, repo: str, number: int, token: str,
    provider: BaseProvider | None, update: Callable[[str], None],
    concurrency: int,
) -> SingleItemResult:
    update("Fetching issue …")
    iss = fetch_single_issue(owner, repo, number, token)

    iss_login = (iss.get("user") or {}).get("login", "unknown")
    iss_title = iss.get("title") or ""
    iss_body = iss.get("body") or ""
    iss_kind = classify_actor(iss_login)

    ev_iss = EventRecord(
        kind="issue", title=iss_title[:120],
        actor=iss_login, actor_kind=iss_kind,
        url=iss.get("html_url", ""),
        created_at=iss.get("created_at", ""),
    )

    events: list[EventRecord] = [ev_iss]
    participants_map: dict[str, dict] = {
        iss_login: _make_participant(iss_login, "author"),
    }
    llm_tasks: list[tuple[EventRecord, str, dict | None]] = []

    # Score the issue itself
    if iss_kind == ActorKind.SYSTEM_BOT:
        ev_iss.ai_score = 0.0
    elif iss_kind == ActorKind.AI_BOT:
        ev_iss.ai_score = 1.0
    elif provider:
        llm_tasks.append((ev_iss, f"Title: {iss_title}\n\n{iss_body}", None))
    else:
        ev_iss.ai_score = 0.0

    # Fetch issue comments (discussion)
    update("Fetching issue discussion …")
    comments = fetch_issue_comments(owner, repo, number, token)
    _log.info("Issue #%d has %d comments", number, len(comments))

    for cm in comments:
        cm_login = (cm.get("user") or {}).get("login", "unknown")
        cm_body = cm.get("body") or ""
        cm_kind = classify_actor(cm_login)

        ev = EventRecord(
            kind="comment", title=cm_body[:120].replace("\n", " "),
            actor=cm_login, actor_kind=cm_kind,
            url=cm.get("html_url", ""),
            created_at=cm.get("created_at", ""),
        )

        if cm_login not in participants_map:
            participants_map[cm_login] = _make_participant(cm_login, "commenter")

        if cm_kind == ActorKind.SYSTEM_BOT:
            ev.ai_score = 0.0
        elif cm_kind == ActorKind.AI_BOT:
            ev.ai_score = 1.0
        elif provider:
            llm_tasks.append((ev, cm_body, None))
        else:
            ev.ai_score = 0.0

        events.append(ev)

    # Run LLM
    logs = _run_llm_tasks(llm_tasks, provider, concurrency, update) if provider else []

    result = SingleItemResult(
        item_type="issue",
        item_title=f"Issue #{number}: {iss_title}",
        item_url=iss.get("html_url", ""),
        repo_name=f"{owner}/{repo}",
        events=events,
        llm_logs=logs,
        participants=list(participants_map.values()),
    )
    _log.info("analyze_single DONE | Issue #%d | events=%d | participants=%d",
              number, len(events), len(result.participants))
    return result
