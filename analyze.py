"""
CLI tool for analysing a single GitHub PR or commit.

Usage:
    python analyze.py https://github.com/owner/repo/commit/abc123
    python analyze.py https://github.com/owner/repo/pull/42
    python analyze.py owner/repo#123
"""

from __future__ import annotations

import argparse
import sys

from config import get_config
from engine import analyze_single, parse_item_url
from providers import get_provider


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyse a single GitHub PR / Commit for AI involvement.",
    )
    parser.add_argument("url", help="GitHub URL (commit/PR) or owner/repo#N shorthand")
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM analysis (L3)")
    args = parser.parse_args()

    cfg = get_config()
    gh_token = cfg.github.token
    if not gh_token:
        print("Error: GITHUB_TOKEN not configured.", file=sys.stderr)
        sys.exit(1)

    try:
        owner, repo, item_type, ident = parse_item_url(args.url)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    # Build LLM provider
    provider = None
    if not args.no_llm and cfg.llm.provider != "none":
        try:
            kwargs: dict = {"model": cfg.llm.model}
            if cfg.llm.provider == "openai":
                kwargs["api_key"] = cfg.llm.api_key
            elif cfg.llm.provider == "github":
                kwargs["token"] = gh_token
            else:
                kwargs["token"] = cfg.llm.api_key
            provider = get_provider(cfg.llm.provider, **kwargs)
        except Exception as exc:
            print(f"Warning: LLM init failed ({exc}), skipping L3.", file=sys.stderr)

    print(f"Analysing {item_type} {owner}/{repo} {ident} ...")

    def _progress(msg: str):
        print(f"  {msg}")

    result = analyze_single(
        owner, repo, item_type, ident, gh_token,
        provider=provider, progress_callback=_progress,
    )

    # Display results
    print()
    print(f"{'=' * 60}")
    print(f"  {result.item_title}")
    print(f"  {result.item_url}")
    print(f"{'=' * 60}")

    if result.participants:
        print(f"\n👥 Participants ({len(result.participants)}):")
        for p in result.participants:
            kind_icon = {"ai_bot": "🤖", "system_bot": "⚙️"}.get(p["kind"], "👤")
            print(f"  {kind_icon} {p['login']} ({p['kind']})")

    print(f"\n📋 Events ({len(result.events)}):")
    print(f"  {'Type':<8} {'Score':>6}  {'Actor':<20} {'Reason'}")
    print(f"  {'─' * 8} {'─' * 6}  {'─' * 20} {'─' * 40}")
    for e in result.events:
        score_marker = "🔴" if e.ai_score >= 0.6 else "🟡" if e.ai_score >= 0.3 else "🟢"
        reason_text = (e.reason or "—")[:60]
        print(f"  {e.kind.upper():<8} {score_marker}{e.ai_score:>5.2f}  {e.actor:<20} {reason_text}")
        if len(e.title) > 0:
            print(f"           └─ {e.title[:80]}")

    if result.llm_logs:
        total_tok = sum(l.total_tokens for l in result.llm_logs)
        ok = sum(1 for l in result.llm_logs if not l.error)
        fail = sum(1 for l in result.llm_logs if l.error)
        model = result.llm_logs[0].model or "?"
        print(f"\n🤖 LLM: {model} · {ok} ok{f', {fail} failed' if fail else ''} · {total_tok:,} tokens")

    scores = [e.ai_score for e in result.events]
    avg = sum(scores) / len(scores) if scores else 0
    print(f"\n📊 Avg AI Score: {avg:.2f} | Events: {len(result.events)} | Participants: {len(result.participants)}")


if __name__ == "__main__":
    main()
