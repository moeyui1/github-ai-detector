"""
Main analysis pipelines — analyze_repo and analyze_single.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

import httpx

from config import get_config
from log import get_logger
from providers.base import BaseProvider

from engine.cache import CacheRepo, event_key, event_updated_at
from engine.commits import analyze_single_commit, build_commit_events
from engine.github_api import _gh_get_one, fetch_commits, fetch_issue_comments_batch, fetch_pulls_and_issues, fetch_pr_reviews_batch, fetch_repo_templates
from engine.issues import analyze_single_issue, build_issue_events
from engine.models import (
    ActorKind,
    AnalysisResult,
    EventRecord,
    LLMLogEntry,
    SingleItemResult,
)
from engine.pulls import analyze_single_pr, build_pr_events
from engine.scoring import _rebase_penalty, _safe_llm_score, _safe_llm_score_batch

_log = get_logger("engine.analysis")


def parse_repo_url(url: str) -> tuple[str, str]:
    """Extract (owner, repo) from a GitHub URL or 'owner/repo' string."""
    url = url.strip().rstrip("/")
    m = re.match(r"(?:https?://github\.com/)?([A-Za-z0-9_.\-]+)/([A-Za-z0-9_.\-]+)", url)
    if not m:
        raise ValueError(f"Cannot parse repo from: {url}")
    return m.group(1), m.group(2)


def parse_item_url(url: str) -> tuple[str, str, str, str]:
    """Parse a GitHub URL into (owner, repo, item_type, identifier).

    Supported formats:
      https://github.com/owner/repo/pull/123
      https://github.com/owner/repo/issues/456
      https://github.com/owner/repo/commit/abc123
      owner/repo#123  (treated as issue/PR)
    """
    url = url.strip().rstrip("/")

    # Full URL patterns
    m = re.match(
        r"https?://github\.com/([A-Za-z0-9_.\-]+)/([A-Za-z0-9_.\-]+)/"
        r"(pull|issues|commit)/([A-Za-z0-9]+)",
        url,
    )
    if m:
        owner, repo, kind, ident = m.group(1), m.group(2), m.group(3), m.group(4)
        kind_map = {"pull": "pr", "issues": "issue", "commit": "commit"}
        return owner, repo, kind_map[kind], ident

    # Shorthand: owner/repo#123
    m = re.match(r"([A-Za-z0-9_.\-]+)/([A-Za-z0-9_.\-]+)#(\d+)", url)
    if m:
        return m.group(1), m.group(2), "issue_or_pr", m.group(3)

    raise ValueError(f"Cannot parse item URL: {url}")


def _find_event_key(ev: EventRecord, raw_lookup: dict[str, tuple[dict, str]]) -> str | None:
    """Find the cache key for an EventRecord by matching URL or title."""
    for key, (raw, kind) in raw_lookup.items():
        if kind == "commit" and ev.kind == "commit":
            sha = raw.get("sha", "")
            if sha and ev.url and sha in ev.url:
                return key
        elif kind == ev.kind:
            num = raw.get("number")
            if num and str(num) in key:
                title = (raw.get("title") or "")[:120]
                if title == ev.title:
                    return key
    return None


def analyze_repo(
    owner: str,
    repo: str,
    token: str,
    provider: BaseProvider | None = None,
    max_items: int = 50,
    progress_callback: Callable[[str], None] | None = None,
    concurrency: int | None = None,
    cache: CacheRepo | None = None,
) -> tuple[AnalysisResult, CacheRepo]:
    """Run the full 3-layer analysis on a repository.

    *cache*: previous analysis cache for this repo (event_key -> entry).
    Returns (result, updated_cache) — caller is responsible for saving.
    """
    if concurrency is None:
        concurrency = get_config().llm.concurrency

    repo_name = f"{owner}/{repo}"
    _log.info("analyze_repo START | repo=%s | max_items=%d | concurrency=%d | llm=%s | cache=%d entries",
              repo_name, max_items, concurrency,
              type(provider).__name__ if provider else "None",
              len(cache) if cache else 0)

    result = AnalysisResult(repo_name=repo_name)
    total_events = 0
    bot_events = 0
    cache = dict(cache) if cache else {}
    new_cache: CacheRepo = {}

    def _update(msg: str):
        if progress_callback:
            progress_callback(msg)

    # ── Fetch data ────────────────────────────────────────────
    _update("Fetching commits …")
    raw_commits = fetch_commits(owner, repo, token, max_items=max_items,
                                max_pages=get_config().analysis.max_pages)
    _log.info("Fetched %d commits", len(raw_commits))
    _update("Fetching pull requests & issues …")
    raw_prs, raw_issues = fetch_pulls_and_issues(owner, repo, token, max_items=max_items)
    _log.info("Fetched %d pull requests, %d issues", len(raw_prs), len(raw_issues))

    # ── Fetch repo templates (for PR/Issue accuracy) ──────
    templates: dict[str, str] = {}
    if provider:
        _update("Fetching repo templates …")
        templates = fetch_repo_templates(owner, repo, token)
        if templates:
            _log.info("Repo templates found: %s", list(templates.keys()))

    # ── Build events & collect LLM tasks ───────────────────
    commit_scores: list[float] = []
    pr_scores: list[float] = []
    issue_scores: list[float] = []

    llm_tasks: list[tuple[EventRecord, str, list[float], dict | None]] = []

    t, b = build_commit_events(raw_commits, provider, commit_scores, result.events, llm_tasks)
    total_events += t
    bot_events += b

    reviews_map: dict[int, list[dict]] = {}
    if provider and raw_prs:
        # Only fetch reviews for PRs not in cache (saves API calls on repeat runs)
        uncached_pr_numbers = []
        for pr in raw_prs:
            num = pr.get("number")
            if not num:
                continue
            key = event_key("pr", pr)
            cur_updated = event_updated_at("pr", pr)
            prev = cache.get(key)
            if prev and cur_updated == prev.get("updated_at", ""):
                continue  # cached, skip review fetch
            uncached_pr_numbers.append(num)
        if uncached_pr_numbers:
            _update(f"Fetching reviews for {len(uncached_pr_numbers)} uncached PRs …")
            reviews_map = fetch_pr_reviews_batch(owner, repo, token, uncached_pr_numbers)
        else:
            _log.info("All %d PRs cached, skipping review fetch", len(raw_prs))

    t, b, rv_total, rv_ai = build_pr_events(raw_prs, provider, pr_scores, result.events, llm_tasks,
                           template=templates.get("pr", ""), reviews_by_pr=reviews_map)
    total_events += t
    bot_events += b
    result.review_total = rv_total
    result.review_ai = rv_ai

    comments_map: dict[int, list[dict]] = {}
    if raw_issues:
        comment_numbers = [
            iss["number"] for iss in raw_issues
            if iss.get("number") and iss.get("comments", 0) > 0
        ]
        if comment_numbers:
            _update(f"Fetching comments for {len(comment_numbers)} issues …")
            comments_map = fetch_issue_comments_batch(owner, repo, token, comment_numbers)

    t, b, ic_total, ic_ai = build_issue_events(raw_issues, provider, issue_scores, result.events, llm_tasks,
                              template=templates.get("issue", ""), comments_by_issue=comments_map)
    total_events += t
    bot_events += b
    result.issue_comment_total = ic_total
    result.issue_comment_ai = ic_ai

    # ── Apply cache: skip unchanged events ────────────────────
    _raw_lookup: dict[str, tuple[dict, str]] = {}
    for c in raw_commits:
        _raw_lookup[event_key("commit", c)] = (c, "commit")
    for pr in raw_prs:
        _raw_lookup[event_key("pr", pr)] = (pr, "pr")
    for iss in raw_issues:
        if "pull_request" not in iss:
            _raw_lookup[event_key("issue", iss)] = (iss, "issue")

    cached_count = 0
    remaining_tasks: list[tuple[EventRecord, str, list[float], dict | None]] = []

    for task in llm_tasks:
        ev, text, score_list, commit = task
        key = _find_event_key(ev, _raw_lookup)
        if key and key in cache:
            raw_dict = _raw_lookup[key][0]
            cur_updated = event_updated_at(_raw_lookup[key][1], raw_dict)
            prev = cache[key]
            if cur_updated == prev.get("updated_at", ""):
                ev.ai_score = prev["ai_score"]
                ev.reason = prev.get("reason", "")
                cached_count += 1
                new_cache[key] = prev
                continue
        remaining_tasks.append(task)

    if cached_count:
        _log.info("Cache hit: %d/%d events reused, %d need LLM", cached_count, len(llm_tasks), len(remaining_tasks))
        _update(f"Cache hit: {cached_count} reused, {len(remaining_tasks)} need LLM …")

    # ── Batch LLM scoring (L3) ───────────────────────────────
    if remaining_tasks and provider:
        _log.info("L3 LLM audit starting | %d tasks | concurrency=%d", len(remaining_tasks), concurrency)
        _update(f"Running L3 LLM audit on {len(remaining_tasks)} events (concurrency={concurrency}) …")

        batch_size = 10
        batches: list[list[tuple[EventRecord, str, list[float], dict | None]]] = []
        for i in range(0, len(remaining_tasks), batch_size):
            batches.append(remaining_tasks[i:i + batch_size])

        done_count = 0
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            def _score_batch(batch: list[tuple[EventRecord, str, list[float], dict | None]]) -> list[LLMLogEntry]:
                texts = [text for _, text, _, _ in batch]
                results_list = _safe_llm_score_batch(provider, texts)
                logs: list[LLMLogEntry] = []
                for (ev, _, _, commit), llm_result in zip(batch, results_list):
                    raw_score = llm_result.score
                    if commit is not None:
                        penalty = _rebase_penalty(commit)
                        raw_score = max(0.0, raw_score - penalty)
                    ev.ai_score = raw_score
                    ev.reason = llm_result.reason
                    logs.append(LLMLogEntry(
                        event_title=ev.title,
                        event_kind=ev.kind,
                        score=raw_score,
                        prompt_tokens=llm_result.prompt_tokens,
                        completion_tokens=llm_result.completion_tokens,
                        total_tokens=llm_result.total_tokens,
                        model=llm_result.model,
                        raw_response=llm_result.raw_response,
                        error=llm_result.error,
                    ))
                return logs

            futures = {pool.submit(_score_batch, b): b for b in batches}
            for future in as_completed(futures):
                batch_logs = future.result()
                result.llm_logs.extend(batch_logs)
                done_count += len(batch_logs)
                _update(f"LLM audit {done_count}/{len(remaining_tasks)} …")

    # ── Update cache with all current events ──────────────────
    for ev in result.events:
        key = _find_event_key(ev, _raw_lookup)
        if key and key not in new_cache:
            raw_dict, kind = _raw_lookup[key]
            new_cache[key] = {
                "updated_at": event_updated_at(kind, raw_dict),
                "ai_score": ev.ai_score,
                "reason": ev.reason,
            }

    # ── Rebuild per-dimension scores ──────────────────────────
    commit_scores.clear()
    pr_scores.clear()
    issue_scores.clear()
    for ev in result.events:
        if ev.kind == "commit":
            commit_scores.append(ev.ai_score)
        elif ev.kind == "pr":
            pr_scores.append(ev.ai_score)
        elif ev.kind == "issue":
            issue_scores.append(ev.ai_score)

    # ── Aggregate scores ──────────────────────────────────────
    result.s_commit = (sum(commit_scores) / len(commit_scores)) if commit_scores else 0.0
    result.s_pr = (sum(pr_scores) / len(pr_scores)) if pr_scores else 0.0
    result.s_review = (result.review_ai / result.review_total) if result.review_total else 0.0
    result.s_issue = (sum(issue_scores) / len(issue_scores)) if issue_scores else 0.0
    result.bot_rate = (bot_events / total_events) if total_events else 0.0

    # AII = (w_commit*S_commit + w_pr*S_pr + w_review*S_review) * (1 - Bot_Rate)
    w = get_config().analysis.weights
    raw_aii = w.commit * result.s_commit + w.pr * result.s_pr + w.review * result.s_review
    result.aii = round(raw_aii * (1 - result.bot_rate), 4)

    # ── Count-based metrics ───────────────────────────────────
    ai_threshold = get_config().analysis.high_risk_threshold
    for ev in result.events:
        is_ai = ev.ai_score >= ai_threshold or ev.actor_kind == ActorKind.AI_BOT
        if ev.kind == "commit":
            result.commit_total += 1
            if is_ai:
                result.commit_ai += 1
        elif ev.kind == "pr":
            result.pr_total += 1
            if is_ai:
                result.pr_ai += 1

    _log.info(
        "analyze_repo DONE | repo=%s | events=%d | bots=%d | aii=%.4f | "
        "cached=%d | llm_calls=%d",
        result.repo_name, total_events, bot_events, result.aii,
        cached_count, len(remaining_tasks),
    )

    return result, new_cache


def analyze_single(
    owner: str,
    repo: str,
    item_type: str,
    identifier: str,
    token: str,
    provider: BaseProvider | None = None,
    progress_callback: Callable[[str], None] | None = None,
    concurrency: int | None = None,
) -> SingleItemResult:
    """Analyse a single PR, issue, or commit with full context."""
    if concurrency is None:
        concurrency = get_config().llm.concurrency

    def _update(msg: str):
        if progress_callback:
            progress_callback(msg)

    _log.info("analyze_single START | %s/%s | type=%s | id=%s", owner, repo, item_type, identifier)

    # For owner/repo#N shorthand, determine if it's a PR or issue
    if item_type == "issue_or_pr":
        _update("Detecting item type …")
        try:
            _gh_get_one(f"/repos/{owner}/{repo}/pulls/{identifier}", token)
            item_type = "pr"
        except httpx.HTTPStatusError:
            item_type = "issue"
        _log.info("Resolved item_type=%s for #%s", item_type, identifier)

    if item_type == "commit":
        return analyze_single_commit(owner, repo, identifier, token, provider, _update, concurrency)
    elif item_type == "pr":
        return analyze_single_pr(owner, repo, int(identifier), token, provider, _update, concurrency)
    elif item_type == "issue":
        return analyze_single_issue(owner, repo, int(identifier), token, provider, _update, concurrency)
    else:
        raise ValueError(f"Unknown item type: {item_type}")
