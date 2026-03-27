"""Generate mock report JSON files for local preview."""
import json, random, os
from datetime import datetime, timedelta, timezone

REPOS = [
    "torvalds/linux",
    "facebook/react",
    "microsoft/vscode",
    "ollama/ollama",
    "langchain-ai/deepagents",
    "open-webui/open-webui",
    "huggingface/transformers",
    "mem0ai/mem0",
    "HKUDS/nanobot",
    "openclaw/openclaw",
    "freeCodeCamp/freeCodeCamp",
    "donnemartin/system-design-primer",
    "kamranahmedse/developer-roadmap",
    "EbookFoundation/free-programming-books",
]

KINDS = ["commit", "pr"]
ACTORS_HUMAN = ["alice", "bob", "charlie", "diana", "eve", "frank"]
ACTORS_AI = ["copilot[bot]", "codex[bot]", "sweep-ai[bot]"]
ACTORS_BOT = ["dependabot[bot]", "github-actions[bot]", "renovate[bot]"]

COMMIT_TITLES = [
    "fix: resolve memory leak in cache layer",
    "feat: add new API endpoint for user profile",
    "chore: update dependencies",
    "refactor: simplify auth middleware",
    "docs: update README with new examples",
    "perf: optimize database queries",
    "style: fix linting errors",
    "test: add unit tests for payment module",
]

PR_TITLES = [
    "Add dark mode support",
    "Implement rate limiting middleware",
    "Fix race condition in worker pool",
    "Upgrade build pipeline to use Rust",
    "Add multi-language support",
    "Refactor state management",
]

def rand_events(repo_name, date_str, count=20):
    events = []
    for i in range(count):
        kind = random.choice(KINDS)
        r = random.random()
        if r < 0.1:
            actor = random.choice(ACTORS_BOT)
            actor_kind = "system_bot"
            ai_score = round(random.uniform(0, 0.15), 4)
        elif r < 0.25:
            actor = random.choice(ACTORS_AI)
            actor_kind = "ai_bot"
            ai_score = round(random.uniform(0.7, 1.0), 4)
        else:
            actor = random.choice(ACTORS_HUMAN)
            actor_kind = "human"
            ai_score = round(random.uniform(0, 0.8), 4)

        if kind == "commit":
            title = random.choice(COMMIT_TITLES)
        elif kind == "pr":
            title = random.choice(PR_TITLES)

        events.append({
            "kind": kind,
            "title": title,
            "actor": actor,
            "actor_kind": actor_kind,
            "ai_score": ai_score,
            "reason": "Mock data for local preview",
            "url": f"https://github.com/{repo_name}/commit/mock{i:03d}",
            "created_at": f"{date_str}T{random.randint(0,23):02d}:{random.randint(0,59):02d}:00Z",
        })
    return events


def make_repo(repo_name, date_str):
    events = rand_events(repo_name, date_str, random.randint(10, 30))

    commit_scores = [e["ai_score"] for e in events if e["kind"] == "commit"]
    pr_scores = [e["ai_score"] for e in events if e["kind"] == "pr"]
    bot_events = sum(1 for e in events if e["actor_kind"] == "system_bot")

    s_commit = round(sum(commit_scores) / len(commit_scores), 4) if commit_scores else 0
    s_pr = round(sum(pr_scores) / len(pr_scores), 4) if pr_scores else 0
    bot_rate = round(bot_events / len(events), 4) if events else 0

    review_total = random.randint(5, 40)
    review_ai = random.randint(0, review_total)
    s_review = round(review_ai / review_total, 4) if review_total else 0

    # Simple AII calc
    weights = {"commit": 0.5, "pr": 0.3, "review": 0.2}
    active = {}
    if commit_scores: active["commit"] = weights["commit"]
    if pr_scores: active["pr"] = weights["pr"]
    if review_total: active["review"] = weights["review"]
    tw = sum(active.values()) if active else 1
    raw = (active.get("commit", 0)/tw * s_commit +
           active.get("pr", 0)/tw * s_pr +
           active.get("review", 0)/tw * s_review)
    aii = round(raw * (1 - bot_rate), 4)

    commit_total = len(commit_scores)
    pr_total = len(pr_scores)
    commit_ai = sum(1 for s in commit_scores if s >= 0.6)
    pr_ai = sum(1 for s in pr_scores if s >= 0.6)

    return {
        "repo_name": repo_name,
        "s_commit": s_commit,
        "s_pr": s_pr,
        "s_review": s_review,
        "bot_rate": bot_rate,
        "aii": aii,
        "commit_total": commit_total,
        "commit_ai": commit_ai,
        "pr_total": pr_total,
        "pr_ai": pr_ai,
        "review_total": review_total,
        "review_ai": review_ai,
        "events": events,
        "llm_logs": [],
    }


def main():
    out_dir = os.path.join(os.path.dirname(__file__), "..", "reports")
    os.makedirs(out_dir, exist_ok=True)

    today = datetime.now(timezone.utc).date()
    # Generate 7 days of reports
    for day_offset in range(7):
        d = today - timedelta(days=day_offset)
        date_str = d.isoformat()
        random.seed(hash(date_str))  # reproducible per date

        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "date": date_str,
            "repos": [make_repo(r, date_str) for r in REPOS],
        }

        path = os.path.join(out_dir, f"report-{date_str}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"  Written: {path}")

    # latest.json = today
    latest = os.path.join(out_dir, "latest.json")
    today_report = os.path.join(out_dir, f"report-{today.isoformat()}.json")
    if os.path.exists(today_report):
        import shutil
        shutil.copy2(today_report, latest)
        print(f"  Copied latest: {latest}")

    print("Done! Mock reports generated.")


if __name__ == "__main__":
    main()
