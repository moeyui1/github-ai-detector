"""
Static HTML report generator for GitHub AI-Radar.

Reads JSON reports from the reports/ directory and produces a static site
suitable for GitHub Pages deployment.

Usage:
    python -m report.html                    # reads reports/, writes site/
    python -m report.html --in reports --out site
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

# ── Paths ─────────────────────────────────────────────────────

_PKG_DIR = Path(__file__).resolve().parent
_TEMPLATE_DIR = _PKG_DIR / "templates"
_STATIC_DIR = _PKG_DIR / "static"

# ── Jinja2 environment ───────────────────────────────────────

_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=False,          # HTML is pre-escaped where needed
    trim_blocks=True,
    lstrip_blocks=True,
)

# Expose bot avatar mapping to all templates
from engine.models import BOT_AVATAR_MAP as _BOT_AVATAR_MAP
_env.globals["bot_avatars"] = _BOT_AVATAR_MAP


# ── Helpers ──────────────────────────────────────────────────

def _score_class(score: float) -> str:
    if score >= 0.6:
        return "high"
    if score >= 0.3:
        return "med"
    return "low"


def _pct(val: float) -> str:
    return f"{val:.1%}"


def _slug(repo_name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", repo_name).strip("-").lower()


def _esc(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _load_config() -> dict:
    """Load config.toml and return full dict."""
    try:
        cfg_path = _PKG_DIR.parent / "config.toml"
        if cfg_path.is_file():
            try:
                import tomllib
            except ModuleNotFoundError:
                import tomli as tomllib
            return tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _get_icon_map(config: dict | None = None) -> dict[str, str]:
    data = config or _load_config()
    raw = data.get("icons", {})
    return {k.lower(): v for k, v in raw.items()}


def _get_site_url(config: dict | None = None) -> str:
    data = config or _load_config()
    return data.get("site", {}).get("site_url", "http://localhost:8080")


def _repo_avatar_url(repo_name: str, icon_map: dict[str, str]) -> str:
    key = repo_name.lower()
    if key in icon_map:
        return icon_map[key]
    owner = repo_name.split("/")[0] if "/" in repo_name else repo_name
    return f"https://avatars.githubusercontent.com/{owner}?size=80"


# ── History loading ──────────────────────────────────────────

def _load_history(reports_dir: Path) -> dict[str, list[tuple[str, float]]]:
    history: dict[str, list[tuple[str, float]]] = {}
    for rfile in sorted(reports_dir.glob("report-*.json")):
        try:
            data = json.loads(rfile.read_text(encoding="utf-8"))
        except Exception:
            continue
        date_str = data.get("date", "")
        for r in data.get("repos", []):
            key = r["repo_name"].lower()
            history.setdefault(key, []).append((date_str, r["aii"]))
    for key in history:
        history[key].sort(key=lambda t: t[0])
    return history


# ── Data enrichment ──────────────────────────────────────────
# Attach computed fields (_slug, _avatar, _cls, _trend, _sparkline, _chart)
# to each repo dict so templates can use them directly.

def _compute_trend(series: list[tuple[str, float]]) -> dict | None:
    """Compute trend direction and diff from history series (last 7 points)."""
    if len(series) < 2:
        return None
    series = series[-7:]
    first_avg = sum(v for _, v in series[:max(1, len(series)//4)]) / max(1, len(series)//4)
    last_avg = sum(v for _, v in series[-max(1, len(series)//4):]) / max(1, len(series)//4)
    diff = last_avg - first_avg
    if abs(diff) < 0.005:
        return {"direction": "flat", "diff": 0}
    elif diff > 0:
        return {"direction": "up", "diff": abs(diff)}
    else:
        return {"direction": "down", "diff": abs(diff)}


def _compute_sparkline(series: list[tuple[str, float]], color_cls: str) -> dict:
    """Compute sparkline SVG data from history series."""
    empty = {"points": False}
    if len(series) < 2:
        return empty
    series = series[-7:]
    values = [v for _, v in series]
    n = len(values)
    w, h, pad = 120, 36, 3
    lo, hi = min(values), max(values)
    span = hi - lo if hi > lo else 0.01

    def px(i: int, v: float) -> tuple[float, float]:
        x = pad + (w - 2 * pad) * i / (n - 1)
        y = pad + (h - 2 * pad) * (1 - (v - lo) / span)
        return round(x, 1), round(y, 1)

    pts = [px(i, v) for i, v in enumerate(values)]
    polyline = " ".join(f"{x},{y}" for x, y in pts)
    area = polyline + f" {pts[-1][0]},{h - pad} {pts[0][0]},{h - pad}"

    color_map = {"high": "#dc2626", "med": "#ca8a04", "low": "#16a34a"}
    stroke = color_map.get(color_cls, "#2563eb")

    first_avg = sum(values[:max(1, n // 4)]) / max(1, n // 4)
    last_avg = sum(values[-max(1, n // 4):]) / max(1, n // 4)
    diff = last_avg - first_avg
    if abs(diff) < 0.005:
        arrow, arrow_cls = "→", "trend-flat"
    elif diff > 0:
        arrow, arrow_cls = f"↑{abs(diff):.1%}", "trend-up"
    else:
        arrow, arrow_cls = f"↓{abs(diff):.1%}", "trend-down"

    lx, ly = pts[-1]
    return {
        "points": True,
        "polyline": polyline,
        "area": area,
        "color": stroke,
        "last_x": lx,
        "last_y": ly,
        "arrow": arrow,
        "arrow_cls": arrow_cls,
    }


def _compute_chart(series: list[tuple[str, float]], cls: str, counter: int) -> dict | None:
    """Compute ECharts data for a repo trend chart."""
    if len(series) < 2:
        return None
    series = series[-7:]
    color_map = {"high": "#dc2626", "med": "#ca8a04", "low": "#16a34a"}
    return {
        "id": f"echart-{counter}",
        "dates_json": json.dumps([d for d, _ in series]),
        "values_json": json.dumps([round(v * 100, 2) for _, v in series]),
        "color": color_map.get(cls, "#2563eb"),
    }


def _enrich_repos(repos: list[dict], icon_map: dict[str, str],
                  history: dict[str, list[tuple[str, float]]]) -> list[dict]:
    """Add computed template fields to each repo dict."""
    chart_counter = 0
    for r in repos:
        r["_slug"] = _slug(r["repo_name"])
        r["_avatar"] = _repo_avatar_url(r["repo_name"], icon_map)
        cls = _score_class(r["aii"])
        r["_cls"] = cls
        repo_key = r["repo_name"].lower()
        series = history.get(repo_key, [])
        r["_trend"] = _compute_trend(series)
        r["_sparkline"] = _compute_sparkline(series, cls)
        chart_counter += 1
        r["_chart"] = _compute_chart(series, cls, chart_counter)
    return repos


# ── Copy static files ────────────────────────────────────────

def _copy_static(out_dir: Path) -> None:
    """Copy CSS, JS, favicon to output directory root."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for fname in ("style.css", "app.js", "favicon.svg"):
        src = _STATIC_DIR / fname
        if src.exists():
            shutil.copy2(src, out_dir / fname)


def _build_asset_versions() -> dict[str, str]:
    """Return short content hashes for static assets used in generated URLs."""
    versions: dict[str, str] = {}
    for fname in ("style.css", "app.js", "favicon.svg"):
        src = _STATIC_DIR / fname
        if not src.exists():
            continue
        versions[fname] = hashlib.sha256(src.read_bytes()).hexdigest()[:10]
    return versions


def _versioned_asset_path(path: str, asset_versions: dict[str, str] | None = None) -> str:
    """Append a stable version query to a relative asset path for cache busting."""
    if not asset_versions:
        return path
    clean_path, _, existing_query = path.partition("?")
    asset_name = Path(clean_path).name
    version = asset_versions.get(asset_name)
    if not version:
        return path
    if existing_query:
        return f"{clean_path}?{existing_query}&v={version}"
    return f"{clean_path}?v={version}"


def _write_repo_fragments(out_dir: Path, repos: list[dict], date_str: str) -> None:
    """Write lazy-loaded repo detail fragments for the report page."""
    fragment_dir = out_dir / "fragments"
    if fragment_dir.exists():
        shutil.rmtree(fragment_dir)
    fragment_dir.mkdir(parents=True, exist_ok=True)

    tmpl = _env.get_template("repo_section_inner.html")
    for repo in repos:
        fragment_html = tmpl.render(r=repo, date_str=date_str)
        fragment_path = fragment_dir / f"{repo['_slug']}.html"
        fragment_path.write_text(fragment_html, encoding="utf-8")


def _site_url_for_path(site_url: str, path: Path) -> str:
    index_suffix = "/index.html"
    site_url = site_url.rstrip("/")
    rel_path = path.as_posix()
    if rel_path == "index.html":
        return f"{site_url}/"
    if rel_path.endswith(index_suffix):
        return f"{site_url}/{rel_path[:-len(index_suffix)]}/"
    return f"{site_url}/{rel_path}"


# ── AI Contributors aggregation ──────────────────────────────

def _aggregate_ai_contributors(reports_dir: Path, up_to_date: str | None = None, days: int = 7) -> list[dict]:
    """Aggregate ai_contributors from the last *days* report files up to *up_to_date*."""
    from collections import defaultdict
    from engine.models import get_ai_tool_profile

    report_files = sorted(reports_dir.glob("report-*.json"))
    if up_to_date:
        report_files = [f for f in report_files
                        if f.stem.replace("report-", "") <= up_to_date]
    report_files = report_files[-days:]

    tool_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"commits": 0, "prs": 0, "reviews": 0})
    for rfile in report_files:
        try:
            data = json.loads(rfile.read_text(encoding="utf-8"))
        except Exception:
            continue
        for contrib in data.get("ai_contributors", []):
            tool = contrib.get("tool", "")
            if not tool:
                continue
            tool_stats[tool]["commits"] += contrib.get("commits", 0)
            tool_stats[tool]["prs"] += contrib.get("prs", 0)
            tool_stats[tool]["reviews"] += contrib.get("reviews", 0)

    ranked = sorted(
        tool_stats.items(),
        key=lambda kv: kv[1]["commits"] + kv[1]["prs"] + kv[1]["reviews"],
        reverse=True,
    )
    out = []
    for tool, counts in ranked:
        entry: dict = {"tool": tool, **counts}
        profile = get_ai_tool_profile(tool)
        if profile:
            entry["github_url"] = profile[0]
            entry["avatar_url"] = profile[1]
        out.append(entry)
    return out


# ── Build site ───────────────────────────────────────────────

def build_site(report_data: dict, out_dir: Path, *,
               history: dict[str, list[tuple[str, float]]] | None = None,
               available_dates: list[str] | None = None,
               css_path: str = "style.css",
               asset_versions: dict[str, str] | None = None) -> None:
    repos = report_data.get("repos", [])
    date_str = report_data.get("date", "unknown")
    config = _load_config()
    icon_map = _get_icon_map(config)
    site_url = _get_site_url(config)
    if history is None:
        history = {}
    if available_dates is None:
        available_dates = []

    repos = _enrich_repos(repos, icon_map, history)
    sorted_repos = sorted(repos, key=lambda r: r["aii"], reverse=True)

    css_base_path, _, _ = css_path.partition("?")
    js_base_path = css_base_path.replace("style.css", "app.js")
    favicon_base_path = css_base_path.replace("style.css", "favicon.svg")
    css_path = _versioned_asset_path(css_base_path, asset_versions)
    js_path = _versioned_asset_path(js_base_path, asset_versions)
    favicon_path = _versioned_asset_path(favicon_base_path, asset_versions)

    # Render main report page
    tmpl = _env.get_template("report.html")
    ai_contributors = report_data.get("ai_contributors", [])
    page = tmpl.render(
        repos=repos,
        sorted_repos=sorted_repos,
        date_str=date_str,
        css_path=css_path,
        js_path=js_path,
        favicon_path=favicon_path,
        site_url=site_url,
        ai_contributors=ai_contributors,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    _write_repo_fragments(out_dir, repos, date_str)
    index_path = out_dir / "index.html"
    index_path.write_text(page, encoding="utf-8")
    print(f"Site written → {index_path}")

    # Generate per-repo events detail pages
    events_tmpl = _env.get_template("events_page.html")
    for r in repos:
        events = r.get("events", [])
        if len(events) <= 15:
            continue
        sorted_events = sorted(events, key=lambda e: e["ai_score"], reverse=True)
        events_html = events_tmpl.render(
            repo_name=r["repo_name"],
            events=sorted_events,
            date_str=date_str,
            css_path=css_path,
            favicon_path=favicon_path,
        )
        events_path = out_dir / f"events-{r['_slug']}.html"
        events_path.write_text(events_html, encoding="utf-8")


# ── RSS feed ─────────────────────────────────────────────────

def _build_rss(reports_dir: Path, out_dir: Path, site_url: str) -> None:
    """Generate an RSS 2.0 feed with daily ranking + KPI data."""
    from datetime import datetime, timezone
    from xml.etree.ElementTree import Element, SubElement, tostring

    report_files = sorted(reports_dir.glob("report-*.json"), reverse=True)[:30]
    if not report_files:
        return

    site_url = site_url.rstrip("/")

    rss = Element("rss", version="2.0")
    channel = SubElement(rss, "channel")
    SubElement(channel, "title").text = "GitHub AI Radar"
    SubElement(channel, "link").text = site_url
    SubElement(channel, "description").text = "Daily AI involvement rankings for GitHub repositories"
    SubElement(channel, "language").text = "en"

    for rfile in report_files:
        data = json.loads(rfile.read_text(encoding="utf-8"))
        date_str = data.get("date", rfile.stem.replace("report-", ""))
        repos = data.get("repos", [])
        if not repos:
            continue

        # Sort by AII descending for ranking
        ranked = sorted(repos, key=lambda r: r.get("aii", 0), reverse=True)

        # Build description text
        lines = [f"📊 GitHub AI Radar — {date_str}", f"Repos analyzed: {len(ranked)}", ""]
        lines.append("Rank | Repository | AII | Commit AI | PR AI | Review AI")
        lines.append("-----|-----------|-----|-----------|-------|----------")
        for i, r in enumerate(ranked, 1):
            name = r.get("repo_name", "?")
            aii = f"{r.get('aii', 0):.1%}"
            c_ai = f"{r.get('commit_ai', 0)}/{r.get('commit_total', 0)}"
            p_ai = f"{r.get('pr_ai', 0)}/{r.get('pr_total', 0)}"
            rv_ai = f"{r.get('review_ai', 0)}/{r.get('review_total', 0)}"
            lines.append(f"#{i} | {name} | {aii} | {c_ai} | {p_ai} | {rv_ai}")

        # KPI summary
        avg_aii = sum(r.get("aii", 0) for r in ranked) / len(ranked) if ranked else 0
        total_events = sum(
            r.get("commit_total", 0) + r.get("pr_total", 0) + r.get("review_total", 0)
            for r in ranked
        )
        lines.extend([
            "",
            f"Average AII: {avg_aii:.1%}",
            f"Total events analyzed: {total_events}",
        ])

        item = SubElement(channel, "item")
        SubElement(item, "title").text = f"AI Radar Report — {date_str}"
        SubElement(item, "link").text = f"{site_url}/{date_str}/"
        SubElement(item, "guid").text = f"{site_url}/{date_str}/"
        SubElement(item, "pubDate").text = datetime.strptime(
            date_str, "%Y-%m-%d"
        ).replace(tzinfo=timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
        SubElement(item, "description").text = "\n".join(lines)

    xml_bytes = tostring(rss, encoding="unicode", xml_declaration=False)
    rss_content = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_bytes
    rss_path = out_dir / "feed.xml"
    rss_path.write_text(rss_content, encoding="utf-8")
    print(f"RSS feed → {rss_path}")


def _build_sitemap(out_dir: Path, site_url: str) -> None:
    """Generate a sitemap.xml for all rendered HTML pages."""
    from xml.etree.ElementTree import Element, SubElement, tostring

    site_url = site_url.rstrip("/")
    html_pages = [
        page for page in sorted(out_dir.rglob("*.html"))
        if "fragments" not in page.relative_to(out_dir).parts
    ]
    if not html_pages:
        return

    urlset = Element("urlset", xmlns="http://www.sitemaps.org/schemas/sitemap/0.9")
    for page in html_pages:
        rel_path = page.relative_to(out_dir)
        url = SubElement(urlset, "url")
        SubElement(url, "loc").text = _site_url_for_path(site_url, rel_path)

    xml_bytes = tostring(urlset, encoding="unicode", xml_declaration=False)
    sitemap_content = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_bytes
    sitemap_path = out_dir / "sitemap.xml"
    sitemap_path.write_text(sitemap_content, encoding="utf-8")
    print(f"Sitemap → {sitemap_path}")


def _build_robots(out_dir: Path, site_url: str) -> None:
    """Generate a robots.txt that points crawlers at the sitemap."""
    site_url = site_url.rstrip("/")
    robots_content = "\n".join([
        "User-agent: *",
        "Allow: /",
        f"Sitemap: {site_url}/sitemap.xml",
        "",
    ])
    robots_path = out_dir / "robots.txt"
    robots_path.write_text(robots_content, encoding="utf-8")
    print(f"Robots → {robots_path}")


def _build_deploy_config(out_dir: Path, config: dict | None = None) -> None:
    """Write Cloudflare Workers deploy files (.gitignore, wrangler.jsonc, .nojekyll)."""
    data = config or _load_config()
    deploy = data.get("deploy", {})

    gitignore_content = "\n".join([
        "# wrangler files",
        ".wrangler",
        ".dev.vars*",
        "!.dev.vars.example",
        ".env*",
        "!.env.example",
        "",
        "# dependencies",
        "node_modules/",
        ".npm/",
        "",
    ])
    (out_dir / ".gitignore").write_text(gitignore_content, encoding="utf-8")

    wrangler_content = json.dumps({
        "$schema": "node_modules/wrangler/config-schema.json",
        "name": deploy.get("worker_name", "github-ai-detector"),
        "compatibility_date": deploy.get("compatibility_date", "2026-04-03"),
        "observability": {"enabled": deploy.get("observability", True)},
        "assets": {"directory": "."},
        "compatibility_flags": ["nodejs_compat"],
    }, indent=2) + "\n"
    (out_dir / "wrangler.jsonc").write_text(wrangler_content, encoding="utf-8")

    (out_dir / ".nojekyll").write_text("", encoding="utf-8")

    print(f"Deploy config → {out_dir}/.gitignore, wrangler.jsonc, .nojekyll")


# ── Build history index ──────────────────────────────────────

def build_history_index(reports_dir: Path, out_dir: Path) -> None:
    report_files = sorted(reports_dir.glob("report-*.json"), reverse=True)
    if not report_files:
        return

    history = _load_history(reports_dir)
    available_dates = sorted([
        json.loads(f.read_text(encoding="utf-8")).get("date", f.stem.replace("report-", ""))
        for f in report_files
    ])

    # Copy static assets to site root
    _copy_static(out_dir)
    asset_versions = _build_asset_versions()

    # Generate per-day report pages
    for rfile in report_files:
        data = json.loads(rfile.read_text(encoding="utf-8"))
        date_str = data.get("date", rfile.stem.replace("report-", ""))
        data["ai_contributors"] = _aggregate_ai_contributors(reports_dir, up_to_date=date_str)
        day_dir = out_dir / date_str
        build_site(data, day_dir, history=history, available_dates=available_dates,
                   css_path="../style.css", asset_versions=asset_versions)

    # Build root page — use the latest report
    root_file = report_files[0]
    root_data = json.loads(root_file.read_text(encoding="utf-8"))
    root_data["ai_contributors"] = _aggregate_ai_contributors(reports_dir)
    build_site(root_data, out_dir, history=history, available_dates=available_dates,
               asset_versions=asset_versions)

    # Build history index page
    entries = []
    for rfile in report_files:
        data = json.loads(rfile.read_text(encoding="utf-8"))
        entries.append({
            "date": data.get("date", rfile.stem.replace("report-", "")),
            "n_repos": len(data.get("repos", [])),
        })

    tmpl = _env.get_template("history.html")
    history_html = tmpl.render(
        entries=entries,
        css_path=_versioned_asset_path("style.css", asset_versions),
        favicon_path=_versioned_asset_path("favicon.svg", asset_versions),
    )
    history_path = out_dir / "history.html"
    history_path.write_text(history_html, encoding="utf-8")
    print(f"History index → {history_path}")

    # Generate discovery files
    site_url = _get_site_url()
    _build_rss(reports_dir, out_dir, site_url)
    _build_sitemap(out_dir, site_url)
    _build_robots(out_dir, site_url)

    # Generate deploy configuration files
    _build_deploy_config(out_dir)


# ── Main ─────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="GitHub AI-Radar — static HTML report builder")
    parser.add_argument("--input", default="reports", help="Reports directory with JSON files")
    parser.add_argument("--out", default="site", help="Output directory for static site")
    args = parser.parse_args()

    reports_dir = Path(args.input)
    out_dir = Path(args.out)

    if not reports_dir.exists():
        print(f"Reports directory not found: {reports_dir}")
        return

    latest_path = reports_dir / "latest.json"
    if latest_path.exists():
        build_history_index(reports_dir, out_dir)
    else:
        report_files = sorted(reports_dir.glob("report-*.json"), reverse=True)
        if report_files:
            build_history_index(reports_dir, out_dir)
        else:
            print("No report files found.")


if __name__ == "__main__":
    main()
