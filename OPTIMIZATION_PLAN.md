# AI-Account-Toolkit 优化方案

> 基于项目现状的全面分析，从**结构治理、文档质量、工程规范、GitHub 运营**四个维度提出优化建议。

---

## 一、项目结构治理（高优先级）

### 1.1 根目录冗余清理

**现状问题**：根目录存在 **19 个目录 + 4 个文件**，其中多个项目与 `packages/` 子模块功能重叠：

| 根目录 | packages/ 子模块 | 关系 |
|--------|------------------|------|
| `cloudflare_temp_email/` | `packages/email/cloudflare_temp_email/` | 完全重复 |
| `grokregister/` | `packages/general/grok-register/` | 功能重复，命名不同 |

**建议**：
- 将根目录下的 `cloudflare_temp_email/`、`grokregister/` 删除或归档到 `archive/` 分支
- 评估其他根目录项目是否也应迁移为子模块（如 `freemail/` 与 `cloudflare_temp_email` 功能类似）
- 目标：根目录只保留**不适合作为子模块的原创核心项目**

### 1.2 子模块分类优化

**现状问题**：`packages/general/` 是个"杂物箱"，`cursor-auto-register` 应该有自己的分类。

**建议调整**：

```
packages/
├── openai/          # OpenAI/ChatGPT 相关
├── claude/          # Claude 相关
├── gemini/          # Gemini 相关
├── codex/           # Codex 相关
├── cursor/          # Cursor 相关（从 general 移出）
├── grok/            # Grok/x.ai 相关（从 general 移出）
├── email/           # 邮箱服务
└── tools/           # 真正的通用工具（key-scraper, ExaFree 等）
```

### 1.3 清理垃圾文件

- `github_issue_content.md` — 临时文件，应删除
- `SKILL.md` — 看起来是模板/遗留文件，确认是否需要

---

## 二、文档质量提升（高优先级）

### 2.1 README.md 结构性问题

#### 问题 1：缺少 6 个子模块的详细说明
以下子模块在 `.gitmodules` 中存在，但 README 中缺少独立的项目导航章节：
1. `packages/openai/chatgpt-creator`
2. `packages/openai/openai-oauth`
3. `packages/gemini/gemini-balance-do`
4. `packages/codex/codex-lb`
5. `packages/claude/claude-key-switch`
6. `packages/general/Ultimate-openai-gemini-claude-api-key-scraper`

#### 问题 2：子模块列表遗漏 tempmail
"快速开始 > 初始化子模块"部分的子模块列表（第 365-382 行）缺少 `packages/email/tempmail/`。

#### 问题 3：快速开始示例路径错误
```bash
# 当前（错误）
python any-auto-register/main.py

# 应改为
python packages/general/any-auto-register/main.py
```

#### 问题 4：依赖安装脚本只扫描根目录
```bash
# 当前：只扫描 */
for dir in */; do

# 应改为：递归扫描包括 packages/
find . -name "requirements.txt" -exec pip install -r {} +
```

### 2.2 添加专业文档元素

| 缺失项 | 建议 |
|--------|------|
| **Table of Contents** | 25+ 个章节必须有目录导航 |
| **LICENSE** | 添加 MIT 或 GPL-3.0 许可证 |
| **CONTRIBUTING.md** | 贡献指南：如何添加新子模块、命名规范等 |
| **Badges** | Stars、License、Last Commit、Submodule Count |
| **英文摘要** | 项目概述加一段英文简介，扩大受众 |

### 2.3 仓库描述和 Topics

**现状**：GitHub 描述为 "百废待兴"，没有设置 Topics。

**建议**：
- 描述改为：`AI 账号注册与管理一站式工具集 | ChatGPT, Claude, Gemini, Codex, Cursor 批量注册、Token 管理、临时邮箱服务`
- 添加 Topics：`chatgpt`, `openai`, `claude`, `gemini`, `account-registration`, `automation`, `temp-email`, `codex`, `ai-tools`

---

## 三、工程规范（中优先级）

### 3.1 修复 PR 模板文件名

**现状**：`.github/PREQUEST_TEMPLATE.md`
**问题**：GitHub 标准命名是 `PULL_REQUEST_TEMPLATE.md`，且 CI workflow 第 23 行引用的也是标准名。当前模板**不会被 GitHub 自动识别**。

**修复**：重命名为 `.github/PULL_REQUEST_TEMPLATE.md`

### 3.2 添加 Issue 模板

创建 `.github/ISSUE_TEMPLATE/` 目录：

```
.github/ISSUE_TEMPLATE/
├── bug_report.yml      # Bug 报告模板
├── feature_request.yml # 功能请求模板
└── new_submodule.yml   # 新子模块请求
```

### 3.3 PR Review Workflow 清理

**现状问题**：
- `EXTRACT_PROMPT` 相关的 checklist 项（PR 模板第 55-67 行）引用了 `src/services/image_stock_extractor.py`，这个文件**在本项目中不存在**。说明模板是从其他项目复制过来的，未做适配。
- Labeler 的规则（feishu、notification、webhook 等）也不匹配本项目。

**建议**：
- 清理 PR 模板中的无关项
- 更新 Labeler 规则，匹配本项目的目录结构（packages/openai、packages/email 等）

### 3.4 命名规范统一

项目中存在混用：
- `openai_pool_orchestrator_v5`（下划线）vs `openai_pool_orchestrator-V6`（连字符 + 大写 V）
- `grokregister`（无分隔符）vs `grok-register`（连字符）

**建议**：统一采用 **连字符小写** 命名（kebab-case），如 `openai-pool-orchestrator-v6`。

---

## 四、GitHub 运营优化（低优先级）

### 4.1 发布 Release

当前 0 个 Release、0 个 Tag。

**建议**：
- 将当前状态打 `v2.0.0` Tag 并创建 Release
- 在 Release Notes 中总结已包含的工具
- 后续新增子模块时发布 minor 版本

### 4.2 启用安全扫描

- 启用 Dependabot alerts
- 启用 Code scanning（免费的 CodeQL）
- 考虑在 CI 中添加 `gitleaks` 扫描敏感信息泄露

### 4.3 添加 Star History 和 Contributor 统计

在 README 底部添加：
```markdown
## Star History
[![Star History Chart](https://api.star-history.com/svg?repos=adminlove520/AI-Account-Toolkit&type=Date)](https://star-history.com/#adminlove520/AI-Account-Toolkit&Date)
```

---

## 五、执行优先级

| 优先级 | 任务 | 预计工作量 |
|--------|------|-----------|
| 🔴 P0 | 修复 README 路径错误和遗漏 | 15 min |
| 🔴 P0 | 删除 `github_issue_content.md` | 1 min |
| 🟠 P1 | 重命名 PR 模板、清理无关内容 | 10 min |
| 🟠 P1 | 添加 LICENSE 文件 | 5 min |
| 🟠 P1 | 补全 6 个子模块的 README 章节 | 20 min |
| 🟡 P2 | 清理根目录重复项目 | 15 min |
| 🟡 P2 | 添加 Issue 模板 | 10 min |
| 🟡 P2 | 更新仓库描述和 Topics | 5 min |
| 🟢 P3 | 创建 CONTRIBUTING.md | 15 min |
| 🟢 P3 | 发布 v2.0.0 Release | 10 min |
| 🟢 P3 | 子模块分类重构 | 30 min |

---

**生成日期**：2026-03-27
