"""
GitHub REST API helpers for data fetching.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import httpx

from log import get_logger
from engine.stats import get_stats

_log = get_logger("engine.github_api")

# ── Concurrency & retry settings ─────────────────────────────
_MAX_RETRIES = 5
_RETRY_DELAY = 2.0  # seconds (exponential backoff base)
_REQUEST_GAP = 0.3  # seconds between sequential batch requests (avoid secondary rate limit)

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
            get_stats().record_gh(path, success=True)
            return data
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            if attempt >= _MAX_RETRIES - 1:
                get_stats().record_gh(path, success=False)
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
                get_stats().record_gh(path, success=False)
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
            get_stats().record_gh(path, success=True)
            return data
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            if attempt >= _MAX_RETRIES - 1:
                get_stats().record_gh(path, success=False)
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
                get_stats().record_gh(path, success=False)
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


def fetch_pulls(
    owner: str, repo: str, token: str,
    max_items: int = 50,
) -> list[dict]:
    """Fetch the most recent pull requests.

    Returns up to *max_items* PRs sorted by ``updated_at`` descending.
    """
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

    _log.info("fetch_pulls: %d PRs", len(prs))
    return prs


# ── Trending repos ────────────────────────────────────────────

def fetch_trending_repos(token: str, count: int = 10, topic: str | None = None,
                         min_stars: int = 500, active_days: int = 14) -> list[str]:
    """Fetch trending repos via GitHub Search API.

    Uses ``/search/repositories`` with ``pushed:>=<date>`` sorted by stars.
    If *topic* is given, adds ``topic:<topic>`` to the query.
    *min_stars* filters out repos below the star threshold (default 500).
    *active_days* limits to repos pushed within the last N days.
    Returns a list of ``owner/repo`` strings (up to *count*).
    """
    today = datetime.now(timezone.utc)
    since = (today - timedelta(days=active_days)).strftime("%Y-%m-%d")
    q = f"pushed:>={since} stars:>={min_stars}"
    if topic:
        q += f" topic:{topic}"
    params: dict = {
        "q": q,
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
            _EXCLUDE_TOPICS = {"education", "tutorial", "tutorials", "awesome", "awesome-list"}
            repos = [
                item["full_name"]
                for item in data.get("items", [])
                if not item.get("name", "").lower().startswith("awesome")
                and not _EXCLUDE_TOPICS.intersection(t.lower() for t in item.get("topics", []))
            ]
            _log.info("fetch_trending_repos: %d repos (query: pushed>=%s)", len(repos), since)
            get_stats().record_gh("/search/repositories", success=True)
            return repos
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            if attempt >= _MAX_RETRIES - 1:
                _log.warning("fetch_trending_repos failed: %s", exc)
                get_stats().record_gh("/search/repositories", success=False)
                return []
            delay = _RETRY_DELAY * (2 ** attempt)
            time.sleep(delay)
        except httpx.HTTPStatusError as exc:
            if _is_retryable(exc) and attempt < _MAX_RETRIES - 1:
                delay = _RETRY_DELAY * (2 ** attempt)
                time.sleep(delay)
            else:
                _log.warning("fetch_trending_repos failed: HTTP %d", exc.response.status_code)
                get_stats().record_gh("/search/repositories", success=False)
                return []
    return []


# ── Repo metadata ─────────────────────────────────────────────

def fetch_repo_pushed_at(owner: str, repo: str, token: str) -> str:
    """Return the ISO-8601 `pushed_at` timestamp for a repo (empty string on error)."""
    try:
        data = _gh_get_one(f"/repos/{owner}/{repo}", token)
        return data.get("pushed_at", "")
    except Exception as exc:
        _log.warning("Failed to fetch repo info for %s/%s: %s", owner, repo, exc)
        return ""


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





# ── Batch concurrent fetchers ─────────────────────────────────

def fetch_pr_reviews_batch(
    owner: str, repo: str, token: str, pr_numbers: list[int],
) -> dict[int, list[dict]]:
    """Fetch reviews for multiple PRs sequentially with inter-request delay."""
    results: dict[int, list[dict]] = {}
    if not pr_numbers:
        return results
    _log.info("Fetching reviews for %d PRs sequentially (gap=%.1fs)", len(pr_numbers), _REQUEST_GAP)
    for i, num in enumerate(pr_numbers):
        try:
            results[num] = _gh_get(f"/repos/{owner}/{repo}/pulls/{num}/reviews", token, {"per_page": 100})
        except Exception as exc:
            _log.warning("Failed to fetch reviews for PR #%d: %s", num, exc)
        if i < len(pr_numbers) - 1:
            time.sleep(_REQUEST_GAP)
    _log.info("Fetched reviews for %d/%d PRs (total %d reviews)",
              len(results), len(pr_numbers), sum(len(v) for v in results.values()))
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

    Uses GitHub Contents API. Missing templates (404) are expected and silently skipped
    without counting as failures in request stats.
    """
    import base64

    templates: dict[str, str] = {}
    for kind, paths in _TEMPLATE_PATHS.items():
        if kind in templates:
            continue
        for path in paths:
            try:
                url = f"{_GH_API}/repos/{owner}/{repo}/contents/{path}"
                resp = httpx.get(url, headers=_gh_headers(token), timeout=15)
                if resp.status_code == 404:
                    continue  # expected — most repos don't have templates
                resp.raise_for_status()
                data = resp.json()
                content = data.get("content", "")
                if content and data.get("encoding") == "base64":
                    templates[kind] = base64.b64decode(content).decode("utf-8", errors="replace")
                    _log.info("Fetched %s template: %s (%d chars)", kind, path, len(templates[kind]))
                    get_stats().record_gh("/repos/contents/", success=True)
                    break
            except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.NetworkError):
                continue
    return templates
