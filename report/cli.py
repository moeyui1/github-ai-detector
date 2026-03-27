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
from engine.github_api import fetch_repo_pushed_at, fetch_trending_repos
from engine.stats import get_stats
from log import get_logger
from providers import get_provider

_log = get_logger("report_cli")


def _print_detection_summary(results: list[dict], threshold: float) -> None:
    """Print a per-repo table showing L1/L2/L3 detection rates by event kind."""
    if not results:
        return

    # Collect stats: repo → kind → {total, l1, l2, l3}
    rows: list[tuple[str, dict[str, dict[str, int]]]] = []
    for r in results:
        repo = r["repo_name"]
        by_kind: dict[str, dict[str, int]] = {}
        for ev in r.get("events", []):
            kind = ev["kind"]
            if kind not in by_kind:
                by_kind[kind] = {"total": 0, "l1": 0, "l2": 0, "l3": 0}
            by_kind[kind]["total"] += 1
            ak = ev.get("actor_kind", "human")
            if ak == "system_bot":
                by_kind[kind]["l1"] += 1
            elif ak == "ai_bot":
                by_kind[kind]["l2"] += 1
            elif ev.get("ai_score", 0) >= threshold:
                by_kind[kind]["l3"] += 1
        rows.append((repo, by_kind))

    # Determine all event kinds present
    all_kinds = sorted({k for _, bk in rows for k in bk})
    if not all_kinds:
        return

    # Build table
    # Columns: Repo | kind1 (L1/L2/L3/Total) | kind2 ... | ALL
    def _fmt(stats: dict[str, int]) -> str:
        t = stats["total"]
        if t == 0:
            return "-"
        detected = stats["l1"] + stats["l2"] + stats["l3"]
        pct = detected / t * 100 if t else 0
        return f"{stats['l1']}/{stats['l2']}/{stats['l3']} ({pct:.0f}%)"

    # Header
    col_headers = [k.upper() for k in all_kinds] + ["ALL"]
    repo_width = max(len(r[0]) for r in rows)
    repo_width = max(repo_width, 10)
    col_width = max(18, *(len(h) for h in col_headers))

    header = "| " + "REPO".ljust(repo_width) + " | " + " | ".join(h.center(col_width) for h in col_headers) + " |"
    sep = "+" + "-" * (repo_width + 2) + "+" + (("-" * (col_width + 2) + "+") * len(col_headers))

    lines = ["\n🔍 Detection Summary (L1: System Bot / L2: AI Bot / L3: LLM)", sep, header, sep]

    for repo, by_kind in rows:
        cells = []
        all_stats = {"total": 0, "l1": 0, "l2": 0, "l3": 0}
        for kind in all_kinds:
            stats = by_kind.get(kind, {"total": 0, "l1": 0, "l2": 0, "l3": 0})
            cells.append(_fmt(stats).center(col_width))
            for k in all_stats:
                all_stats[k] += stats[k]
        cells.append(_fmt(all_stats).center(col_width))
        line = "| " + repo.ljust(repo_width) + " | " + " | ".join(cells) + " |"
        lines.append(line)

    lines.append(sep)
    print("\n".join(lines))


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
        "bot_rate": round(result.bot_rate, 4),
        "aii": round(result.aii, 4),
        "commit_total": result.commit_total,
        "commit_ai": result.commit_ai,
        "pr_total": result.pr_total,
        "pr_ai": result.pr_ai,
        "review_total": result.review_total,
        "review_ai": result.review_ai,
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

    active_days = cfg.analysis.inactive_days or 14

    # Append trending repos (deduplicated)
    if cfg.github.trending_count > 0:
        print(f"Fetching {cfg.github.trending_count} trending repos (active within {active_days}d) …")
        trending = fetch_trending_repos(cfg.github.token, cfg.github.trending_count, active_days=active_days)
        existing = {u.rstrip("/").split("github.com/")[-1].lower() for u in repo_urls}
        for r in trending:
            if r.lower() not in existing:
                repo_urls.append(r)
                existing.add(r.lower())
                print(f"  + {r} (trending)")
            else:
                print(f"  • {r} (already configured)")

    # Append trending AI repos (deduplicated, across all configured topics)
    if cfg.github.trending_ai_count > 0:
        topics = cfg.github.trending_ai_topics
        print(f"Fetching up to {cfg.github.trending_ai_count} trending AI repos (topics: {', '.join(topics)}) …")
        existing = {u.rstrip("/").split("github.com/")[-1].lower() for u in repo_urls}
        ai_added = 0
        for topic in topics:
            if ai_added >= cfg.github.trending_ai_count:
                break
            ai_trending = fetch_trending_repos(cfg.github.token, cfg.github.trending_ai_count, topic=topic, active_days=active_days)
            for r in ai_trending:
                if ai_added >= cfg.github.trending_ai_count:
                    break
                if r.lower() not in existing:
                    repo_urls.append(r)
                    existing.add(r.lower())
                    ai_added += 1
                    print(f"  + {r} (trending: {topic})")

    if repo_urls:
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

        # Skip inactive repos
        inactive_days = cfg.analysis.inactive_days
        if inactive_days > 0:
            pushed_at = fetch_repo_pushed_at(owner, repo, token)
            if pushed_at:
                from datetime import timedelta
                try:
                    pushed_dt = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
                    cutoff = datetime.now(timezone.utc) - timedelta(days=inactive_days)
                    if pushed_dt < cutoff:
                        print(f"Skip {repo_name} — inactive (last push: {pushed_at[:10]}, >{inactive_days}d ago)")
                        continue
                except (ValueError, TypeError):
                    pass

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

    # Print per-repo detection layer summary
    _print_detection_summary(serialised_results, cfg.analysis.high_risk_threshold)

    # Print request summary table
    get_stats().print_summary()


if __name__ == "__main__":
    main()
