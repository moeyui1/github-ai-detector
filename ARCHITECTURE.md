# ARCHITECTURE.md — GitHub AI-Radar 技术架构文档

> **读者对象**：后续接手开发的 AI 编程模型（Copilot / Cursor / Claude 等）及人类开发者。
> 本文档完整描述系统的模块职责、数据流、核心算法、类型约定、扩展点和已知限制，以供准确理解与修改代码。

---

## 目录

1. [总体架构](#1-总体架构)
2. [文件清单与职责](#2-文件清单与职责)
3. [数据流（端到端）](#3-数据流端到端)
4. [模块详解：providers.py](#4-模块详解providerspyLLM-抽象层)
5. [模块详解：engine.py](#5-模块详解enginepy分析引擎)
6. [核心算法：三层过滤](#6-核心算法三层过滤)
7. [核心算法：AII 评分公式](#7-核心算法aii-评分公式)
8. [关键类型与数据结构](#8-关键类型与数据结构)
9. [环境变量与配置](#9-环境变量与配置)
10. [扩展指南](#10-扩展指南)
11. [已知限制与 TODO](#11-已知限制与-TODO)
12. [开发约定](#12-开发约定)

---

## 1. 总体架构

```
┌─────────────────────────────────────────────────────────┐
│               CLI / report (analyze.py, report/)        │
│  ┌────────────────────────────────────────────────────┐  │
│  │     engine/analysis.py → analyze_repo()           │  │
│  │  ┌──────┐  ┌──────┐  ┌──────┐  → AnalysisResult   │  │
│  │  │L1 Bot│→│L2 AI │→│L3 LLM│                      │  │
│  │  │Filter│  │Match │  │Audit │  (concurrent)       │  │
│  │  └──────┘  └──────┘  └──┬───┘                      │  │
│  │  commits.py / pulls.py / issues.py / scoring.py    │  │
│  └──────────────────────────┼─────────────────────────┘  │
│                             │                            │
│  ┌──────────────────────────▼─────────────────────────┐  │
│  │       providers/base.py → _call_llm() / _call_llm_batch() │
│  │  ┌─────────────────┐  ┌────────────────────────┐   │  │
│  │  │ openai_provider  │  │ github_provider        │   │  │
│  │  └─────────────────┘  └────────────────────────┘   │  │
│  │          ↑ prompts/detect_ai.txt (单项 prompt)      │  │
│  │          ↑ prompts/detect_ai_batch.txt (批量 prompt) │  │
│  └─────────────────────────────────────────────────────┘  │
│                             │                            │
│  └─────────────────────────────────────────────────────┘  │
│                             │                            │
└─────────────────────────────┼────────────────────────────┘
                              │
            ┌─────────────────┼─────────────────┐
            ▼                 ▼                 ▼
   GitHub REST API     OpenAI API      GitHub Models API
   (commits/PRs/       (chat/          (chat/
    issues)            completions)     completions)
```

### 关键设计原则

- **分层解耦**：业务逻辑 (`engine/`) / LLM 交互 (`providers/`) / 报告生成 (`report/`) / Prompt (`prompts/`) 完全分离。
- **分析与展示分离**：`engine/` 负责分析，`report/` 负责报告生成。engine 包可在 CLI、测试、API server 等任何上下文中独立使用。
- **Prompt 外部化**：所有 LLM prompt 以纯文本文件存放在 `prompts/` 目录，修改 prompt 无需改动代码。
- **Provider 可选**：传入 `provider=None` 时引擎仅使用 L1/L2 静态规则，不发起任何 LLM 调用。
- **多仓库支持**：`config.toml` 配置多个仓库，CLI 批量分析并生成报告。
- **实时快照模型**：每次运行拉取最新 N 条事件（而非时间窗口过滤），生成当前时刻的快照报告。
- **事件级缓存**：`engine/cache.py` 跟踪已分析事件的 `updated_at` 时间戳，未变更的事件直接复用缓存分数，避免重复 LLM 调用。

---

## 2. 文件清单与职责

### 顶层模块

| 文件 | 职责 | 对外暴露 |
|------|------|----------|
| `analyze.py` | 单项分析 CLI 工具（一行命令分析 PR/Issue/Commit） | `main()` |
| `config.py` | 配置加载器；读取 config.toml 并提供类型化 Config 单例 | `Config`, `load_config()`, `get_config()` |
| `config.toml` | 集中式 TOML 配置文件 | — |
| `log.py` | 日志模块，默认启用（INFO），通过 `LOG_LEVEL` 环境变量控制 | `get_logger()` |
| `requirements.txt` | pip 依赖声明 | — |

### `.github/workflows/` — CI/CD

| 文件 | 职责 |
|------|------|
| `daily-report.yml` | GitHub Actions 每日定时任务：分析 config.toml 中的仓库 → 生成 JSON → 渲染静态站点 → 部署到 GitHub Pages |

### `report/` — 报告生成包

| 文件 | 职责 | 对外暴露 |
|------|------|----------|
| `__init__.py` | 包入口 | — |
| `cli.py` | CLI 批量分析入口，按 `config.toml` 或命令行参数分析仓库，输出 JSON；集成事件缓存避免重复评分 | `main()` |
| `html.py` | 静态 HTML 报告生成器，基于 Jinja2 模板引擎将 JSON 渲染为带侧边栏导航的响应式站点 | `build_site()`, `build_history_index()`, `main()` |
| `templates/` | Jinja2 模板目录，包含 `base.html`、`report.html`、`summary.html`、`repo_section.html`、`events_page.html`、`history.html`、`macros.html` | — |
| `static/` | 静态资源目录，包含 `style.css`、`app.js`、`favicon.svg` | — |

### `prompts/` — Prompt 模板

| 文件 | 职责 |
|------|------|
| `__init__.py` | `load_prompt(name)` 加载指定 prompt 文件 |
| `detect_ai.txt` | 单项 AI 检测 system prompt（纯文本，修改无需改代码） |
| `detect_ai_batch.txt` | 批量 AI 检测 system prompt，用于一次评分多条事件 |

### `providers/` — LLM Provider 抽象层

| 文件 | 职责 | 对外暴露 |
|------|------|----------|
| `__init__.py` | 包入口，re-export + 工厂函数 | `BaseProvider`, `LLMCallResult`, `get_provider()` |
| `base.py` | 基类 `BaseProvider`、`LLMCallResult` dataclass、共享 LLM 调用逻辑（重试 & 参数兼容） | `BaseProvider`, `LLMCallResult` |
| `openai_provider.py` | OpenAI 兼容端点 Provider | `OpenAIProvider` |
| `github_provider.py` | GitHub Models 端点 Provider | `GitHubModelsProvider` |

### `engine/` — 分析引擎

| 文件 | 职责 | 对外暴露 |
|------|------|----------|
| `__init__.py` | 包入口，re-export 所有公共接口 | 见下方各模块 |
| `models.py` | 数据模型 + Bot 分类逻辑 | `ActorKind`, `EventRecord`, `LLMLogEntry`, `AnalysisResult`, `SingleItemResult`, `classify_actor()` |
| `github_api.py` | GitHub REST API 请求（带重试、并发批量获取） | `fetch_commits()`, `fetch_pulls_and_issues()`, `fetch_pr_reviews_batch()`, `fetch_issue_comments_batch()`, 单项 fetch 系列 |
| `cache.py` | 事件级缓存 — 跟踪已分析事件，避免对未变更事件重复调用 LLM | `CacheData`, `CacheRepo`, `load_cache()`, `save_cache()`, `event_key()`, `event_updated_at()` |
| `scoring.py` | LLM 评分辅助 — rebase 纠偏、安全调用（单项 + 批量）、并发执行 | `_run_llm_tasks()`, `_safe_llm_score()`, `_safe_llm_score_batch()`, `_rebase_penalty()` |
| `commits.py` | Commit 处理 — 批量事件构建 + 单 commit 分析 | `build_commit_events()`, `analyze_single_commit()` |
| `pulls.py` | PR 处理 — 批量事件构建（reviews 作为上下文附加，非独立事件）+ 单 PR 分析 | `build_pr_events()` → 4-tuple, `analyze_single_pr()` |
| `issues.py` | Issue 处理 — 批量事件构建（comments 作为上下文附加，非独立事件）+ 单 issue 分析 | `build_issue_events()` → 4-tuple, `analyze_single_issue()` |
| `analysis.py` | 主管线 — `analyze_repo()` / `analyze_single()` / URL 解析 | `analyze_repo()`, `analyze_single()`, `parse_repo_url()`, `parse_item_url()` |

---

## 3. 数据流（端到端）

```
用户输入 repo URL 列表 + 选择 LLM provider
         │
         ▼
report/cli.py: for url in repo_list:
                 parse_repo_url(url) → (owner, repo)
               get_provider(name, **kwargs) → BaseProvider | None
               load_cache() → CacheData
         │
         ▼
engine.analyze_repo(owner, repo, token, provider, max_items, cache)
         │
         ├── github_api.fetch_commits(max_items, max_pages) ──→ GitHub API（分页拉取，过滤 merge commit）
         └── github_api.fetch_pulls_and_issues(max_items) ─→ 分别调用 /pulls 和 /issues 端点
               └→ 返回 (prs: list[dict], issues: list[dict])（各自独立 max_items）
         │
         ├── fetch_pr_reviews_batch()       ──→ 并发批量获取 PR reviews
         └── fetch_issue_comments_batch()   ──→ 并发批量获取 Issue comments
         │
         ▼
    commits.build_commit_events() → (total, bots)
    pulls.build_pr_events(reviews_by_pr=...)
        → (total, bots, review_total, review_ai)   # reviews 内容附加到 PR，AI 计数仅靠 L1/L2
    issues.build_issue_events(comments_by_issue=...)
        → (total, bots, comment_total, comment_ai)  # comments 内容附加到 Issue，AI 计数仅靠 L1/L2
    对每个事件执行三层过滤：
         │
         ├── models.classify_actor(login) → ActorKind
         │     ├─ SYSTEM_BOT → ai_score = 0.0, bot_events++
         │     ├─ AI_BOT     → ai_score = 1.0, bot_events++
         │     └─ HUMAN      → 进入缓存查找 / L3
         │
         ├── 缓存查找: 若事件 updated_at 未变更 → 复用缓存 ai_score + reason
         │
         └── L3: 批量 LLM 评分（batch_size=10，仅对未命中缓存的事件）
               scoring._safe_llm_score_batch() → providers.analyze_batch()
               → _call_llm_batch() → 单次 LLM 调用评分多条事件
               │  (commit 特例: raw_score -= rebase_penalty)
               └→ EventRecord.ai_score = clamped score
         │
         ▼
    聚合 → (AnalysisResult, CacheRepo)
         │
         ▼
report/cli.py: save_cache() → 保存更新后的缓存
               序列化为 JSON 报告 (report-YYYY-MM-DD.json + latest.json)
report/html.py: 基于 Jinja2 模板渲染为静态 HTML 站点
```

---

## 4. 模块详解：providers/（LLM 抽象层）

> 位于 `providers/` 包，每个 Provider 实现在独立文件中。

### 4.1 类继承结构

```
BaseProvider (ABC)              # providers/base.py
├── OpenAIProvider              # providers/openai_provider.py
└── GitHubModelsProvider        # providers/github_provider.py
```

### 4.2 BaseProvider（base.py）

| 成员 | 类型 | 说明 |
|------|------|------|
| `analyze_text(text)` | 抽象方法 | 接收原始文本，返回 `LLMCallResult`（含 score + reason + model + token 用量 + 错误信息） |
| `analyze_batch(texts)` | 实例方法 | 批量评分：一次 LLM 调用评分多条文本，返回 `list[LLMCallResult]`；默认实现逐条调用 `analyze_text`，子类覆盖为真正的批量调用 |
| `_build_messages(text)` | 实例方法 | 将文本截断至 1000 字符，从 `prompts/detect_ai.txt` 加载 system prompt，构建消息列表 |
| `_build_batch_messages(texts)` | 实例方法 | 将多条文本拼接为编号格式（`[1]\n...\n---\n[2]\n...`），每条截断至 800 字符，使用 `prompts/detect_ai_batch.txt` 作为 system prompt |
| `_parse_response(raw)` | 静态方法 | 从 LLM 返回的 JSON 字符串中提取 score 和 reason；自动清理 `<think>` 标签和 markdown code fence；包含 fallback 正则匹配 |
| `_parse_batch_response(raw, expected)` | 静态方法 | 从 LLM 返回的 JSON 数组中解析多条 `(score, reason)` 结果；支持正则 fallback |
| `_call_llm(client, model, text)` | 实例方法 | 共享的单项 LLM 调用实现，包含指数退避重试（5 次，429/RateLimitError）和不支持参数的自动降级 |
| `_call_llm_batch(client, model, texts)` | 实例方法 | 批量 LLM 调用，重试逻辑同 `_call_llm`；Token 用量按条目数均分记录；429 响应体记录到 WARNING 日志（截断至 300 字符） |

### 4.2.1 LLMCallResult

```python
@dataclass
class LLMCallResult:
    score: float = 0.5
    reason: str = ""              # LLM 判定原因
    model: str = ""               # 使用的模型名称
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    raw_response: str = ""
    error: str = ""           # 非空表示调用失败
```

### 4.2.2 Prompt 加载

System prompt 从 `prompts/detect_ai.txt` 读取（首次调用时加载并缓存）。修改 prompt 只需编辑该文本文件，无需改动任何 Python 代码。

### 4.2.3 重试与参数兼容

- **指数退避**：遇到 429 / RateLimitError 时 1s → 2s → 4s → 8s → 16s 重试，最多 5 次
- **参数降级**：遇到 400 + "does not support" 时，自动移除不支持的参数（如 `max_completion_tokens`）并立即重试
- **max_completion_tokens**：默认 4096（为推理模型的 chain-of-thought 预留足够空间）

### 4.2.4 推理模型（Reasoning Model）处理

推理模型（如 Kimi-K2.5、DeepSeek-R1、o1）在输出最终回答前会先产生思考过程。系统的处理策略：

1. **只取 `message.content`**（最终回答），不使用 `reasoning_content`（思考过程）
2. **清理 `<think>` 标签**：部分开源模型会在 content 中混入 `<think>...</think>` 标签，自动剥离
3. **content 为空时告警**：记录 `has_reasoning` 和 `finish_reason`，提示可能需要加大 `max_completion_tokens`
4. **清理 markdown code fence**：部分模型返回 ` ```json ... ``` ` 包裹的 JSON，自动去除

### 4.3 OpenAIProvider（openai_provider.py）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `api_key` | `$OPENAI_API_KEY` | API 密钥 |
| `base_url` | `https://api.openai.com/v1` | 可替换为任何 OpenAI 兼容端点 |
| `model` | `gpt-4o-mini` | 模型标识 |

通过 openai SDK 调用，使用 `Bearer` Token 鉴权。

### 4.4 GitHubModelsProvider（github_provider.py）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `token` | `$GITHUB_TOKEN` | GitHub PAT |
| `model` | `gpt-4o-mini` | 模型标识 |

端点：`https://models.github.ai/inference`，通过 openai SDK 调用。

### 4.5 工厂函数

```python
get_provider(name: str, **kwargs) -> BaseProvider
```

- `name` 取值：`"openai"` | `"github"`（大小写不敏感）
- `**kwargs` 透传至对应 Provider 的 `__init__`

### 4.6 扩展新 Provider

1. 在 `providers/` 下创建新文件（如 `anthropic_provider.py`）
2. 继承 `BaseProvider`，实现 `analyze_text(self, text: str) -> LLMCallResult`
3. 在 `providers/__init__.py` 的 `get_provider()` 字典中注册

---

## 5. 模块详解：engine/（分析引擎）

> 位于 `engine/` 包，按职责拆分为多个子模块。

### 5.1 Bot 列表（models.py）

| 常量 | 条目数 | 用途 |
|------|--------|------|
| `SYSTEM_BOT_LIST` | 30+ | L1 过滤 — 纯自动化系统 Bot，评分直接置 0 |
| `AI_BOT_LIST` | 20+ | L2 匹配 — 已知 AI 代码助手 Bot，评分直接置 1.0 |

**分类逻辑** (`classify_actor`)：
1. `login.lower()` 在 `AI_BOT_LIST` 中 → `AI_BOT`
2. 在 `SYSTEM_BOT_LIST` 中 **或** 以 `[bot]` 结尾且不在 AI 列表 → `SYSTEM_BOT`
3. 其余 → `HUMAN`

> **注意**：AI_BOT_LIST 优先匹配，确保 AI Bot 不会被 `[bot]` 后缀误判为 System Bot。

### 5.2 GitHub API 交互（github_api.py）

**关键常量：**

| 常量 | 值 | 说明 |
|------|-----|------|
| `_GH_CONCURRENCY` | 10 | 批量并发请求最大并行数 |
| `_MAX_RETRIES` | 3 | 指数退避重试次数 |
| `_RETRY_DELAY` | 1.0 | 重试基础延迟（秒） |

**重试机制：**
- `_gh_get()` / `_gh_get_one()` 均支持指数退避重试
- 可重试条件：429（Rate Limit）、502/503/504（服务器错误）、网络超时
- 延迟公式：`delay = 1.0 * (2 ** attempt)`

**合并 Fetch（核心优化）：**

| 函数 | 说明 |
|------|------|
| `fetch_commits(owner, repo, token, max_items=50, max_pages=10)` | 分页获取最新非 merge commit。自动过滤 `Merge ...` 开头的合并提交，持续翻页直到凑够 `max_items` 条有效 commit 或达到 `max_pages` 上限 |
| `fetch_pulls_and_issues(owner, repo, token, max_items=50)` | 分别调用 `/pulls` 和 `/issues` 端点独立获取 PR 和 Issue（按 `updated_at` 降序）。PR 和 Issue 各自享有 `max_items` 配额，不再互相抢占。对于禁用了 PR 功能的仓库（如 torvalds/linux），`/pulls` 返回 404 时自动跳过。返回 `(prs, issues)` 元组 |

**并发批量获取：**

| 函数 | 说明 |
|------|------|
| `fetch_pr_reviews_batch(owner, repo, token, pr_numbers)` | 并发批量获取多个 PR 的 reviews，返回 `dict[int, list[dict]]` |
| `fetch_issue_comments_batch(owner, repo, token, issue_numbers)` | 并发批量获取多个 Issue 的 comments，返回 `dict[int, list[dict]]` |

**单项 Fetch：**

| 函数 | 说明 |
|------|------|
| `fetch_single_commit(owner, repo, sha, token)` | 单个 commit |
| `fetch_single_pr(owner, repo, number, token)` | 单个 PR |
| `fetch_single_issue(owner, repo, number, token)` | 单个 issue |
| `fetch_pr_comments(owner, repo, number, token)` | PR 的 review comments + issue comments |
| `fetch_pr_reviews(owner, repo, number, token)` | PR 的 reviews |
| `fetch_pr_commits(owner, repo, number, token)` | PR 包含的 commits |
| `fetch_issue_comments(owner, repo, number, token)` | Issue 的 comments |

### 5.2.1 事件级缓存（cache.py）

| 类型/函数 | 说明 |
|-----------|------|
| `CacheRepo = dict[str, dict]` | 单仓库缓存：`event_key → {updated_at, ai_score, reason}` |
| `CacheData = dict[str, CacheRepo]` | 全局缓存：`repo_name → CacheRepo` |
| `load_cache(path)` | 从 `reports/cache.json` 加载缓存（缺失或损坏时返回空 dict） |
| `save_cache(data, path)` | 覆盖写入缓存文件 |
| `event_key(kind, raw)` | 生成稳定缓存键：`commit:sha` / `pr:number` / `issue:number` |
| `event_updated_at(kind, raw)` | 提取事件的 `updated_at`（commit 取 `commit.author.date`） |

**缓存工作流**：
1. `report/cli.py` 启动时调用 `load_cache()` 加载上次运行的缓存
2. `analyze_repo()` 接收 `cache: CacheRepo`，在事件构建完成后、LLM 评分前逐一查找缓存
3. 若事件的 `updated_at` 与缓存一致，直接复用 `ai_score` 和 `reason`，跳过 LLM 调用
4. 所有事件（含新评分和缓存命中）写入 `new_cache:CacheRepo` 并返回
5. `report/cli.py` 收集各仓库的 `new_cache` 后调用 `save_cache()` 持久化

### 5.3 LLM 评分辅助（scoring.py）

#### Rebase 纠偏

```python
def _rebase_penalty(commit: dict) -> float
```

当 `commit.author.date != commit.committer.date` 时，返回 0.3 的扣减量。这用于对冲 rebase/squash 操作导致的时间密度异常——这些操作是正常的 Git 工作流，不应被视为 AI 信号。

最终 commit 的 AI 分数 = `max(0.0, raw_llm_score - rebase_penalty)`

#### 安全调用（单项 + 批量）

```python
def _safe_llm_score(provider, text) -> LLMCallResult       # 单条评分，异常返回 0.0
def _safe_llm_score_batch(provider, texts) -> list[LLMCallResult]  # 批量评分
```

`_safe_llm_score_batch()`：
- 若仅 1 条文本，退化为 `_safe_llm_score()` 单条调用
- 多条文本调用 `provider.analyze_batch(texts)`
- 失败时返回全零分数（**不回退到逐条调用**，避免加剧 Rate Limit）

#### 并发 LLM 执行器

```python
def _run_llm_tasks(llm_tasks, provider, concurrency, update) -> list[LLMLogEntry]
```

使用 `ThreadPoolExecutor` 并发调用 LLM，收集 `LLMLogEntry`（含 token 用量和错误信息）。

### 5.4 事件处理模块

| 模块 | 批量函数 | 返回值 | 单项函数 |
|------|----------|--------|----------|
| `commits.py` | `build_commit_events()` | `(total, bots)` | `analyze_single_commit()` |
| `pulls.py` | `build_pr_events()` | `(total, bots, review_total, review_ai)` | `analyze_single_pr()` — 含 commits + comments + reviews |
| `issues.py` | `build_issue_events()` | `(0, 0, comment_total, comment_ai)` | `analyze_single_issue()` — 含 comments |

**PR / Commit 显式 AI 检测（L3 前置）：**

在进入 LLM 评分（L3）之前，先通过正则匹配检测是否有显式的 AI 参与声明：
- **PR**：扫描 PR body 是否含 "contributed by ... AI assistant"、"AI-generated" 等模式 → 命中则 `ai_score = 1.0`，跳过 LLM
- **Commit**：检测 Git trailer 如 `Assisted-by: Gemini`、`Co-authored-by: ... Copilot` → 命中则 `ai_score = 1.0`，跳过 LLM

**Issue 处理策略：**

Issue 本身**不再作为事件**分析（不进入 LLM 评分），仅统计 issue comments 中的 AI Bot 参与（L1/L2 作者名判定）。`build_issue_events()` 返回的事件计数恒为 `(0, 0, ...)`。

**Reviews / Comments 处理策略（关键优化）：**

Reviews 和 Issue comments **不再**作为独立事件生成，而是：
1. **内容附加**：Review/comment 的文本片段附加到父 PR/Issue 的 LLM 评分文本中（格式：`[login(kind)/state]: body[:200]`，总量上限 500 字符），作为 LLM 评分的上下文参考
2. **AI 计数**：review_total/review_ai 和 comment_total/comment_ai 由 `classify_actor()` 的 L1/L2 规则**纯按作者名**判定，不需要 LLM 调用
3. **效果**：大幅减少 LLM 调用量（如 openclaw 从 1280 个事件降至 180 个）

每个模块遵循相同模式：
1. 遍历原始 API 数据
2. `classify_actor()` 判定 L1/L2
3. 对 HUMAN 事件创建 LLM 任务（附加 review/comment 上下文）
4. 单项分析额外获取讨论/参与者信息

### 5.5 analyze_repo() 主入口（analysis.py）

```python
def analyze_repo(
    owner: str,
    repo: str,
    token: str,
    provider: BaseProvider | None = None,
    max_items: int = 50,
    progress_callback: Callable[[str], None] | None = None,
    concurrency: int | None = None,
    cache: CacheRepo | None = None,
) -> tuple[AnalysisResult, CacheRepo]
```

**参数说明**：

| 参数 | 说明 |
|------|------|
| `owner`, `repo` | 仓库拥有者与名称 |
| `token` | GitHub PAT（可空） |
| `provider` | LLM Provider 实例；`None` 表示跳过 L3 |
| `max_items` | 每类事件最大拉取条数（默认 50） |
| `progress_callback` | UI 进度回调 |
| `concurrency` | LLM 并发数，默认从 config.toml 读取 |
| `cache` | 事件缓存（上次运行的结果），传入后未变更事件将复用缓存分数 |

**返回**：`tuple[AnalysisResult, CacheRepo]` — 分析结果 + 更新后的缓存数据

**处理顺序**：
1. `fetch_commits(max_items, max_pages)` 分页获取最新非 merge commit（自动过滤合并提交）
2. `fetch_pulls_and_issues(max_items)` 分别通过 `/pulls` 和 `/issues` 端点独立获取 PR 和 Issue（各自 max_items）
3. 并发批量获取 PR reviews（`fetch_pr_reviews_batch()`）和 Issue comments（`fetch_issue_comments_batch()`）
4. `build_commit_events()` / `build_pr_events()` / `build_issue_events()` 构建事件列表
5. **缓存查找**：将每个 HUMAN 事件与原始 API 数据匹配（`_find_event_key()`），若事件的 `updated_at` 与缓存一致，直接复用缓存的 `ai_score` 和 `reason`
6. **批量 LLM 评分（L3）**：仅对未命中缓存的事件，按 `batch_size=10` 分组调用 `_safe_llm_score_batch()`
7. 所有事件的结果写入 `new_cache`，与 `AnalysisResult` 一起返回

**计数指标**：
- `commit_ai`：使用 LLM 分数（≥ `high_risk_threshold`）或 `actor_kind == AI_BOT` 或被 AI trailer 正则命中
- `pr_ai`：使用 LLM 分数（≥ `high_risk_threshold`）或 `actor_kind == AI_BOT` 或被显式 AI 声明正则命中
- `review_ai`、`issue_comment_ai`：由 `build_pr_events` / `build_issue_events` 的 4-tuple 返回值直接设置（仅 L1/L2 判定）

### 5.6 analyze_single()（单项分析）

```python
def analyze_single(owner, repo, item_type, identifier, token, ...) -> SingleItemResult
```

根据 `item_type` 分发到 `analyze_single_commit()` / `analyze_single_pr()` / `analyze_single_issue()`，支持 `owner/repo#N` 简写自动检测 PR vs Issue。

---

---

## 6. 核心算法：三层过滤

### L1 — 系统 Bot 身份过滤

**目的**：排除纯自动化流水线产生的事件噪声。

**判定规则**：
- `login.lower()` 存在于 `SYSTEM_BOT_LIST`
- 或 `login` 以 `[bot]` 结尾且不在 `AI_BOT_LIST` 中

**结果**：`ai_score = 0.0`，同时计入 `bot_events`（影响 Bot_Rate）。

### L2 — 确定性 AI Bot 识别

**目的**：识别已知的 AI 代码助手 Bot。

**判定规则**：`login.lower()` 存在于 `AI_BOT_LIST`

**结果**：`ai_score = 1.0`，同时计入 `bot_events`。

**优先级**：L2 在代码中先于 L1 检查（`classify_actor` 中 AI_BOT_LIST 优先），确保像 `copilot[bot]` 这样同时匹配 `[bot]` 后缀的账号被正确归类为 AI Bot 而非 System Bot。

### L3 — LLM 文本风格审计

**触发条件**：ActorKind == HUMAN 且 provider 不为 None 且未被显式 AI 检测命中。

**流程（批量评分）**：
1. 收集所有需要 LLM 评分的 HUMAN 事件文本（排除已被正则检测为显式 AI 的事件）
2. 按 `batch_size=10` 分组
3. 每组调用 `_safe_llm_score_batch()` → `provider.analyze_batch(texts)` → `_call_llm_batch()`
4. 单次 LLM 调用中，多条事件以编号格式拼接（`[1]\n...\n---\n[2]\n...`），每条截断至 800 字符
5. LLM 返回 JSON 数组 `[{"score": ..., "reason": "..."}, ...]`
6. `_parse_batch_response` 解析数组（含正则 fallback）
7. **仅对 Commit**：减去 `_rebase_penalty`（0 或 0.3），结果 clamp 到 ≥ 0
8. `reason` 写入 `EventRecord.reason`，贯穿整条数据管道至 UI 展示

**PR/Issue 的 Review/Comment 上下文**：
- Reviews 和 issue comments 的文本片段作为上下文附加到父 PR/Issue 的评分文本中（上限 500 字符）
- 这些内容帮助 LLM 更准确判断 PR/Issue 的 AI 参与度
- Review/comment 的 AI 计数**不使用 LLM**，而是由 L1/L2 纯按作者名判定

**批量评分容错**：
- `_safe_llm_score_batch` 捕获所有异常，批量调用失败时返回全零分数
- **不回退到逐条调用**，避免 Rate Limit 下雪崩效应

**LLM 检测信号**（写在 system prompt 中）：
- 过度礼貌或正式的语气
- 异常规整的结构
- 缺少领域缩写的长难句
- ChatGPT / Copilot 典型的模板短语
- PR 描述：**保守评分**，结构化技术写作不视为 AI，除非有明确 ChatGPT 风格用语
- Commit 消息：含 AI assistance trailer（如 `Assisted-by: Gemini`）→ 满分
- 显式声明 AI 协作的描述 → 满分

**容错**：`_safe_llm_score` 捕获所有异常，LLM 调用失败时返回 0.0。

---

## 7. 核心算法：AII 评分公式

### 分维度得分

$$S_{dim} = \frac{1}{N_{dim}} \sum_{i=1}^{N_{dim}} score_i \quad \text{where } dim \in \{commit, pr, issue\}$$

每个维度独立计算所有事件 AI 得分的算术平均。空维度（无事件）得分为 0。

### Bot Rate

$$Bot\_Rate = \frac{N_{system\_bot} + N_{ai\_bot}}{N_{total}}$$

统计所有被 L1 或 L2 命中的事件占比。这个指标作为"环境背景噪声"在 UI 上独立展示。

### AI Involvement Index

$$AII = \bigl(0.5 \times S_{commit} + 0.3 \times S_{pr} + 0.2 \times S_{review} + 0.0 \times S_{issue}\bigr) \times (1 - Bot\_Rate)$$

**权重分配理由**：
- Commit 权重最高 (0.5)：代码提交是最直接的 AI 辅助场景
- PR 次之 (0.3)：PR 描述可能由 AI 生成但代码本身可能是人写的
- Review (0.2)：代码审查中的 AI 参与（基于 L1/L2 作者名判定，`s_review = review_ai / review_total`）
- Issue 不纳入 AII (0.0)：Issue 仅靠 title + body 判断 AI 太武断，误报率高，Issue 分数仅供参考不纳入总分

**乘以 `(1 - Bot_Rate)` 的原因**：如果一个仓库 90% 的事件来自 Bot，其余人类事件即使 AI 分数很高，实际"人类使用 AI"的影响面也被压缩了。

---

## 8. 关键类型与数据结构

### ActorKind (Enum)

```python
class ActorKind(str, Enum):
    SYSTEM_BOT = "system_bot"  # L1 — 系统自动化
    AI_BOT     = "ai_bot"      # L2 — AI 代码助手
    HUMAN      = "human"       # 进入 L3 判定
```

### EventRecord (dataclass)

```python
@dataclass
class EventRecord:
    kind: str           # "commit" | "pr" | "issue"
    title: str          # 截断至 120 字符的标题/首行
    actor: str          # GitHub login
    actor_kind: ActorKind
    ai_score: float     # 0.0 ~ 1.0
    reason: str         # LLM 判定原因（L3 产出）
    url: str            # GitHub 网页链接
    created_at: str     # ISO 8601 日期字符串
    extra: dict         # 预留扩展字段
```

### LLMLogEntry (dataclass)

```python
@dataclass
class LLMLogEntry:
    event_title: str
    event_kind: str
    score: float
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    model: str = ""             # 使用的模型名称
    raw_response: str = ""
    error: str = ""             # 非空表示 LLM 调用失败
```

### AnalysisResult (dataclass)

```python
@dataclass
class AnalysisResult:
    repo_name: str              # "owner/repo"
    events: list[EventRecord]   # 所有已分析事件
    llm_logs: list[LLMLogEntry] # LLM 调用日志
    s_commit: float             # Commit 维度平均分
    s_pr: float                 # PR 维度平均分
    s_review: float             # Review 维度得分（review_ai / review_total）
    s_issue: float              # Issue 维度平均分
    bot_rate: float             # Bot 事件占总事件比例
    aii: float                  # AI Involvement Index (最终指数)
    commit_total: int           # Commit 总数
    commit_ai: int              # AI Commit 数（LLM score ≥ threshold、AI_BOT 或 AI trailer）
    pr_total: int               # PR 总数
    pr_ai: int                  # AI PR 数（LLM score ≥ threshold 或显式 AI 协作标记）
    review_total: int           # PR Review 总数
    review_ai: int              # AI Review 数（L1/L2 作者名判定）
    issue_comment_total: int    # Issue Comment 总数
    issue_comment_ai: int       # AI Issue Comment 数（L1/L2 作者名判定）
```

### SingleItemResult (dataclass)

```python
@dataclass
class SingleItemResult:
    item_type: str              # "pr" | "issue" | "commit"
    item_title: str
    item_url: str
    repo_name: str
    events: list[EventRecord]
    llm_logs: list[LLMLogEntry]
    participants: list[dict]    # [{login, kind, role}]
```

---

## 9. 环境变量与配置

| 变量名 | 来源 | 用途 | 必填 |
|--------|------|------|------|
| `GITHUB_TOKEN` | `.env` 或系统环境 | GitHub API 鉴权；GitHub Models LLM 鉴权 | 推荐 |
| `OPENAI_API_KEY` | `.env` 或系统环境 | OpenAI provider 鉴权 | 仅 OpenAI |
| `LOG_LEVEL` | 系统环境 | 日志控制：默认 INFO（均输出到文件+控制台），`"debug"` = DEBUG，`"off"` = 关闭 | 否 |

> **配置优先级**：环境变量 > `config.toml` > 代码默认值。
> 所有非敏感参数建议写入 `config.toml`，Token/API Key 建议通过环境变量设置。

### 9.2 config.toml 配置项

| 配置路径 | 类型 | 默认值 | 说明 |
|----------|------|--------|------|
| `github.repos` | list[str] | `[]` | 要分析的仓库列表（URL 或 owner/repo） |
| `github.token` | str | `""` | GitHub PAT |
| `llm.provider` | str | `"none"` | LLM Provider: `"none"` / `"openai"` / `"github"` |
| `llm.model` | str | `"gpt-4o-mini"` | 模型标识 |
| `llm.api_key` | str | `""` | OpenAI API Key |
| `llm.base_url` | str | `"https://api.openai.com/v1"` | OpenAI 兼容端点 |
| `llm.concurrency` | int | `30` | L3 阶段 LLM 并发调用数 |
| `analysis.max_items` | int | `50` | 每类事件最大拉取条数（commit 取最新 N 条非 merge，issue/PR 取最近更新的 N 条） |
| `analysis.max_pages` | int | `10` | commit 拉取的最大分页次数（merge commit 较多时需要多页才能凑够 max_items） |
| `analysis.high_risk_threshold` | float | `0.6` | 高风险事件阈值 |
| `analysis.weights.commit` | float | `0.5` | AII 公式中 Commit 权重 |
| `analysis.weights.pr` | float | `0.3` | AII 公式中 PR 权重 |
| `analysis.weights.review` | float | `0.2` | AII 公式中 Review 权重（基于 L1/L2 作者名判定） |
| `analysis.weights.issue` | float | `0.0` | AII 公式中 Issue 权重（不纳入 AII 总分） |
| `bots.system` | list[str] | *(30+ 条内置列表)* | L1 系统 Bot 登录名列表（小写） |
| `bots.ai` | list[str] | *(20+ 条内置列表)* | L2 AI Bot 登录名列表（小写） |
| `database.path` | str | `"ai_radar.db"` | SQLite 数据库文件路径（仅单项分析时使用，待清理） |
| `icons.<owner/repo>` | str | — | 自定义仓库图标 URL（可选，默认使用 GitHub 头像） |

UI 中所有 Token 输入均为 `type="password"`，不在前端明文显示。

---

## 10. 扩展指南

### 10.1 添加新的 LLM Provider

1. 创建 `providers/anthropic_provider.py`：

```python
from providers.base import BaseProvider, LLMCallResult

class AnthropicProvider(BaseProvider):
    def __init__(self, api_key: str | None = None, model: str = "claude-sonnet-4-20250514"):
        ...

    def analyze_text(self, text: str) -> LLMCallResult:
        # 可复用 self._call_llm() 或自定义调用逻辑
        ...
```

2. 在 `providers/__init__.py` 中注册：

```python
from providers.anthropic_provider import AnthropicProvider

def get_provider(name, **kwargs):
    providers = {
        "openai": OpenAIProvider,
        "github": GitHubModelsProvider,
        "anthropic": AnthropicProvider,  # ← 新增
    }
    ...
```

3. 同步更新 `app.py` 侧边栏中的 `st.selectbox` 选项列表。

### 10.2 添加新的 Bot 到列表

直接在 `config.toml` 的 `bots.system` 或 `bots.ai` 列表中追加条目。所有 login 必须为 **小写**。代码中不再硬编码 Bot 列表。

### 10.3 添加新的事件维度

当前 reviews 和 issue comments 不作为独立事件维度，而是通过 L1/L2 作者名匹配统计 AI 计数，内容附加为父事件的 LLM 上下文。如需添加全新维度：

1. 在 `engine/github_api.py` 中添加 fetch 函数
2. 在 `engine/` 下创建新模块，实现 `build_xxx_events()`
3. 在 `engine/analysis.py` 的 `analyze_repo()` 中调用新模块
4. 在 `engine/models.py` 的 `AnalysisResult` 中添加 `s_xxx: float` 字段
5. 修改 AII 公式权重（确保所有权重之和仍为 1.0）
6. 更新 `report/templates/` 中的 Jinja2 模板展示

### 10.4 缓存与持久化

系统使用事件级缓存（`engine/cache.py`）避免跨运行的重复 LLM 调用：

- 缓存文件位于 `reports/cache.json`，结构为 `{repo_name: {event_key: {updated_at, ai_score, reason}}}`
- 每个事件通过稳定键标识（`commit:sha` 或 `pr:number` / `issue:number`）
- 重新分析时，若事件的 `updated_at` 未变更，直接复用缓存的 `ai_score` 和 `reason`，否则重新 LLM 评分
- 通过 `--force` CLI 参数可忽略缓存，强制重新评分所有事件

分析报告通过 JSON 文件持久化到 `reports/` 目录，并通过 `gh-pages` 分支累积存储。

### 10.5 批量 / 异步优化

当前 L3 阶段使用两层优化：

1. **批量 LLM 评分**：多个事件在一次 LLM 调用中评分（`batch_size=10`），大幅减少 API 调用次数（如 openclaw 从 650+ 次降至 ~13 次）
2. **并发执行**：批量任务通过 `concurrent.futures.ThreadPoolExecutor` 并发提交，并发度通过 `config.toml` 的 `llm.concurrency` 配置（默认 30）

GitHub API 访问也使用并发批量获取（`_GH_CONCURRENCY=10`）：
- `fetch_pr_reviews_batch()` / `fetch_issue_comments_batch()` 并发批量获取 reviews/comments

如需进一步优化，可迁移到 `httpx.AsyncClient` + `asyncio.gather`。

### 10.6 CI/CD — GitHub Actions + GitHub Pages

系统支持通过 GitHub Actions 每日自动分析并将结果部署为 GitHub Pages 静态站点。

#### 工作流总览

```
Schedule (每天 08:00 UTC) 或手动触发
         │
         ▼
Checkout main 分支（源代码） + gh-pages 分支（历史数据）
         │
         ▼
report/cli.py → 批量分析 config.toml 中的仓库（实时快照）
         │
         ├── reports/report-YYYY-MM-DD.json  (当日快照 JSON 报告，增量写入 gh-pages)
         ├── reports/latest.json              (最新报告副本)
         └── reports/cache.json               (事件缓存，避免重复 LLM 调用)
         │
         ▼
report/html.py → 基于 Jinja2 模板将所有 JSON 报告渲染为静态 HTML
         │
         ├── site/index.html       (最新报告，带侧边栏导航)
         ├── site/style.css        (共享 CSS 样式文件)
         ├── site/app.js           (共享 JS 脚本)
         ├── site/favicon.svg      (站点图标)
         ├── site/YYYY-MM-DD/      (每日独立报告页面)
         └── site/history.html     (历史报告索引)
         │
         ▼
git commit & push → gh-pages 分支
         │
         ▼
GitHub Pages (从 gh-pages 分支部署) → https://<user>.github.io/<repo>/
```

**分支策略**：历史 JSON 报告和生成的静态站点均持久化在 `gh-pages` 分支上，避免 artifact 过期丢失数据。每次运行增量写入新报告，并从所有历史 JSON 重建整站。

#### 配置步骤

1. **设置 GitHub Secrets**：
   - `GH_PAT`：GitHub Personal Access Token（需 `repo` 权限）
   - `OPENAI_API_KEY`：OpenAI API Key（可选，使用 OpenAI provider 时需要）

2. **启用 GitHub Pages**：
   - 进入 repo Settings → Pages → Source 选择 **Deploy from a branch** → 分支选 `gh-pages` / `/ (root)`

3. **配置要分析的仓库**：
   - 编辑 `config.toml` 中的 `github.repos` 列表

4. **可选：手动触发**：
   - 进入 Actions 标签页 → “Daily AI Intrusion Report” → Run workflow

#### report/cli.py

```
python -m report.cli [--repos owner/repo1 owner/repo2] [--out reports] [--force]
```

- 无 `--repos` 参数时从 `config.toml` 读取仓库列表
- 输出 `reports/report-YYYY-MM-DD.json` 和 `reports/latest.json`
- 自动加载/保存事件缓存（`reports/cache.json`），未变更的事件复用缓存分数
- `--force` 忽略缓存，强制重新评分所有事件
- 支持 `GITHUB_TOKEN` / `OPENAI_API_KEY` 环境变量

#### report/html.py

```
python -m report.html [--input reports] [--out site]
```

- 基于 Jinja2 模板将 `reports/` 目录下所有 JSON 报告渲染为响应式静态 HTML
- **模板架构**：`report/templates/` 包含 `base.html`（页面骨架）、`macros.html`（可复用宏）、`report.html`（主报告页）等 7 个模板
- **静态资源**：`report/static/` 包含 `style.css`、`app.js`、`favicon.svg`，构建时自动复制到 `site/`
- **首页排行榜**：按 AII 降序排列，排名卡片 + 奖牌（金/银/铜），超过 8 个项目时折叠显示
- **项目头像**：自动拉取 GitHub 组织头像，支持 `config.toml [icons]` 自定义
- **趋势折线图**：内联 SVG sparkline（≥5 天数据）+ ECharts 趋势图（≥5 天数据，最多 30 天）
- **移动端适配**：排名卡片、KPI 区域、进度条等均支持移动端响应式布局
- **Reason 列**：事件表格展示 LLM 判定原因
- 每个项目在侧边栏独立展示（按 AI 评分排序），附 AII 评分徽章、头像和 GitHub 链接
- 自动生成每日报告页面和历史索引

#### analyze.py（单项分析 CLI）

```
python analyze.py <GitHub URL>
python analyze.py https://github.com/owner/repo/commit/abc123
python analyze.py https://github.com/owner/repo/pull/42
python analyze.py owner/repo#123
python analyze.py --no-llm <URL>     # 跳过 LLM 分析
```

快速分析单个 PR / Issue / Commit，在终端直接输出结果，包含参与者、事件评分、Reason 和 LLM 调用统计。用于调试和快速检查。

---

## 11. 已知限制与 TODO

| # | 类别 | 描述 | 优先级 |
|---|------|------|--------|
| 1 | API | 仅拉取最近 N 条事件，不支持分页遍历全部历史 | Medium |
| 2 | API | ~~未处理 GitHub API rate limit 429 响应~~ → 已实现 3 次指数退避重试（429/502/503/504/网络超时），429 响应体记录到日志 | ~~Medium~~ Done |
| 3 | LLM | ~~串行调用 LLM~~ → 已改为并发（ThreadPoolExecutor），并发度可配置 | ~~High~~ Done |
| 4 | LLM | ~~单一 system prompt~~ → 已添加 `detect_ai_batch.txt` 批量评分专用 prompt | ~~Low~~ Done |
| 5 | 算法 | ~~AII 权重硬编码~~ → 已移入 config.toml 可自定义 | ~~Low~~ Done |
| 6 | 算法 | ~~L3 仅分析事件本体文本~~ → 单项分析已支持 PR comments/reviews、Issue comments；批量分析中 reviews/comments 作为上下文附加到父事件 | ~~Medium~~ Done |
| 7 | 持久化 | ~~无数据缓存或历史记录~~ → 已通过事件级缓存（`engine/cache.py`）避免重复 LLM 调用；JSON 报告 + gh-pages 分支累积存储历史数据 | ~~Medium~~ Done |
| 8 | 性能 | ~~LLM 调用次数过多~~ → 已实现三项优化：(1) Reviews/comments 不再独立 LLM 调用，AI 计数靠 L1/L2 作者名判定；(2) 批量评分 batch_size=10；(3) 合并 Issues API 扫描 | ~~High~~ Done |
| 9 | 安全 | Token 通过环境变量或 config.toml 配置，无加密存储 | Low |
| 10 | 测试 | 缺少单元测试和集成测试 | High |

---

## 12. 开发约定

### 代码风格
- Python 3.10+ 语法（`X | Y` union types, `match` 等）
- 所有文件顶部 `from __future__ import annotations`（延迟类型求值）
- dataclass 用于数据容器，不用 TypedDict 或 Pydantic（MVP 简洁性）
- `httpx` 用于 GitHub API 请求（同步模式）
- `openai` SDK 用于 LLM 调用（替代手写 httpx 调用）

### 命名
- 模块级常量：`UPPER_SNAKE`
- 类：`PascalCase`
- 函数 / 变量：`snake_case`
- 私有函数前缀 `_`（如 `_gh_get`, `_rebase_penalty`, `_safe_llm_score`）

### 依赖管理
- `requirements.txt` 仅声明直接依赖，由 pip resolver 处理传递依赖
- 不锁版本到 patch level（MVP 阶段灵活性优先）

### Git 忽略
建议 `.gitignore` 中包含：
```
.env
__pycache__/
*.pyc
logs/
site/
reports/
```

---

*文档版本：v0.9 | 最后更新：2026-03-19*
