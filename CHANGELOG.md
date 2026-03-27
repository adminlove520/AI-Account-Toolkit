# Changelog

All notable changes to this project will be documented in this file.

## [2.1.0] - 2026-03-27

### Added
- tempmail 子模块 (`packages/email/tempmail`) - 自托管临时邮箱服务
- MIT LICENSE 文件
- CONTRIBUTING.md 贡献指南
- GitHub Issue 模板 (Bug 报告 / 功能请求 / 新子模块推荐)
- README 添加 Badges、目录导航、英文摘要、Star History
- 6 个缺失子模块的文档章节 (chatgpt-creator, openai-oauth, claude-key-switch, gemini-balance-do, codex-lb, key-scraper)

### Fixed
- 修复 9 个子模块 `not our ref` 克隆失败问题 (Issue #2)
- 修复 README 中 `any-auto-register` 路径错误
- 修复依赖安装脚本只扫描根目录的问题
- 重命名 PR 模板文件名 (PREQUEST_TEMPLATE → PULL_REQUEST_TEMPLATE)

### Changed
- README 全面重构：按 packages 分类组织，添加子模块表格
- 清理 PR 模板中的无关内容 (EXTRACT_PROMPT 段落)
- 更新 PR Review Workflow labeler 规则匹配项目结构
- 更新 GitHub 仓库描述和 Topics

### Removed
- 移除根目录重复的 `cloudflare_temp_email/` 目录 (已作为子模块存在)
- 移除根目录重复的 `grokregister/` 目录 (已作为子模块存在)
- 移除临时文件 `github_issue_content.md` 和空模板 `SKILL.md`
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - 2026-03-25

### Added

- **GPT_register+duckmail+CPA+autouploadsub2api** - ChatGPT 批量自动注册工具（DuckMail + OAuth + Sub2Api 版）
  - 支持 DuckMail 临时邮箱并发注册
  - 自动获取 OTP 验证码
  - OAuth 登录获取 Token
  - 可选自动上传 Token 到 Sub2Api 平台
  - Web 管理界面（端口 18421）

- **team_all-in-one** - ChatGPT Team 一键注册工具
  - Flask Web 管理界面
  - 支持 GPTMail、NPCMail 多种临时邮箱
  - 多线程批量注册
  - OAuth 自动授权
  - Token 导出功能
  - Sub2Api 平台上传支持

- **Register_GPT_v0** - GPT 注册工具
  - 自动化 GPT 账号注册流程
  - 支持邮箱验证
  - 支持验证码处理
  - 支持代理配置

### 子模块添加

- **OpenAI 相关子模块**
  - **packages/openai/ABCard** (submodule) - ChatGPT Business/Plus 自动开通工具
    - 全自动注册 ChatGPT 账号
    - 开通 Business (5席位 $0) 或 Plus (个人版 $0)
    - Xvfb + Chrome 自动支付，绕过 hCaptcha
    - Web UI (Streamlit) 操作界面
    - 兑换码管控系统
  - **packages/openai/chatgpt-creator** (submodule) - ChatGPT 账号创建工具
  - **packages/openai/openai-oauth** (submodule) - OpenAI OAuth 认证工具

- **Gemini 相关子模块**
  - **packages/gemini/gemini-balance-do** (submodule) - Gemini 余额查询工具

- **Codex 相关子模块**
  - **packages/codex/codex-lb** (submodule) - Codex 负载均衡工具
  - **packages/codex/codex_register** (submodule) - Codex 注册脚本
    - 基于 Python 的 HTTP 自动化脚本
    - 通过 MailAPI 轮询邮箱验证码
    - 注册完成后自动上传到 CPA
    - 支持并发执行和代理管理
  - **packages/codex/codex-register-fix** (submodule) - Codex 注册修复版本
    - 基于 codex-manager 二次开发
    - 修复 OpenAI 授权流程变更导致的注册失败问题
    - 支持 Sentinel PoW Token 生成
    - 提供完整的 OAuth 登录流程

- **Claude 相关子模块**
  - **packages/claude/claude-key-switch** (submodule) - Claude 密钥切换工具

- **邮箱相关子模块**
  - **packages/email/cloudflare_temp_email** (submodule) - Cloudflare 临时邮箱服务
    - 基于 Cloudflare 免费服务构建
    - Rust WASM 邮件解析，高性能
    - AI 邮件识别，自动提取验证码
    - 支持 SMTP/IMAP 代理
    - Telegram Bot 集成
    - 用户管理，支持 OAuth2、Passkey 登录
  - **packages/email/msOauth2api** (submodule) - 微软 OAuth2 邮件取件 API
    - 将微软的 OAuth2 认证取件流程封装成简单的 API
    - 部署在 Vercel 的无服务器平台上
    - 支持 Graph API 取件，速度更快更稳定
    - 自动提取邮件中的 6 位数字验证码
  - **packages/email/Hotmail-Outlook-Create-Account-Register-Auto** (submodule) - Hotmail 账号自动创建工具
    - 高级 Hotmail / Outlook 账号创建和自动化工具
    - 支持验证码绕过、代理轮换、指纹伪装
    - 逼真的人类行为模拟
    - 多线程并发创建账号
  - **packages/email/outlook-auto-register** (submodule) - Outlook 邮箱注册工具集
    - 基于 Outlook 邮箱 OAuth2 认证的批量自动注册工具集
    - 支持多个目标平台共享同一套邮箱接码模块
    - 提供统一的启动入口和配置向导
    - 支持多种代理方式和验证码提取

- **通用工具子模块**
  - **packages/general/any-auto-register** (submodule) - 多平台账号自动注册工具
  - **packages/general/Ultimate-openai-gemini-claude-api-key-scraper** (submodule) - 多平台 API 密钥抓取工具
  - **packages/general/grok-register** (submodule) - x.ai 注册批处理工具
    - 面向 x.ai 注册批处理的一体化项目
    - 提供控制台、注册执行器、WARP 网络出口
    - 支持 grok2api token 落池和运行时环境
    - 内置 warp 网络出口和 grok2api token sink
  - **packages/general/MREGISTER** (submodule) - ChatGPT 注册机 Web UI
    - 基于 FastAPI 的控制台，统一管理注册脚本
    - 提供可持久化、可排队、可下载结果的任务系统
    - 支持通过 API 调用任务接口
    - 内置 chatgpt_register_v2 和 grok-register 脚本
  - **packages/general/cursor-auto-register** (submodule) - 光标设置管理工具
    - 轻松管理光标设置，包括大小和颜色调整
    - 创建和管理光标配置文件
    - 备份和恢复光标设置
    - 适用于 Windows 和 macOS 系统
  - **packages/general/ExaFree** (submodule) - Exa 免费使用工具
    - 提供 Exa 相关服务的免费访问
    - 支持 Exa 功能的使用
    - 简单易用的界面
    - 适用于需要 Exa 服务的用户

### Updated

- 项目结构优化，整合多个注册工具
- 根目录 README 添加新子项目导航
- 所有子模块添加中文 README 文件
- 更新项目文档和说明

## [1.0.0] - 2025-02-18

### Added

- **CPAtools** - Codex 账号管理工具
- **GPT-team** - GPT 团队全自动注册工具
- **chatgpt_register_duckmail** - DuckMail 注册工具
- **codex** - Codex 相关工具
- **freemail** - 临时邮箱服务
- **merge-mailtm-share** - MailTM 邮箱合并工具
- **ob12api** - OB12 API 服务
- **openai_pool_orchestrator_v5** - OpenAI 账号池管理工具
- **openai_pool_orchestrator-V6** - OpenAI 账号池编排器
- **ClashVerge_** - ClashVerge 非港轮询脚本
- **any-auto-register** (submodule) - 多平台账号自动注册工具

---

**Note**: This changelog documents the major additions and changes to the AI-Account-Toolkit project. For detailed changes to individual submodules, please refer to their respective repositories.
