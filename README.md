# AI-Account-Toolkit 注册与管理工具集

## 项目概述

这是一个全面的 ChatGPT 相关工具集合，包含账号注册、团队管理、Codex 账号管理、临时邮箱服务等多种功能。本工具集旨在简化 ChatGPT 相关操作，提高账号管理效率。

## 项目结构

```
chatgpt_register/
├── CPAtools/             # Codex 账号管理工具
├── GPT-team/             # GPT 团队全自动注册工具
├── chatgpt_register_duckmail/ # DuckMail 注册工具
├── codex/                # Codex 相关工具
├── freemail/             # 临时邮箱服务
├── merge-mailtm-share/   # MailTM 邮箱合并工具
├── ob12api/              # OB12 API 服务
└── openai_pool_orchestrator_v5/ # OpenAI 账号池管理工具
```

## 项目导航

### 1. CPAtools - Codex 账号管理工具

**功能**：批量检查和清理失效的 Codex 账号，通过 HTTP 请求验证账号状态，自动删除 401 失效账号。

**主要文件**：
- `clean_codex_accounts.py` - 主脚本，用于检查和删除失效账号
- `config.son` - 配置文件
- `requirements.txt` - 依赖项

**使用指南**：[CPAtools/README.md](CPAtools/README.md)

### 2. GPT-team - 全自动协议注册工具（CF 临时邮箱版）

**功能**：纯 HTTP 协议注册子号，母号自动登录获取 Token，自动拉 Team 邀请，自动 Codex OAuth 授权上传 CPA。

**主要文件**：
- `get_tokens.py` - 获取开卡 Team 的账号信息
- `gpt-team-new.py` - 完整团队管理
- `config.yaml` - 配置文件
- `accounts.txt` - 账号信息存储

**使用指南**：[GPT-team/README.md](GPT-team/README.md)

### 3. chatgpt_register_duckmail

**功能**：使用 DuckMail 进行 ChatGPT 账号注册的工具。

**主要文件**：
- `chatgpt_register.py` - 主注册脚本
- `config.json` - 配置文件

**使用指南**：[chatgpt_register_duckmail/README.md](chatgpt_register_duckmail/README.md)

### 4. codex

**功能**：Codex 相关工具，包含协议密钥生成等功能。

**主要文件**：
- `protocol_keygen.py` - 协议密钥生成工具
- `config.json` - 配置文件

**使用指南**：[codex/README.md](codex/README.md)

### 5. freemail - 临时邮箱服务

**功能**：基于 Cloudflare Worker 的临时邮箱服务，支持邮箱管理、邮件转发等功能。

**主要组件**：
- `src/` - 服务端源代码
- `public/` - 前端静态文件
- `wrangler.toml` - Cloudflare Worker 配置

**使用指南**：[freemail/README.md](freemail/README.md)

### 6. merge-mailtm-share - MailTM 邮箱合并工具

**功能**：合并和管理 MailTM 临时邮箱，支持批量操作和状态管理。

**主要文件**：
- `auto_pool_maintainer_mailtm.py` - 邮箱池维护脚本
- `merge_mailtm/` - 核心功能模块
- `requirements.txt` - 依赖项

**使用指南**：[merge-mailtm-share/README.md](merge-mailtm-share/README.md)

### 7. ob12api - OB12 API 服务

**功能**：提供 OB12 相关的 API 服务，支持账号注册和管理。

**主要文件**：
- `main.py` - 主服务脚本
- `ob1_register/` - OB1 注册相关模块
- `src/` - 服务端源代码

**使用指南**：[ob12api/README.md](ob12api/README.md)

### 8. openai_pool_orchestrator_v5 - OpenAI 账号池管理工具

**功能**：管理 OpenAI 账号池，支持自动注册、维护和使用。

**主要文件**：
- `run.py` - 运行脚本
- `openai_pool_orchestrator/` - 核心功能模块
- `requirements.txt` - 依赖项

**使用指南**：[openai_pool_orchestrator_v5/README.md](openai_pool_orchestrator_v5/README.md)

## 快速开始

### 1. 环境准备

```bash
# 安装基础依赖
pip install -r <项目目录>/requirements.txt

# 或安装所有项目依赖
for dir in */; do
  if [ -f "$dir/requirements.txt" ]; then
    echo "Installing dependencies for $dir"
    pip install -r "$dir/requirements.txt"
  fi
done
```

### 2. 配置设置

1. 根据每个项目的 README 配置相应的配置文件
2. 确保网络连接正常，必要时配置代理
3. 对于需要临时邮箱的项目，确保已部署相应的邮箱服务

### 3. 运行项目

根据每个项目的具体说明运行相应的脚本，例如：

```bash
# 运行 GPT-team 完整流程
python GPT-team/gpt-team-new.py

# 运行 CPAtools 检查 Codex 账号
python CPAtools/clean_codex_accounts.py
```

## 注意事项

1. **安全性**：本工具集涉及账号管理，请注意保护好配置文件中的敏感信息
2. **合规性**：请遵守 OpenAI 等相关服务的使用条款，不要滥用工具
3. **网络环境**：部分功能可能需要稳定的网络环境或代理支持
4. **依赖管理**：不同项目可能有不同的依赖要求，请按需安装
5. **版本兼容**：确保使用兼容的 Python 版本（建议 Python 3.6+）

## 故障排除

### 常见问题

1. **网络错误**：检查网络连接和代理设置
2. **依赖错误**：确保已安装所有必要的依赖包
3. **配置错误**：仔细检查配置文件中的各项参数
4. **API 限制**：注意 API 调用频率，避免触发限制

### 日志和调试

- 大多数工具会在运行过程中输出详细的日志信息
- 对于复杂问题，可以查看项目中的日志文件或启用详细日志模式

## 相关资源

- **Cloudflare Worker**：用于部署临时邮箱服务
- **OpenAI API**：用于与 OpenAI 服务交互
- **MailTM**：临时邮箱服务提供商
- **DuckMail**：临时邮箱服务提供商

## 免责声明

本工具集仅供学习和研究使用，使用本工具产生的一切后果由使用者自行承担。请遵守相关服务的使用条款，不要用于任何违法或不当用途。

---

**更新日期**：2026-03-13
**版本**：1.0.0
