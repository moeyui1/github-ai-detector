"""
Pull request processing — batch event building and single-PR analysis.
"""

from __future__ import annotations

import re
from typing import Callable

from log import get_logger
from providers.base import BaseProvider

from engine.github_api import (
    fetch_pr_comments,
    fetch_pr_commits,
    fetch_pr_reviews,
    fetch_single_pr,
)
from engine.models import (
    ActorKind,
    EventRecord,
    SingleItemResult,
    classify_actor,
)
from engine.scoring import _run_llm_tasks

_log = get_logger("engine.pulls")

# Patterns that indicate explicit AI collaboration in PR descriptions.
# These are deliberately strict — only match clear, unambiguous statements.
_AI_COLLAB_PATTERNS = re.compile(
    r"(?i)"
    r"(?:contributed|created|generated|authored|written|produced|made)\s+"
    r"(?:by|with|via|using|through)\s+"
    r"[\w\s-]*?"
    r"(?:AI\b|artificial.intelligence|copilot|GPT|LLM|claude|codex|assistant|agent)"
    r"|"
    r"\bAI[- ](?:generated|assisted|authored|created|contributed|powered)\b"
    r"|"
    r"\b(?:copilot|GPT|claude|codex|cursor|aider|devin)\s+(?:generated|wrote|created|authored)\b"
)


def _make_participant(login: str, role: str) -> dict:
    kind = classify_actor(login)
    return {"login": login, "kind": kind.value, "role": role}


def build_pr_events(
    raw_prs: list[dict],
    provider: BaseProvider | None,
    pr_scores: list[float],
    events: list[EventRecord],
    llm_tasks: list[tuple[EventRecord, str, list[float], dict | None]],
    template: str = "",
    reviews_by_pr: dict[int, list[dict]] | None = None,
) -> tuple[int, int, int, int]:
    """Process raw PRs into events.

    Reviews are NOT separate events — their content is appended to the
    parent PR's LLM text as context, and AI review counts are determined
    purely by author-name matching (L1/L2).

    Returns (total_events, bot_events, review_total, review_ai).
    """
    total = 0
    bots = 0
    review_total = 0
    review_ai = 0

    for pr in raw_prs:
        login = (pr.get("user") or {}).get("login", "unknown")
        body = pr.get("body") or ""
        title = pr.get("title") or ""
        kind = classify_actor(login)
        total += 1

        ev = EventRecord(
            kind="pr",
            title=title[:120],
            actor=login,
            actor_kind=kind,
            url=pr.get("html_url", ""),
            created_at=pr.get("created_at", ""),
        )

        # ── Count reviews by author (no LLM needed) ──────
        pr_number = pr.get("number")
        review_snippets: list[str] = []
        if reviews_by_pr and pr_number in reviews_by_pr:
            for rv in reviews_by_pr[pr_number]:
                rv_login = (rv.get("user") or {}).get("login", "unknown")
                rv_body = rv.get("body") or ""
                rv_state = rv.get("state", "")
                if not rv_body.strip():
                    continue
                review_total += 1
                rv_kind = classify_actor(rv_login)
                if rv_kind == ActorKind.AI_BOT:
                    review_ai += 1
                # Keep a short snippet of review content for LLM context
                review_snippets.append(f"[{rv_login}({rv_kind.value})/{rv_state}]: {rv_body[:200]}")

        if kind == ActorKind.SYSTEM_BOT:
            ev.ai_score = 0.0
            bots += 1
        elif kind == ActorKind.AI_BOT:
            ev.ai_score = 1.0
            bots += 1
        elif _AI_COLLAB_PATTERNS.search(body):
            # Explicit AI collaboration statement in PR body → definite AI
            ev.ai_score = 1.0
            ev.reason = "PR body explicitly mentions AI collaboration"
            _log.info("PR #%s: explicit AI collaboration detected in body", pr_number)
        elif provider:
            combined = f"Title: {title}\n\n{body}"
            if template:
                combined = f"[REPO PR TEMPLATE]:\n{template[:500]}\n\n[ACTUAL PR CONTENT]:\n{combined}"
            # Append review snippets as context (capped at 500 chars total)
            if review_snippets:
                ctx = "\n".join(review_snippets)[:500]
                combined += f"\n\n[REVIEWS]:\n{ctx}"
            llm_tasks.append((ev, combined, pr_scores, None))
        else:
            ev.ai_score = 0.0

        pr_scores.append(ev.ai_score)
        events.append(ev)

    return total, bots, review_total, review_ai


def analyze_single_pr(
    owner: str, repo: str, number: int, token: str,
    provider: BaseProvider | None, update: Callable[[str], None],
    concurrency: int,
) -> SingleItemResult:
    update("Fetching PR …")
    pr = fetch_single_pr(owner, repo, number, token)

    pr_login = (pr.get("user") or {}).get("login", "unknown")
    pr_title = pr.get("title") or ""
    pr_body = pr.get("body") or ""
    pr_kind = classify_actor(pr_login)

    ev_pr = EventRecord(
        kind="pr", title=pr_title[:120],
        actor=pr_login, actor_kind=pr_kind,
        url=pr.get("html_url", ""),
        created_at=pr.get("created_at", ""),
    )

    events: list[EventRecord] = [ev_pr]
    participants_map: dict[str, dict] = {
        pr_login: _make_participant(pr_login, "author"),
    }
    llm_tasks: list[tuple[EventRecord, str, dict | None]] = []

    # Score the PR itself
    if pr_kind == ActorKind.SYSTEM_BOT:
        ev_pr.ai_score = 0.0
    elif pr_kind == ActorKind.AI_BOT:
        ev_pr.ai_score = 1.0
    elif provider:
        llm_tasks.append((ev_pr, f"Title: {pr_title}\n\n{pr_body}", None))
    else:
        ev_pr.ai_score = 0.0

    # Fetch PR commits — check commit authors
    update("Fetching PR commits …")
    pr_commits = fetch_pr_commits(owner, repo, number, token)
    _log.info("PR #%d has %d commits", number, len(pr_commits))

    for c in pr_commits:
        c_login = (c.get("author") or {}).get("login", "unknown")
        c_msg = (c.get("commit") or {}).get("message", "")
        c_kind = classify_actor(c_login)

        ev = EventRecord(
            kind="commit", title=c_msg.split("\n")[0][:120],
            actor=c_login, actor_kind=c_kind,
            url=c.get("html_url", ""),
            created_at=(c.get("commit", {}).get("author", {}).get("date", "")),
        )

        if c_login not in participants_map:
            participants_map[c_login] = _make_participant(c_login, "commit_author")

        if c_kind == ActorKind.SYSTEM_BOT:
            ev.ai_score = 0.0
        elif c_kind == ActorKind.AI_BOT:
            ev.ai_score = 1.0
        elif provider:
            llm_tasks.append((ev, c_msg, c))
        else:
            ev.ai_score = 0.0

        events.append(ev)

    # Fetch PR comments (review comments + issue-style discussion)
    update("Fetching PR discussion …")
    comments = fetch_pr_comments(owner, repo, number, token)
    _log.info("PR #%d has %d discussion comments", number, len(comments))

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

    # Fetch PR reviews (approved/rejected)
    update("Fetching PR reviews …")
    reviews = fetch_pr_reviews(owner, repo, number, token)
    _log.info("PR #%d has %d reviews", number, len(reviews))

    for rv in reviews:
        rv_login = (rv.get("user") or {}).get("login", "unknown")
        rv_body = rv.get("body") or ""
        rv_state = rv.get("state", "")
        rv_kind = classify_actor(rv_login)

        if rv_login not in participants_map:
            participants_map[rv_login] = _make_participant(rv_login, "reviewer")

        # Only score reviews that have a body
        if rv_body.strip():
            ev = EventRecord(
                kind="review", title=f"[{rv_state}] {rv_body[:100].replace(chr(10), ' ')}",
                actor=rv_login, actor_kind=rv_kind,
                url=rv.get("html_url", ""),
                created_at=rv.get("submitted_at", ""),
            )
            if rv_kind == ActorKind.SYSTEM_BOT:
                ev.ai_score = 0.0
            elif rv_kind == ActorKind.AI_BOT:
                ev.ai_score = 1.0
            elif provider:
                llm_tasks.append((ev, rv_body, None))
            else:
                ev.ai_score = 0.0
            events.append(ev)

    # Run LLM
    logs = _run_llm_tasks(llm_tasks, provider, concurrency, update) if provider else []

    result = SingleItemResult(
        item_type="pr",
        item_title=f"PR #{number}: {pr_title}",
        item_url=pr.get("html_url", ""),
        repo_name=f"{owner}/{repo}",
        events=events,
        llm_logs=logs,
        participants=list(participants_map.values()),
    )
    _log.info("analyze_single DONE | PR #%d | events=%d | participants=%d",
              number, len(events), len(result.participants))
    return result
