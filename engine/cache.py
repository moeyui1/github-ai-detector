"""
Event cache — tracks previously analysed events to avoid redundant LLM calls.

Stores a single JSON file with per-repo event records.  Each event is keyed
by a stable identifier (commit SHA or issue/PR number) and remembers its
``updated_at`` timestamp plus the LLM result.  On re-analysis the cache is
consulted: if the event hasn't been updated since last run the cached score
is reused; otherwise the event is re-scored.

Cache structure (``reports/cache.json``):
{
    "owner/repo": {
        "commit:abc123": {"updated_at": "...", "ai_score": 0.05, "reason": "..."},
        "pr:42":         {"updated_at": "...", "ai_score": 0.10, "reason": "..."},
        "issue:99":      {"updated_at": "...", "ai_score": 0.00, "reason": "..."}
    }
}
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from log import get_logger

_log = get_logger("engine.cache")

CacheRepo = dict[str, dict]   # event_key → {updated_at, ai_score, reason}
CacheData = dict[str, CacheRepo]  # repo_name → CacheRepo

_DEFAULT_PATH = Path("reports/cache.json")


def load_cache(path: Path = _DEFAULT_PATH) -> CacheData:
    """Load the cache file.  Returns empty dict if missing or corrupt."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        _log.info("Cache loaded | repos=%d | path=%s", len(data), path)
        return data
    except Exception as exc:
        _log.warning("Cache load failed (%s), starting fresh", exc)
        return {}


def save_cache(data: CacheData, path: Path = _DEFAULT_PATH) -> None:
    """Overwrite the cache file atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(len(v) for v in data.values())
    _log.info("Cache saved | repos=%d | events=%d | path=%s", len(data), total, path)


def event_key(kind: str, raw: dict) -> str:
    """Build a stable cache key for an event.

    - commit  → ``commit:<sha>``
    - pr/issue → ``pr:<number>`` / ``issue:<number>``
    """
    if kind == "commit":
        sha = raw.get("sha", "")
        return f"commit:{sha}"
    number = raw.get("number", "")
    return f"{kind}:{number}"


def event_updated_at(kind: str, raw: dict) -> str:
    """Extract the ``updated_at`` (or equivalent) timestamp from a raw event."""
    if kind == "commit":
        return raw.get("commit", {}).get("author", {}).get("date", "")
    return raw.get("updated_at", "")


def cache_is_fresh(cached_updated: str, current_updated: str, max_age_hours: int = 24) -> bool:
    """Return True if cached entry is still fresh enough to reuse.

    Fresh means the event hasn't been updated more than *max_age_hours*
    after the cached snapshot, so minor/trivial updates don't trigger
    unnecessary LLM re-scoring.
    """
    if cached_updated == current_updated:
        return True
    if not cached_updated or not current_updated:
        return False
    try:
        cached_dt = datetime.fromisoformat(cached_updated.replace("Z", "+00:00"))
        current_dt = datetime.fromisoformat(current_updated.replace("Z", "+00:00"))
        return (current_dt - cached_dt) < timedelta(hours=max_age_hours)
    except (ValueError, TypeError):
        return False
