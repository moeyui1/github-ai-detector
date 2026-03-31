# 🛸 GitHub AI Radar

**How much of the code on GitHub is actually written by AI?**

GitHub AI Radar scans any GitHub repository and tells you exactly how much of its development is AI-generated. It analyzes Commits and Pull Requests through a three-layer detection pipeline — bot identity filtering, known AI bot matching, and LLM-powered text style auditing — producing an **AI Involvement Index (AII)** score from 0% to 100%.

English | [中文](README_ZH.md)

[![Daily AI Radar Report](https://github.com/moeyui1/github-ai-detector/actions/workflows/daily-report.yml/badge.svg)](https://github.com/moeyui1/github-ai-detector/actions/workflows/daily-report.yml)
![License](https://img.shields.io/badge/license-MIT-blue)
![Python](https://img.shields.io/badge/python-3.10%2B-brightgreen)

## Why?

AI coding assistants like Copilot, Cursor, and Codex are reshaping open-source development at an unprecedented pace. But how deep does the AI involvement really go?

GitHub AI Radar gives you the answer — with data, not guesswork.

- **Track AI adoption** across popular open-source projects
- **Daily automated reports** published to GitHub Pages
- **Share rankings** as beautiful images with QR codes
- **Zero-config deployment** — one GitHub Action, fully automated

---

## How It Works

```
GitHub Events ──→ L1: System Bot Filter ──→ L2: AI Bot Match ──→ L3: LLM Audit ──→ AII Score
                  (dependabot, etc.)        (copilot[bot], etc.)   (text analysis)
```

1. **L1** — Filters out system bots (CI/CD, dependabot) by username
2. **L2** — Identifies known AI coding assistants (Copilot, Codex) by username
3. **L3** — Explicit pattern detection (PR AI collaboration mentions, commit Git trailers like `Assisted-by`) + LLM text style audit
4. **AII** — Dynamically weighted: only dimensions with actual data receive weight (e.g. a repo with no PRs assigns 100% weight to commits)

## Features

- 🔍 **Three-layer detection** — Static rules + explicit AI pattern matching + LLM text audit
- 📊 **Beautiful report site** — Podium-style rankings, trend charts, sparklines, GitHub avatars, and shareable images
- ⚡ **Batch LLM scoring** — 10 events per API call, with retry and concurrency for large repos
- 🔌 **Multiple LLM backends** — OpenAI, GitHub Models, or any OpenAI-compatible endpoint
- 📦 **Event-level cache** — Skips LLM calls for unchanged events across runs
- 🛠️ **Flexible CLI** — Single-item analysis, batch reports, and `--force` full re-scoring
- 📱 **Mobile responsive** — Adaptive layout for desktop and mobile devices
- 📸 **Share as image** — One-click ranking snapshot with QR code, perfect for social media

## Quick Start

### 1. Install

```bash
pip install -r requirements.txt
npm install
```

### 2. Configure

Copy and edit the environment file:

```bash
cp .env.example .env
```

| Variable | Required | Description |
|----------|----------|-------------|
| `GITHUB_TOKEN` | Recommended | GitHub PAT — without it, API is limited to 60 req/h |
| `LLM_PROVIDER` | Optional | LLM backend: `none`, `openai`, or `github` (default: config.toml) |
| `LLM_MODEL` | Optional | Model name, e.g. `gpt-4.1` (default: config.toml) |
| `OPENAI_API_KEY` | Optional | Required when using the OpenAI provider |
| `OPENAI_BASE_URL` | Optional | OpenAI-compatible endpoint (Azure, GitHub Models, etc.) |

You can also configure repos, LLM settings, and bot lists in `config.toml`.

### 3. Run

**Analyze a single PR / Commit:**

```bash
python analyze.py https://github.com/owner/repo/pull/42
python analyze.py owner/repo#123
python analyze.py --no-llm <URL>    # skip LLM, use static rules only
```

**Generate a batch report:**

```bash
# Analyze repos from config.toml → JSON (with event cache)
python -m report.cli --out reports

# Force re-score all events (ignore cache)
python -m report.cli --force

# Build the compiled CSS bundle
npm run build:css

# Render JSON → static HTML site
python -m report.html --input reports --out site

# Preview locally (open http://localhost:8000)
python -m http.server 8000 -d site
```

**Generate mock data for local development:**

```bash
python scripts/mock_reports.py       # 7 days of mock reports → reports/
npm run build:css
python -m report.html --input reports --out site
python -m http.server 8000 -d site   # preview at http://localhost:8000
```

## Deploy with GitHub Actions

Automatically analyze your repos daily and publish to GitHub Pages — zero maintenance:

1. **Fork this repo**
2. Add secrets in **Settings → Secrets**: `GH_PAT` and optionally `OPENAI_API_KEY`
3. Set **Settings → Pages → Source** to **Deploy from a branch** → `gh-pages` / `/ (root)`
4. Edit `config.toml` to add the repositories you want to track
5. Trigger manually or wait for the daily schedule

Reports will be available at `https://<user>.github.io/<repo>/`

## License

MIT
