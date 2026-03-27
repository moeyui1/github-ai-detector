"""
Centralised configuration loader for GitHub AI-Radar.
Reads config.toml from the project root and exposes a typed Config object.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    import tomli as tomllib  # Python 3.10 fallback

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config.toml"


@dataclass
class GitHubConfig:
    repos: list[str] = field(default_factory=list)
    token: str = ""
    trending_count: int = 0
    trending_ai_count: int = 0
    trending_ai_topics: list[str] = field(default_factory=lambda: ["artificial-intelligence", "machine-learning", "llm", "generative-ai", "deep-learning"])


@dataclass
class LLMConfig:
    provider: str = "none"
    model: str = "gpt-4o-mini"
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    concurrency: int = 30


@dataclass
class WeightsConfig:
    commit: float = 0.5
    pr: float = 0.3
    review: float = 0.2


@dataclass
class AnalysisConfig:
    max_items: int = 50
    max_pages: int = 10
    high_risk_threshold: float = 0.6
    inactive_days: int = 14
    weights: WeightsConfig = field(default_factory=WeightsConfig)


@dataclass
class BotsConfig:
    system: list[str] = field(default_factory=list)
    ai: list[str] = field(default_factory=list)


@dataclass
class DatabaseConfig:
    path: str = "ai_radar.db"


@dataclass
class SiteConfig:
    site_url: str = ""


@dataclass
class Config:
    github: GitHubConfig = field(default_factory=GitHubConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    bots: BotsConfig = field(default_factory=BotsConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    site: SiteConfig = field(default_factory=SiteConfig)


def load_config(path: str | Path | None = None) -> Config:
    """Load configuration from a TOML file, with env-var overrides for secrets."""
    from log import get_logger
    log = get_logger("config")

    cfg_path = Path(path) if path else _DEFAULT_CONFIG_PATH
    log.info("Loading config from %s", cfg_path)

    raw: dict = {}
    if cfg_path.is_file():
        raw = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    else:
        log.warning("Config file not found: %s — using defaults", cfg_path)

    gh_raw = raw.get("github", {})
    llm_raw = raw.get("llm", {})
    analysis_raw = raw.get("analysis", {})
    weights_raw = analysis_raw.pop("weights", {}) if isinstance(analysis_raw, dict) else {}
    bots_raw = raw.get("bots", {})
    db_raw = raw.get("database", {})
    site_raw = raw.get("site", {})

    # Backward compat: old config may have `repo_url` (string) instead of `repos` (list)
    if "repo_url" in gh_raw and "repos" not in gh_raw:
        url = gh_raw.pop("repo_url")
        gh_raw["repos"] = [url] if url else []
    elif "repo_url" in gh_raw:
        gh_raw.pop("repo_url")

    # Backward compat: old config may have extra_system/extra_ai
    if "extra_system" in bots_raw:
        bots_raw.setdefault("system", []).extend(bots_raw.pop("extra_system"))
    if "extra_ai" in bots_raw:
        bots_raw.setdefault("ai", []).extend(bots_raw.pop("extra_ai"))

    # Ignore sensitive fields from config file — they must come from env vars
    gh_raw.pop("token", None)
    llm_raw.pop("api_key", None)

    cfg = Config(
        github=GitHubConfig(**gh_raw),
        llm=LLMConfig(**llm_raw),
        analysis=AnalysisConfig(weights=WeightsConfig(**weights_raw), **analysis_raw),
        bots=BotsConfig(**bots_raw),
        database=DatabaseConfig(**db_raw),
        site=SiteConfig(**site_raw),
    )

    # Secrets: only from environment variables
    cfg.github.token = os.environ.get("GITHUB_TOKEN", "")
    if cfg.github.token:
        log.info("GITHUB_TOKEN loaded from environment")
    cfg.llm.api_key = os.environ.get("OPENAI_API_KEY", "")
    if cfg.llm.api_key:
        log.info("OPENAI_API_KEY loaded from environment")
    # Non-secret overrides from environment
    if env_base := os.environ.get("OPENAI_BASE_URL"):
        cfg.llm.base_url = env_base
        log.info("OPENAI_BASE_URL loaded from environment")
    if env_provider := os.environ.get("LLM_PROVIDER"):
        cfg.llm.provider = env_provider
        log.info("LLM_PROVIDER loaded from environment")
    if env_concurrency := os.environ.get("LLM_CONCURRENCY"):
        try:
            cfg.llm.concurrency = int(env_concurrency)
            log.info("LLM_CONCURRENCY loaded from environment: %d", cfg.llm.concurrency)
        except ValueError:
            log.warning("Invalid LLM_CONCURRENCY value: %s", env_concurrency)

    log.info("Config loaded | repos=%d | llm=%s | model=%s | max_items=%d | concurrency=%d",
             len(cfg.github.repos), cfg.llm.provider, cfg.llm.model,
             cfg.analysis.max_items, cfg.llm.concurrency)

    return cfg


# Module-level singleton — imported by other modules
_config: Config | None = None


def get_config() -> Config:
    """Return the cached Config singleton, loading on first call."""
    global _config
    if _config is None:
        _config = load_config()
    return _config
