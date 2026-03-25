"""
Request statistics tracker for GitHub API and LLM calls.

Tracks per-repo per-category request counts (success/fail).
Retried requests count as ONE request — final outcome determines success/fail.
"""

from __future__ import annotations

import re
import threading
from collections import defaultdict
from dataclasses import dataclass, field

from log import get_logger

_log = get_logger("engine.stats")

# Categories derived from GitHub API paths
_PATH_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"/repos/[^/]+/[^/]+/pulls/\d+/reviews"), "reviews"),
    (re.compile(r"/repos/[^/]+/[^/]+/pulls/\d+/comments"), "comments"),
    (re.compile(r"/repos/[^/]+/[^/]+/pulls/\d+/commits"), "commits"),
    (re.compile(r"/repos/[^/]+/[^/]+/pulls/\d+$"), "pulls"),
    (re.compile(r"/repos/[^/]+/[^/]+/pulls$"), "pulls"),
    (re.compile(r"/repos/[^/]+/[^/]+/issues/\d+/comments"), "comments"),
    (re.compile(r"/repos/[^/]+/[^/]+/issues/\d+$"), "issues"),
    (re.compile(r"/repos/[^/]+/[^/]+/issues$"), "issues"),
    (re.compile(r"/repos/[^/]+/[^/]+/commits/[a-f0-9]+$"), "commits"),
    (re.compile(r"/repos/[^/]+/[^/]+/commits$"), "commits"),
    (re.compile(r"/repos/[^/]+/[^/]+/contents/"), "templates"),
    (re.compile(r"/search/repositories"), "trending"),
]

_REPO_RE = re.compile(r"/repos/([^/]+/[^/]+)")


def _classify_path(path: str) -> tuple[str, str]:
    """Return (repo_name, category) from a GitHub API path."""
    category = "other"
    for pat, cat in _PATH_PATTERNS:
        if pat.search(path):
            category = cat
            break
    m = _REPO_RE.search(path)
    repo = m.group(1) if m else "_global"
    return repo, category


@dataclass
class _Counter:
    success: int = 0
    failed: int = 0

    @property
    def total(self) -> int:
        return self.success + self.failed


class RequestStats:
    """Thread-safe request statistics collector."""

    def __init__(self):
        self._lock = threading.Lock()
        # {repo: {category: _Counter}}
        self._gh: dict[str, dict[str, _Counter]] = defaultdict(lambda: defaultdict(_Counter))
        self._llm: dict[str, _Counter] = defaultdict(_Counter)

    def record_gh(self, path: str, success: bool) -> None:
        repo, category = _classify_path(path)
        with self._lock:
            c = self._gh[repo][category]
            if success:
                c.success += 1
            else:
                c.failed += 1

    def record_llm(self, repo: str, success: bool) -> None:
        with self._lock:
            c = self._llm[repo]
            if success:
                c.success += 1
            else:
                c.failed += 1

    def print_summary(self) -> None:
        """Print a summary table to the logger and stdout."""
        gh_cats = ["commits", "pulls", "issues", "reviews", "comments", "templates"]
        all_repos = sorted(set(list(self._gh.keys()) + list(self._llm.keys())) - {"_global"})

        if not all_repos:
            return

        # Build rows
        rows: list[tuple[str, list[str]]] = []
        totals: dict[str, _Counter] = defaultdict(_Counter)

        for repo in all_repos:
            cells: list[str] = []
            for cat in gh_cats:
                c = self._gh.get(repo, {}).get(cat, _Counter())
                cells.append(_fmt_cell(c))
                totals[cat].success += c.success
                totals[cat].failed += c.failed
            # LLM column
            lc = self._llm.get(repo, _Counter())
            cells.append(_fmt_cell(lc))
            totals["llm"].success += lc.success
            totals["llm"].failed += lc.failed
            rows.append((repo, cells))

        # Global entries (trending, etc.)
        if "_global" in self._gh:
            cells = []
            for cat in gh_cats:
                c = self._gh["_global"].get(cat, _Counter())
                cells.append(_fmt_cell(c))
                totals[cat].success += c.success
                totals[cat].failed += c.failed
            cells.append(_fmt_cell(_Counter()))
            rows.append(("_global", cells))

        # Build total row
        total_cells = [_fmt_cell(totals[cat]) for cat in gh_cats] + [_fmt_cell(totals["llm"])]

        # Calculate column widths
        headers = ["Repository"] + [c.capitalize() for c in gh_cats] + ["LLM"]
        col_widths = [len(h) for h in headers]
        for repo, cells in rows:
            col_widths[0] = max(col_widths[0], len(repo))
            for i, cell in enumerate(cells):
                col_widths[i + 1] = max(col_widths[i + 1], len(cell))
        col_widths[0] = max(col_widths[0], len("TOTAL"))
        for i, cell in enumerate(total_cells):
            col_widths[i + 1] = max(col_widths[i + 1], len(cell))

        # Print
        sep = "+-" + "-+-".join("-" * w for w in col_widths) + "-+"
        header_line = "| " + " | ".join(h.center(col_widths[i]) for i, h in enumerate(headers)) + " |"

        lines = [sep, header_line, sep]
        for repo, cells in rows:
            line = "| " + repo.ljust(col_widths[0]) + " | " + " | ".join(
                cells[i].center(col_widths[i + 1]) for i in range(len(cells))
            ) + " |"
            lines.append(line)
        lines.append(sep)
        total_line = "| " + "TOTAL".ljust(col_widths[0]) + " | " + " | ".join(
            total_cells[i].center(col_widths[i + 1]) for i in range(len(total_cells))
        ) + " |"
        lines.append(total_line)
        lines.append(sep)

        table = "\n".join(lines)
        print(f"\n📊 Request Summary\n{table}")
        _log.info("Request summary:\n%s", table)


def _fmt_cell(c: _Counter) -> str:
    if c.total == 0:
        return "-"
    if c.failed == 0:
        return f"{c.success}/{c.total}"
    return f"{c.success}/{c.total} ({c.success * 100 // c.total}%)"


# ── Module-level singleton ────────────────────────────────────
_stats: RequestStats | None = None


def get_stats() -> RequestStats:
    global _stats
    if _stats is None:
        _stats = RequestStats()
    return _stats


def reset_stats() -> None:
    global _stats
    _stats = RequestStats()
