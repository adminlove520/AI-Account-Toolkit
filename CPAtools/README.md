# CPAtools - Codex 账号管理工具

## 项目介绍

CPAtools 是一个专为管理 Codex 账号设计的工具，主要用于批量检查和清理失效的 Codex 账号。该工具通过 HTTP 请求验证账号状态，并可以自动删除 401 失效的账号，提高账号管理效率。

## 功能特性

- ✅ 批量检查 Codex 账号状态
- ✅ 自动识别 401 失效账号
- ✅ 支持并发操作，提高处理速度
- ✅ 支持从 HAR 文件自动提取配置
- ✅ 支持交互模式和命令行模式
- ✅ 可自定义过滤条件（类型、提供者）
- ✅ 详细的进度和结果报告

## 核心文件

- `clean_codex_accounts.py` - 主脚本，用于检查和删除失效账号
- `config.son` - 配置文件，存储脚本运行参数

## 配置说明

编辑 `config.son` 文件，填入以下信息：

```json
{
  "base_url": "你的CPA地址",
  "cpa_password": "你的CPA密码",
  "target_type": "codex",
  "provider": "",
  "workers": 200,
  "delete_workers": 40,
  "timeout": 10,
  "retries": 1,
  "user_agent": "codex_cli_rs/0.76.0 (Debian 13.0.0; x86_64) WindowsTerminal",
  "chatgpt_account_id": "",
  "output": "invalid_codex_accounts.json"
}
```

**配置参数说明：**

- `base_url` - CPA 服务的基础 URL
- `cpa_password` - CPA 服务的密码（用于生成管理 token）
- `target_type` - 目标账号类型，默认为 "codex"
- `provider` - 账号提供者（可选）
- `workers` - 检测并发数
- `delete_workers` - 删除并发数
- `timeout` - 请求超时时间（秒）
- `retries` - 失败重试次数
- `user_agent` - 请求头中的 User-Agent
- `chatgpt_account_id` - ChatGPT 账号 ID（可选）
- `output` - 失效账号输出文件

## 使用方法

### 交互模式

在命令行中直接运行脚本：

```bash
python clean_codex_accounts.py
```

然后根据提示选择操作：

1. 仅检查 401 并导出
2. 检查 401 并立即删除
3. 直接删除 output 文件中的账号
0. 退出

### 命令行模式

**仅检查模式：**

```bash
python clean_codex_accounts.py --config config.son
```

**检查并删除模式：**

```bash
python clean_codex_accounts.py --config config.son --delete
```

**从 output 文件删除模式：**

```bash
python clean_codex_accounts.py --config config.son --delete-from-output
```

**使用 HAR 文件自动提取配置：**

```bash
python clean_codex_accounts.py --har your_har_file.har
```

## 命令行参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--config` | 配置文件路径 | config.json |
| `--base-url` | CPA 服务基础 URL | 你的CPA地址 |
| `--token` | 管理 token | 环境变量 MGMT_TOKEN |
| `--har` | 从 HAR 文件提取配置 | None |
| `--target-type` | 按账号类型过滤 | codex |
| `--provider` | 按提供者过滤 | None |
| `--workers` | 检测并发数 | 120 |
| `--delete-workers` | 删除并发数 | 20 |
| `--timeout` | 请求超时时间 | 12 |
| `--retries` | 失败重试次数 | 1 |
| `--user-agent` | User-Agent | codex_cli_rs/0.76.0 |
| `--chatgpt-account-id` | ChatGPT 账号 ID | 环境变量 CHATGPT_ACCOUNT_ID |
| `--output` | 失效账号输出文件 | invalid_codex_accounts.json |
| `--delete` | 开启删除模式 | False |
| `--delete-from-output` | 从输出文件删除 | False |
| `--yes` | 删除时跳过确认 | False |

## 依赖项

- Python 3.6+
- requests
- aiohttp（异步请求）

## 安装依赖

```bash
pip install aiohttp requests
```

## 工作流程

1. 加载配置信息（从配置文件、命令行或 HAR 文件）
2. 获取所有认证文件列表
3. 过滤符合条件的账号
4. 并发检查账号状态
5. 识别 401 失效账号
6. 导出失效账号到文件
7. （可选）删除失效账号

## 注意事项

1. 使用前请确保已正确配置 `base_url` 和 `cpa_password`
2. 大规模删除操作前请谨慎确认，建议先执行检查模式
3. 调整并发数以适应你的网络环境，避免请求过多导致服务拒绝
4. 如遇到网络问题，可适当增加 `timeout` 和 `retries` 值

## 输出说明

- 执行过程中会显示详细的进度信息
- 检查完成后会生成 `invalid_codex_accounts.json` 文件，包含所有 401 失效的账号信息
- 删除操作会显示成功和失败的账号数量

## 故障排除

- **缺少管理 token**：请确保在配置文件中设置了 `cpa_password`，或通过 `--token` 参数提供
- **网络错误**：检查网络连接和代理设置，调整 `timeout` 和 `retries` 参数
- **依赖错误**：执行 `pip install aiohttp requests` 安装所需依赖
