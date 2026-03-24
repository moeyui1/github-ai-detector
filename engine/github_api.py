"""
GitHub REST API helpers for data fetching.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import httpx

from log import get_logger

_log = get_logger("engine.github_api")

# ── Concurrency & retry settings ─────────────────────────────
_GH_CONCURRENCY = 3   # max parallel GitHub API requests (keep low to avoid secondary rate limits)
_MAX_RETRIES = 5
_RETRY_DELAY = 2.0  # seconds (exponential backoff base)

_GH_API = "https://api.github.com"


def _gh_headers(token: str) -> dict[str, str]:
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _is_retryable(exc: httpx.HTTPStatusError) -> bool:
    code = exc.response.status_code
    if code in (429, 502, 503, 504):
        return True
    if code == 403:
        # Only retry 403 if it's a rate limit (not a permission denial)
        remaining = exc.response.headers.get("x-ratelimit-remaining")
        if remaining == "0":
            return True
        body = exc.response.text.lower()
        if "rate limit" in body or "abuse" in body:
            return True
    return False


def _rate_limit_delay(response: httpx.Response, attempt: int) -> float:
    """Calculate delay respecting Retry-After / x-ratelimit-reset headers."""
    # 429 / 403 with Retry-After header
    retry_after = response.headers.get("retry-after")
    if retry_after:
        try:
            return max(float(retry_after), 1.0)
        except ValueError:
            pass
    # GitHub x-ratelimit-reset (unix timestamp)
    reset_ts = response.headers.get("x-ratelimit-reset")
    if reset_ts:
        try:
            wait = int(reset_ts) - int(time.time()) + 1
            if 0 < wait <= 300:  # wait up to 5 minutes
                return float(wait)
        except ValueError:
            pass
    return _RETRY_DELAY * (2 ** attempt)


def _gh_get(path: str, token: str, params: dict | None = None) -> list[dict]:
    """GET from GitHub REST API; returns JSON list. Retries on transient errors."""
    url = f"{_GH_API}{path}"
    _log.debug("GitHub API GET %s params=%s", url, params)
    for attempt in range(_MAX_RETRIES):
        try:
            resp = httpx.get(url, headers=_gh_headers(token), params=params or {}, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            _log.info("GitHub API GET %s → %d items", path, len(data) if isinstance(data, list) else 1)
            return data
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            if attempt >= _MAX_RETRIES - 1:
                raise
            delay = _RETRY_DELAY * (2 ** attempt)
            _log.warning("Retry %s (%d/%d) in %.1fs: %s", path, attempt + 1, _MAX_RETRIES, delay, exc)
            time.sleep(delay)
        except httpx.HTTPStatusError as exc:
            if _is_retryable(exc) and attempt < _MAX_RETRIES - 1:
                delay = _rate_limit_delay(exc.response, attempt)
                _log.warning("Retry %s (%d/%d) in %.1fs: HTTP %d", path, attempt + 1, _MAX_RETRIES, delay, exc.response.status_code)
                time.sleep(delay)
            else:
                raise
    return []  # unreachable


def _gh_get_one(path: str, token: str, params: dict | None = None) -> dict:
    """GET a single JSON object from GitHub REST API. Retries on transient errors."""
    url = f"{_GH_API}{path}"
    _log.debug("GitHub API GET (one) %s", url)
    for attempt in range(_MAX_RETRIES):
        try:
            resp = httpx.get(url, headers=_gh_headers(token), params=params or {}, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            _log.info("GitHub API GET %s → ok", path)
            return data
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            if attempt >= _MAX_RETRIES - 1:
                raise
            delay = _RETRY_DELAY * (2 ** attempt)
            _log.warning("Retry %s (%d/%d) in %.1fs: %s", path, attempt + 1, _MAX_RETRIES, delay, exc)
            time.sleep(delay)
        except httpx.HTTPStatusError as exc:
            if _is_retryable(exc) and attempt < _MAX_RETRIES - 1:
                delay = _rate_limit_delay(exc.response, attempt)
                _log.warning("Retry %s (%d/%d) in %.1fs: HTTP %d", path, attempt + 1, _MAX_RETRIES, delay, exc.response.status_code)
                time.sleep(delay)
            else:
                raise
    return {}  # unreachable


# ── Batch fetch ───────────────────────────────────────────────

def fetch_commits(
    owner: str, repo: str, token: str,
    max_items: int = 50, max_pages: int = 10,
) -> list[dict]:
    """Fetch the most recent non-merge commits.

    Paginates through up to *max_pages* pages, skipping merge commits
    (``Merge ...``), until *max_items* useful commits are collected.
    """
    import re
    merge_re = re.compile(r"^Merge ", re.IGNORECASE)
    collected: list[dict] = []
    per_page = min(max_items, 100)
    for page in range(1, max_pages + 1):
        params: dict = {"per_page": per_page, "page": page}
        batch = _gh_get(f"/repos/{owner}/{repo}/commits", token, params)
        if not batch:
            break
        for c in batch:
            msg = (c.get("commit") or {}).get("message", "")
            if merge_re.match(msg):
                continue
            collected.append(c)
            if len(collected) >= max_items:
                break
        if len(collected) >= max_items:
            break
    _log.info("fetch_commits: collected %d non-merge commits in %d page(s)", len(collected), page)
    return collected


def fetch_pulls_and_issues(
    owner: str, repo: str, token: str,
    max_items: int = 50,
) -> tuple[list[dict], list[dict]]:
    """Fetch the most recent PRs and issues separately.

    PRs are fetched from ``/pulls`` endpoint; issues from ``/issues``
    endpoint (filtering out pull requests).  Each category independently
    respects *max_items*, so the caller always gets up to *max_items* PRs
    **and** up to *max_items* issues.

    Returns ``(full_pr_objects, issues)``.
    """
    # ── PRs via /pulls endpoint ──────────────────────────────
    pr_params: dict = {
        "per_page": min(max_items, 100),
        "state": "all",
        "sort": "updated",
        "direction": "desc",
    }
    try:
        prs = _gh_get(f"/repos/{owner}/{repo}/pulls", token, pr_params)
        prs = prs[:max_items]
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            _log.info("Pulls endpoint returned 404 (disabled?), skipping PRs for %s/%s", owner, repo)
            prs = []
        else:
            raise

    # ── Issues via /issues endpoint (skip PRs) ───────────────
    issue_params: dict = {
        "per_page": min(max_items, 100),
        "state": "all",
        "sort": "updated",
        "direction": "desc",
    }
    all_items = _gh_get(f"/repos/{owner}/{repo}/issues", token, issue_params)
    issues = [item for item in all_items if "pull_request" not in item][:max_items]

    _log.info("fetch_pulls_and_issues: %d PRs, %d issues", len(prs), len(issues))
    return prs, issues


# ── Trending repos ────────────────────────────────────────────

def fetch_trending_repos(token: str, count: int = 10) -> list[str]:
    """Fetch today's trending repos via GitHub Search API.

    Uses ``/search/repositories`` with ``pushed:>=TODAY`` sorted by stars.
    Returns a list of ``owner/repo`` strings (up to *count*).
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    params: dict = {
        "q": f"pushed:>={today}",
        "sort": "stars",
        "order": "desc",
        "per_page": count,
    }
    url = f"{_GH_API}/search/repositories"
    _log.debug("GitHub Search API %s params=%s", url, params)
    for attempt in range(_MAX_RETRIES):
        try:
            resp = httpx.get(url, headers=_gh_headers(token), params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            repos = [
                item["full_name"]
                for item in data.get("items", [])
                if not item.get("name", "").lower().startswith("awesome")
            ]
            _log.info("fetch_trending_repos: %d repos (query: pushed>=%s)", len(repos), today)
            return repos
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            if attempt >= _MAX_RETRIES - 1:
                _log.warning("fetch_trending_repos failed: %s", exc)
                return []
            delay = _RETRY_DELAY * (2 ** attempt)
            time.sleep(delay)
        except httpx.HTTPStatusError as exc:
            if _is_retryable(exc) and attempt < _MAX_RETRIES - 1:
                delay = _RETRY_DELAY * (2 ** attempt)
                time.sleep(delay)
            else:
                _log.warning("fetch_trending_repos failed: HTTP %d", exc.response.status_code)
                return []
    return []


# ── Single-item fetch helpers ─────────────────────────────────

def fetch_single_commit(owner: str, repo: str, sha: str, token: str) -> dict:
    """Fetch a single commit by SHA."""
    return _gh_get_one(f"/repos/{owner}/{repo}/commits/{sha}", token)


def fetch_single_pr(owner: str, repo: str, number: int, token: str) -> dict:
    """Fetch a single pull request."""
    return _gh_get_one(f"/repos/{owner}/{repo}/pulls/{number}", token)


def fetch_pr_comments(owner: str, repo: str, number: int, token: str) -> list[dict]:
    """Fetch review comments + issue comments on a PR."""
    review_comments = _gh_get(f"/repos/{owner}/{repo}/pulls/{number}/comments", token, {"per_page": 100})
    issue_comments = _gh_get(f"/repos/{owner}/{repo}/issues/{number}/comments", token, {"per_page": 100})
    return review_comments + issue_comments


def fetch_pr_reviews(owner: str, repo: str, number: int, token: str) -> list[dict]:
    """Fetch reviews (approved/changes-requested/commented) on a PR."""
    return _gh_get(f"/repos/{owner}/{repo}/pulls/{number}/reviews", token, {"per_page": 100})


def fetch_pr_commits(owner: str, repo: str, number: int, token: str) -> list[dict]:
    """Fetch commits belonging to a PR."""
    return _gh_get(f"/repos/{owner}/{repo}/pulls/{number}/commits", token, {"per_page": 100})


def fetch_single_issue(owner: str, repo: str, number: int, token: str) -> dict:
    """Fetch a single issue."""
    return _gh_get_one(f"/repos/{owner}/{repo}/issues/{number}", token)


def fetch_issue_comments(owner: str, repo: str, number: int, token: str) -> list[dict]:
    """Fetch comments on an issue."""
    return _gh_get(f"/repos/{owner}/{repo}/issues/{number}/comments", token, {"per_page": 100})


# ── Batch concurrent fetchers ─────────────────────────────────

def fetch_pr_reviews_batch(
    owner: str, repo: str, token: str, pr_numbers: list[int],
) -> dict[int, list[dict]]:
    """Fetch reviews for multiple PRs concurrently."""
    results: dict[int, list[dict]] = {}
    if not pr_numbers:
        return results
    _log.info("Fetching reviews for %d PRs concurrently (workers=%d)", len(pr_numbers), _GH_CONCURRENCY)
    with ThreadPoolExecutor(max_workers=_GH_CONCURRENCY) as pool:
        futures = {
            pool.submit(_gh_get, f"/repos/{owner}/{repo}/pulls/{num}/reviews", token, {"per_page": 100}): num
            for num in pr_numbers
        }
        for future in as_completed(futures):
            num = futures[future]
            try:
                results[num] = future.result()
            except Exception as exc:
                _log.warning("Failed to fetch reviews for PR #%d: %s", num, exc)
    _log.info("Fetched reviews for %d/%d PRs (total %d reviews)",
              len(results), len(pr_numbers), sum(len(v) for v in results.values()))
    return results


def fetch_issue_comments_batch(
    owner: str, repo: str, token: str, issue_numbers: list[int],
) -> dict[int, list[dict]]:
    """Fetch comments for multiple issues concurrently."""
    results: dict[int, list[dict]] = {}
    if not issue_numbers:
        return results
    _log.info("Fetching comments for %d issues concurrently (workers=%d)", len(issue_numbers), _GH_CONCURRENCY)
    with ThreadPoolExecutor(max_workers=_GH_CONCURRENCY) as pool:
        futures = {
            pool.submit(_gh_get, f"/repos/{owner}/{repo}/issues/{num}/comments", token, {"per_page": 100}): num
            for num in issue_numbers
        }
        for future in as_completed(futures):
            num = futures[future]
            try:
                results[num] = future.result()
            except Exception as exc:
                _log.warning("Failed to fetch comments for issue #%d: %s", num, exc)
    _log.info("Fetched comments for %d/%d issues (total %d comments)",
              len(results), len(issue_numbers), sum(len(v) for v in results.values()))
    return results


# ── Repo template helpers ─────────────────────────────────────

_TEMPLATE_PATHS = {
    "pr": [
        ".github/PULL_REQUEST_TEMPLATE.md",
        ".github/pull_request_template.md",
        "PULL_REQUEST_TEMPLATE.md",
        "docs/pull_request_template.md",
    ],
    "issue": [
        ".github/ISSUE_TEMPLATE.md",
        ".github/issue_template.md",
        "ISSUE_TEMPLATE.md",
        "docs/issue_template.md",
    ],
}


def fetch_repo_templates(owner: str, repo: str, token: str) -> dict[str, str]:
    """Fetch PR/Issue template files from the repo. Returns {'pr': '...', 'issue': '...'}.

    Uses GitHub Contents API. Missing templates are silently skipped.
    """
    import base64

    templates: dict[str, str] = {}
    for kind, paths in _TEMPLATE_PATHS.items():
        if kind in templates:
            continue
        for path in paths:
            try:
                data = _gh_get_one(f"/repos/{owner}/{repo}/contents/{path}", token)
                content = data.get("content", "")
                if content and data.get("encoding") == "base64":
                    templates[kind] = base64.b64decode(content).decode("utf-8", errors="replace")
                    _log.info("Fetched %s template: %s (%d chars)", kind, path, len(templates[kind]))
                    break
            except httpx.HTTPStatusError:
                continue
    return templates
