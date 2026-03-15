# 🛸 GitHub AI Radar

**GitHub 上的代码，究竟有多少是 AI 写的？**

GitHub AI Radar 扫描任意 GitHub 仓库，精确告诉你其开发中有多少是 AI 生成的。它通过三层检测管线——系统 Bot 过滤、已知 AI Bot 匹配、LLM 文本风格审计——分析仓库的 Commit、Pull Request 和 Issue，为每个仓库生成 **AI Involvement Index (AII)** 综合指数（0%–100%）。

[English](README.md) | 中文

![License](https://img.shields.io/badge/license-MIT-blue)
![Python](https://img.shields.io/badge/python-3.10%2B-brightgreen)

## 为什么需要它？

Copilot、Cursor、Codex 等 AI 编程助手正以前所未有的速度重塑开源生态。但 AI 的参与到底有多深？

GitHub AI Radar 用数据而非猜测给你答案。

- **追踪 AI 参与度** — 看着热门开源项目的 AI 使用率每天变化
- **每日自动报告** — 发布到 GitHub Pages，零维护
- **分享排名** — 一键生成精美排名图片 + 二维码
- **零配置部署** — 一个 GitHub Action 全自动搞定

---

## 工作原理

```
GitHub 事件 ──→ L1: 系统 Bot 过滤 ──→ L2: AI Bot 匹配 ──→ L3: LLM 审计 ──→ AII 评分
                (dependabot 等)        (copilot[bot] 等)     (文本分析)
```

1. **L1** — 通过用户名过滤系统 Bot（CI/CD、dependabot 等）
2. **L2** — 通过用户名识别已知 AI 编程助手（Copilot、Codex 等）
3. **L3** — 显式模式检测（PR 描述中的 AI 协作声明、Commit 中的 Git trailer 如 `Assisted-by`）+ LLM 文本风格审计
4. **AII** — 汇总 Commit、PR 两个维度的评分，生成 0–1 综合指数（Issue 仅供参考，不纳入总分）

## 功能特性

- 🔍 **三层检测** — 静态规则 + 显式 AI 模式匹配 + LLM 文本审计，准确识别 AI 参与
- 📊 **精美报告站点** — 领奖台式排行榜、趋势图、迷你折线、GitHub 头像、可分享排名图片
- ⚡ **批量 LLM 评分** — 单次 API 调用评分 10 个事件，支持重试和并发，适合大规模仓库
- 🔌 **多 LLM 后端** — 支持 OpenAI、GitHub Models 及任意 OpenAI 兼容端点
- 📦 **事件级缓存** — 跨运行复用未变更事件的评分，避免重复 LLM 调用
- 🛠️ **灵活的 CLI** — 单项分析、批量报告、`--force` 强制重新评分
- 📱 **移动端适配** — 自适应布局，桌面端和移动端均可舒适浏览
- 📸 **图片分享** — 一键生成排名截图 + 二维码，适合社交媒体分享

## 快速开始

### 1. 安装

```bash
pip install -r requirements.txt
```

### 2. 配置

复制并编辑环境变量文件：

```bash
cp .env.example .env
```

| 变量 | 必填 | 说明 |
|------|------|------|
| `GITHUB_TOKEN` | 推荐 | GitHub PAT — 无 Token 时 API 限制为 60 次/h |
| `OPENAI_API_KEY` | 可选 | 使用 OpenAI provider 时必填 |
| `OPENAI_BASE_URL` | 可选 | OpenAI 兼容端点（Azure、GitHub Models 等） |

也可以在 `config.toml` 中配置仓库列表、LLM 参数和 Bot 名单。

### 3. 运行

**分析单个 PR / Issue / Commit：**

```bash
python analyze.py https://github.com/owner/repo/pull/42
python analyze.py owner/repo#123
python analyze.py --no-llm <URL>    # 跳过 LLM，仅使用静态规则
```

**生成批量报告：**

```bash
# 分析 config.toml 中的仓库 → JSON（含事件缓存）
python -m report.cli --out reports

# 强制重新评分所有事件（忽略缓存）
python -m report.cli --force

# 渲染 JSON → 静态 HTML 站点
python -m report.html --input reports --out site

# 本地预览（打开 http://localhost:8000）
python -m http.server 8000 -d site
```

## 通过 GitHub Actions 部署

自动每日分析并发布到 GitHub Pages，零维护：

1. **Fork 本仓库**
2. 在 **Settings → Secrets** 中添加 `GH_PAT`，可选添加 `OPENAI_API_KEY`
3. 在 **Settings → Pages → Source** 选择 **Deploy from a branch** → `gh-pages` / `/ (root)`
4. 编辑 `config.toml` 添加你想追踪的仓库
5. 手动触发或等待每日定时自动运行

报告地址：`https://<user>.github.io/<repo>/`

## 项目结构

```
├── analyze.py              # 单项分析 CLI
├── config.py / config.toml # 配置
├── log.py                  # 日志（LOG_LEVEL 环境变量控制）
├── prompts/                # LLM Prompt 模板
├── providers/              # LLM Provider 抽象层（OpenAI、GitHub Models）
├── engine/                 # 核心分析管线
│   ├── analysis.py         #   主调度器（analyze_repo / analyze_single）
│   ├── github_api.py       #   GitHub REST API（含重试、并发与分页）
│   ├── cache.py            #   事件级缓存（跳过未变更事件）
│   ├── scoring.py          #   LLM 评分（单项 + 批量）
│   ├── commits.py          #   Commit 事件处理（AI trailer 检测 + LLM）
│   ├── pulls.py            #   PR 事件处理（显式 AI 模式检测 + LLM）
│   └── issues.py           #   Issue 评论计数（L1/L2 作者名匹配，无 LLM）
├── report/                 # 报告生成
│   ├── cli.py              #   批量分析 CLI（输出 JSON + 缓存）
│   ├── html.py             #   静态 HTML 站点生成器（Jinja2）
│   ├── templates/          #   Jinja2 HTML 模板
│   │   ├── base.html       #     页面骨架（head、CDN、body）
│   │   ├── report.html     #     主报告页（侧边栏 + 内容区）
│   │   ├── summary.html    #     排行榜（卡片 + 详细表格）
│   │   ├── repo_section.html #   仓库详情（KPI、图表、事件）
│   │   ├── events_page.html #    独立事件详情页
│   │   ├── history.html    #     历史索引页
│   │   └── macros.html     #     可复用组件宏（rank_card、sparkline 等）
│   └── static/             #   静态资源
│       ├── style.css       #     CSS 样式
│       ├── app.js          #     客户端 JavaScript
│       └── favicon.svg     #     网站图标
└── .github/workflows/
    └── daily-report.yml    # GitHub Actions 每日任务 → gh-pages
```

## 许可证

MIT
