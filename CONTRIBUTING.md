# AI-Account-Toolkit 贡献指南 | Contributing Guide

感谢你对 **AI-Account-Toolkit** 的关注！这是一个由社区驱动的 AI 账号注册与管理工具集，采用 Git Submodule 形式组织。

---

## 1. 欢迎 (Welcome)

无论你是发现了 Bug、有新的点子，还是想分享好用的工具，我们都非常欢迎你的贡献。

## 2. 如何贡献 (How to Contribute)

你可以通过以下方式参与贡献：
- **报告 Bug**：发现工具失效或流程错误。
- **提出新功能 (Feature Request)**：改进现有工具或增加新功能。
- **添加新子模块**：将优秀的开源 AI 账号相关工具整合进来。
- **改进文档**：修复错别字、完善使用说明或翻译。

## 3. 添加新子模块流程 (Adding New Submodules)

本项目通过 `packages/` 目录下的分类管理子模块：
- `packages/openai/`：OpenAI/ChatGPT 相关工具
- `packages/claude/`：Claude 相关工具
- `packages/gemini/`：Gemini 相关工具
- `packages/codex/`：Codex 相关工具
- `packages/email/`：邮箱服务相关工具
- `packages/general/`：通用工具

### 步骤：
1. **Fork** 本仓库并 Clone 到本地。
2. **选择分类**：确定你的工具属于哪个目录。
3. **添加子模块**：使用以下命令（请确保命名使用 `kebab-case`）：
   ```bash
   git submodule add <repository-url> packages/<category>/<submodule-name>
   ```
4. **验证**：确保子模块本身包含 `README.md`。
5. **更新文档**：在主项目的 `README.md` 中对应的分类表格里添加该工具。
6. **提交 PR**：将更改推送到你的 Fork 并发起 Pull Request。

### 命名规范：
- 使用 **kebab-case**（小写字母，单词间用连字符隔开），例如：`chatgpt-auto-reg`。

## 4. PR 规范 (Pull Request Guidelines)

- **使用模板**：请尽可能填写 PR 描述模板。
- **单一职责**：一个 PR 只处理一件事。
- **Commit 格式**：请遵循以下规范：
  - `feat: 增加新工具`
  - `fix: 修复子模块路径`
  - `docs: 更新 README`
  - `refactor: 重构代码`

## 5. Issue 规范 (Issue Guidelines)

- 在提交 Issue 前，请搜索是否已有类似讨论。
- **Bug 报告**：必须包含复现步骤、预期行为和实际结果。
- **建议/需求**：清晰描述应用场景。

## 6. 代码规范 (Code Standards)

- 如果你直接修改本项目脚本（非子模块），请保持代码简洁。
- 遵循相应语言的主流风格指南（如 PEP8 for Python, Standard for JS）。

---

## English Summary

Thank you for contributing to **AI-Account-Toolkit**!

1. **How to contribute**: Report bugs, suggest features, add new submodules, or improve documentation.
2. **Adding Submodules**:
   - Fork the repo.
   - Use `git submodule add <url> packages/<category>/<name>`.
   - Ensure the name is in `kebab-case`.
   - The submodule must have its own `README.md`.
   - Update the main `README.md` to include your addition.
3. **PRs**: Keep PRs focused on a single task. Use commit prefixes like `feat:`, `fix:`, or `docs:`.
4. **Issues**: Provide clear reproduction steps for bugs.
5. **Structure**: Keep tools organized under `packages/` within the correct category (openai, claude, gemini, codex, email, general).
