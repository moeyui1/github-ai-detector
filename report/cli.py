"""
CLI entry point for batch analysis — intended for CI / GitHub Actions.

Usage:
    python -m report.cli                    # analyse all repos in config.toml
    python -m report.cli --repos owner/repo1 owner/repo2
    python -m report.cli --out reports      # custom output dir
    python -m report.cli --force            # ignore cache, re-score everything
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from config import get_config
from engine import AnalysisResult, analyze_repo, parse_repo_url
from engine.cache import CacheData, load_cache, save_cache
from engine.github_api import fetch_trending_repos
from log import get_logger
from providers import get_provider

_log = get_logger("report_cli")


def _build_provider():
    """Build an LLM provider from config (returns None when provider == 'none')."""
    cfg = get_config()
    name = cfg.llm.provider.lower()
    if name in ("none", ""):
        return None
    kwargs: dict = {"model": cfg.llm.model}
    if name == "openai":
        kwargs["api_key"] = cfg.llm.api_key
        kwargs["base_url"] = cfg.llm.base_url
    elif name == "github":
        kwargs["token"] = cfg.github.token
    return get_provider(name, **kwargs)


def _serialize_result(result: AnalysisResult) -> dict:
    """Convert AnalysisResult to a JSON-serialisable dict."""
    return {
        "repo_name": result.repo_name,
        "s_commit": round(result.s_commit, 4),
        "s_pr": round(result.s_pr, 4),
        "s_review": round(result.s_review, 4),
        "s_issue": round(result.s_issue, 4),
        "bot_rate": round(result.bot_rate, 4),
        "aii": round(result.aii, 4),
        "commit_total": result.commit_total,
        "commit_ai": result.commit_ai,
        "pr_total": result.pr_total,
        "pr_ai": result.pr_ai,
        "review_total": result.review_total,
        "review_ai": result.review_ai,
        "issue_comment_total": result.issue_comment_total,
        "issue_comment_ai": result.issue_comment_ai,
        "events": [
            {
                "kind": e.kind,
                "title": e.title,
                "actor": e.actor,
                "actor_kind": e.actor_kind.value,
                "ai_score": round(e.ai_score, 4),
                "reason": e.reason,
                "url": e.url,
                "created_at": e.created_at,
            }
            for e in result.events
        ],
        "llm_logs": [
            {
                "event_title": l.event_title,
                "event_kind": l.event_kind,
                "score": round(l.score, 4),
                "model": l.model,
                "prompt_tokens": l.prompt_tokens,
                "completion_tokens": l.completion_tokens,
                "total_tokens": l.total_tokens,
                "error": l.error,
            }
            for l in result.llm_logs
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="GitHub AI-Radar — batch CLI report")
    parser.add_argument(
        "--repos", nargs="*", default=None,
        help="GitHub repos (owner/repo or URL). Defaults to config.toml.",
    )
    parser.add_argument("--out", default="reports", help="Output directory (default: reports)")
    parser.add_argument("--force", action="store_true", help="Ignore cache, re-score all events")
    args = parser.parse_args()

    cfg = get_config()
    repo_urls = list(args.repos or cfg.github.repos)
    if not repo_urls and cfg.github.trending_count <= 0:
        print("No repos specified. Use --repos or set github.repos in config.toml.", file=sys.stderr)
        sys.exit(1)

    # Append trending repos (deduplicated)
    if cfg.github.trending_count > 0:
        print(f"Fetching {cfg.github.trending_count} trending repos …")
        trending = fetch_trending_repos(cfg.github.token, cfg.github.trending_count)
        existing = {u.rstrip("/").split("github.com/")[-1].lower() for u in repo_urls}
        for r in trending:
            if r.lower() not in existing:
                repo_urls.append(r)
                print(f"  + {r} (trending)")
            else:
                print(f"  • {r} (already configured)")
        print(f"Total repos to analyse: {len(repo_urls)}")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    provider = _build_provider()
    token = cfg.github.token

    # Load event cache
    cache_path = out_dir / "cache.json"
    cache_data: CacheData = {} if args.force else load_cache(cache_path)

    # Analyse each repo
    serialised_results: list[dict] = []
    for url in repo_urls:
        try:
            owner, repo = parse_repo_url(url)
        except ValueError as exc:
            print(f"Skip invalid repo URL: {url} ({exc})", file=sys.stderr)
            continue

        repo_name = f"{owner}/{repo}"
        repo_cache = cache_data.get(repo_name, {})

        print(f"Analysing {repo_name} … (cache: {len(repo_cache)} entries)")
        try:
            result, new_cache = analyze_repo(
                owner, repo, token,
                provider=provider,
                max_items=cfg.analysis.max_items,
                progress_callback=lambda msg: print(f"  {msg}"),
                cache=repo_cache,
            )
            cache_data[repo_name] = new_cache
            serialised_results.append(_serialize_result(result))
            print(f"  ✓ AII={result.aii:.2%}  Bot={result.bot_rate:.2%}  Events={len(result.events)}")
        except Exception as exc:
            print(f"  ✗ Failed: {exc}", file=sys.stderr)

    if not serialised_results:
        print("All analyses failed.", file=sys.stderr)
        sys.exit(1)

    # Save cache (overwrite)
    save_cache(cache_data, cache_path)

    # Write report
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date": date_str,
        "repos": serialised_results,
    }
    json_path = out_dir / f"report-{date_str}.json"
    json_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "latest.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\nReport saved → {json_path}")
    print(f"Cache saved  → {cache_path}")


if __name__ == "__main__":
    main()
