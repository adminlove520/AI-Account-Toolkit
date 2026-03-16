# merge-mailtm

账号池自动维护脚本，负责把以下三段流程串起来：

1. 探测并清理管理端中已失效的账号；
2. 使用临时邮箱注册新账号并获取 token；
3. 将生成的账号、密码、token 和日志写入本地文件，必要时再上传到远端。

当前脚本入口为 `auto_pool_maintainer_mailtm.py`。

## 本次新增

最近一轮更新主要补了下面 3 类能力：

1. 清理阶段支持“周限额账号自动停用 / 到期自动恢复”
2. 临时邮箱 provider 新增 `cfmail`
3. 主脚本按职责拆分为多个模块，便于继续维护和扩 provider

当前拆分后的核心模块如下：

- `merge_mailtm/temp_mail.py`
  - 临时邮箱 provider 抽象、路径映射、消息规范化、验证码轮询
- `merge_mailtm/task_trace.py`
  - 注册任务 trace 构建、收尾、失败邮箱复用候选提取
- `merge_mailtm/weekly_limit.py`
  - 周限额字段提取与归并
- `merge_mailtm/reports.py`
  - 周限额 / refresh CSV 与状态文件落盘
- `merge_mailtm/shared.py`
  - 通用配置、日志、时间与 JSON 工具函数

## 运行环境

- Python 版本：建议使用 `>=3.13`
- 建议优先使用项目虚拟环境：`./.venv/bin/python`
- 依赖见 `requirements.txt`

如果你已经在项目目录初始化过虚拟环境，推荐这样安装依赖：

```bash
./.venv/bin/pip install -r requirements.txt
```

如果还没有虚拟环境，可以先执行：

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

## 快速开始

最常用的启动方式：

```bash
./.venv/bin/python auto_pool_maintainer_mailtm.py --config config.json
```

查看帮助：

```bash
./.venv/bin/python auto_pool_maintainer_mailtm.py --help
```

帮助参数如下：

- `--config`：配置文件路径，默认读取 `config.json`
- `--min-candidates`：覆盖配置中的最小候选账号阈值
- `--timeout`：统计 candidates 时接口超时秒数
- `--log-dir`：日志目录，默认是脚本同级目录下的 `logs`

## 配置说明

示例配置见 `config.json`。

当前仓库内的 `config.json` 结构如下：

```json
{
  "clean": {
    "base_url": "http://your-management-host:8318",
    "token": "your-management-token",
    "target_type": "codex",
    "workers": 10,
    "delete_workers": 10,
    "timeout": 10,
    "retries": 1,
    "user_agent": "codex_cli_rs/0.76.0 (Debian 13.0.0; x86_64) WindowsTerminal"
  },
  "email": {
    "provider": "duckmail",
    "api_key": "dk_your_duckmail_bearer",
    "worker_domain": "https://api.duckmail.sbs",
    "email_domains": ["duckmail.your-management-token.de"]
  },
  "maintainer": {
    "min_candidates": 60
  },
  "run": {
    "workers": 1,
    "proxy": "http://127.0.0.1:7897",
    "sleep_min": 5,
    "sleep_max": 30
  },
  "oauth": {
    "issuer": "https://auth.openai.com",
    "client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
    "redirect_uri": "http://localhost:1455/auth/callback",
    "retry_attempts": 3,
    "retry_backoff_base": 2.0,
    "retry_backoff_max": 15.0
  },
  "upload": {
    "base_url": "http://your-management-host:8318",
    "token": "your-management-token"
  },
  "output": {
    "save_local": false,
    "save_accounts_local": true,
    "save_token_file_local": true,
    "reuse_failed_mail": true,
    "reuse_failed_mail_max_attempts": 2,
    "account_dir": "account",
    "accounts_file": "accounts.txt",
    "csv_file": "registered_accounts.csv",
    "details_file": "created_accounts_details.csv",
    "failed_task_dir": "failed_register_tasks",
    "reusable_pool_file": "reusable_failed_accounts.json",
    "weekly_limit_report_file": "weekly_limit_details.csv",
    "weekly_limit_state_file": "weekly_limited_accounts.json",
    "ak_file": "ak.txt",
    "rk_file": "rk.txt"
  }
}
```

### `clean`

用于清理失效账号：

- `base_url`：管理接口地址，例如 `http://127.0.0.1:8317`
- `token`：管理接口 Bearer Token
- `target_type`：目标账号类型，通常为 `codex`
- `workers`：账号状态探测并发数，用于 `401` 与周限额检查
- `delete_workers`：删除并发数
- `timeout`：接口超时时间
- `retries`：重试次数
- `user_agent`：管理接口请求头中的 User-Agent

当前清理阶段的实际顺序是：

1. 拉取管理端账号列表
2. 先检查已停用周限额账号是否已到恢复时间，若已到则自动启用
3. 对当前可用账号并发探测：
   - 是否 `401`
   - 是否周限额已用尽
4. 对新命中的周限额账号调用状态更新接口停用
5. 对真实 `401` 账号再执行 refresh / 删除流程

### `email`

用于临时邮箱提供商配置：

- `worker_domain`：临时邮箱 API 地址，如 `https://api.mail.tm`
- `provider`：可选，默认 `mailtm`，也支持 `duckmail`、`cfmail`
- `api_key`
  - `duckmail` 下表示 DuckMail 官方 API Key
  - `cfmail` 下可兼容填写站点密码 `x-custom-auth`
- `site_password`
  - 仅 `cfmail` 使用，语义上等同于站点密码 `x-custom-auth`
  - 若同时配置 `site_password` 与 `api_key`，优先使用 `site_password`
- `custom_auth`
  - `cfmail` 的 `site_password` 别名，方便兼容不同命名习惯
- `email_domains`：可选域名白名单，空数组表示使用服务端返回的可用域名

其中 `cfmail` provider 和 Mail.tm / DuckMail 有两个关键差异：

1. 会先探测 `GET /open_api/settings` 获取域名和站点能力
2. 创建邮箱走 `POST /api/new_address`，收件箱读取走 `GET /api/mails` / `GET /api/mail/:id`

典型 `cfmail` 配置示例：

```json
{
  "email": {
    "provider": "cfmail",
    "worker_domain": "https://your-worker.example.com",
    "site_password": "your-x-custom-auth-if-needed",
    "email_domains": ["example.com"]
  }
}
```

#### 三种邮件提供方总览

| provider | 默认 `worker_domain` | 典型用途 | 额外鉴权 | 推荐配置键 |
| --- | --- | --- | --- | --- |
| `mailtm` | `https://api.mail.tm` | 公共临时邮箱，默认方案 | 无 | `provider`、`email_domains` |
| `duckmail` | `https://api.duckmail.sbs` | DuckMail 域名或需要官方 API Key 的场景 | `Authorization: Bearer <api_key>` | `provider`、`api_key`、`email_domains` |
| `cfmail` | 无默认值，必须显式填写 | 自建 Cloudflare Temp Email Worker | `x-custom-auth`，邮件读取还要邮箱 JWT | `provider`、`worker_domain`、`site_password/custom_auth`、`email_domains` |

共同点：

- 三种 provider 最终都会被统一成同一套内部消息结构，验证码提取主流程不需要额外分支。
- `email_domains` 都是“域名白名单/偏好域名”用途，不填时默认尽量使用服务端返回的可用域名。
- 除非你明确要切 provider，否则保持不写 `provider` 也可以，代码会默认回落到 `mailtm`。

差异点：

- `mailtm` / `duckmail` 属于“先拿域名列表，再本地拼邮箱地址，再创建账号”的模式。
- `cfmail` 属于“先探测站点能力，再请求服务端创建最终地址”的模式，最终邮箱地址以服务端返回的 `address` 为准。
- `cfmail` 若站点启用了地址密码，服务端创建时可能返回一次性明文密码；脚本会尽量缓存它，供失败复用或重新登录取 JWT 时使用。

#### `mailtm` 使用与配置

适用场景：

- 想要最少配置、直接跑默认公共临时邮箱服务。
- 不需要私有域名、站点密码或自建 Worker。

行为说明：

- 若未配置 `email.provider`，脚本默认按 `mailtm` 处理。
- 若未配置 `email.worker_domain`，自动回落到官方地址 `https://api.mail.tm`。
- 创建邮箱时脚本会先拉取域名列表，再从可用域名中随机选择一个。
- 收件箱读取走 Mail.tm 风格接口，脚本会自动轮询消息列表和详情。

推荐最小配置：

```json
{
  "email": {
    "provider": "mailtm",
    "email_domains": []
  }
}
```

常见配置：

```json
{
  "email": {
    "provider": "mailtm",
    "worker_domain": "https://api.mail.tm",
    "email_domains": ["example.com", "example.org"]
  }
}
```

说明：

- `email_domains` 只是优先域名列表；如果这些域名当前不可用，脚本会回退到 provider 返回的其他域名。
- `mailtm` 不需要额外的 `api_key`、`site_password` 或 `custom_auth`。

#### `duckmail` 使用与配置

适用场景：

- 你在使用 DuckMail 服务。
- 需要指定 DuckMail 的域名，或者服务端要求带官方 API Key 访问。

行为说明：

- 若未配置 `email.worker_domain`，自动回落到 `https://api.duckmail.sbs`。
- 推荐显式配置 `email.api_key`；脚本会把它作为 DuckMail 请求的 Bearer 凭据使用。
- 旧配置里如果没有 `api_key`，代码仍会兼容回退到 `email.admin_password`，但这只是兼容逻辑，不建议继续依赖。
- 创建、取 token、拉列表、读详情都已走 DuckMail 兼容适配层。

推荐配置：

```json
{
  "email": {
    "provider": "duckmail",
    "worker_domain": "https://api.duckmail.sbs",
    "api_key": "dk_your_api_key",
    "email_domains": ["duckmail.example.com"]
  }
}
```

说明：

- `api_key` 推荐始终显式填写，尤其是私有域名或官方要求鉴权的环境。
- `email_domains` 建议只填你确认当前服务端已启用的 DuckMail 域名。

#### `cfmail` 使用与配置

适用场景：

- 你有自己的 Cloudflare Temp Email Worker。
- 你希望控制域名、访问密码、邮箱能力或后端部署方式。

行为说明：

- `cfmail` 必须显式配置 `email.worker_domain`，因为它没有内置默认服务地址。
- `email.worker_domain` 推荐直接填写 Worker 后端 API 域名；如果误填了独立部署的 Pages 前端域名，代码会尝试从前端打包资源里的 `VITE_API_BASE` 自动反推真实后端地址，但仍建议优先填写后端 Worker 域名。
- 脚本会先请求 `GET /open_api/settings` 探测站点是否私有、可用域名、是否允许匿名创建、是否启用地址密码。
- 创建邮箱走 `POST /api/new_address`。
- 收件箱列表走 `GET /api/mails?limit=100&offset=0`，详情走 `GET /api/mail/:id`。
- 详情接口返回的 `raw` 邮件原文会被脚本解析为 `subject`、`from`、`text`、`html`，再进入统一验证码提取逻辑。

私有站点配置：

```json
{
  "email": {
    "provider": "cfmail",
    "worker_domain": "https://mail-worker.example.com",
    "site_password": "your-x-custom-auth",
    "email_domains": ["example.com"]
  }
}
```

公开站点配置：

```json
{
  "email": {
    "provider": "cfmail",
    "worker_domain": "https://mail-worker.example.com",
    "email_domains": ["example.com"]
  }
}
```

兼容说明：

- `site_password`、`custom_auth`、`api_key` 三者都会被当作 `x-custom-auth` 使用。
- 读取优先级是：`site_password` > `custom_auth` > `api_key`。
- 推荐今后统一写 `site_password`，语义最清晰。

注意事项：

- 如果 Worker 是私有站点而你没有配置 `site_password`，创建邮箱和读取邮件很可能都会失败。
- `cfmail` 的最终邮箱地址不是本地拼出来就一定生效，必须以接口响应返回的 `address` 为准。
- 若后端开启地址密码，脚本会尽量保存创建时返回的地址密码，以便后续重新登录获取 JWT。

### `maintainer`

- `min_candidates`：清理完成后，池子里最少保留多少个可用账号；低于这个值就会进入补号流程

### `run`

- `workers`：补号并发数
- `proxy`：HTTP/HTTPS 代理，例如 `http://127.0.0.1:10808`
- `sleep_min` / `sleep_max`：单线程补号时两次尝试间的随机等待秒数

### `oauth`

OpenAI OAuth 相关参数，通常保持默认即可：

- `issuer`
- `client_id`
- `redirect_uri`
- `retry_attempts`
- `retry_backoff_base`
- `retry_backoff_max`

### `upload`

用于上传 token 文件到远端管理端：

- `base_url`
  - 上传接口基础地址
  - 当前代码会优先读取 `upload.base_url`，未配置时才回退到 `clean.base_url`
- `token`
  - 上传接口 Bearer Token
  - 当前代码会优先读取 `upload.token`，未配置时才回退到 `clean.token`

### `output`

本地输出与上传行为控制：

- `save_local`
  - 是否在本地保存 token 相关文件
  - 开启后会生成 `account/` 和 `output_fixed/`
- `save_accounts_local`
  - 是否在本地保存账号密码信息
  - 默认建议开启
- `save_token_file_local`
  - 是否在本地保存原始 token JSON 文件
- `reuse_failed_mail`
  - 是否启用失败邮箱复用池
  - 开启后会优先复用上次失败但仍可继续尝试的临时邮箱
- `reuse_failed_mail_max_attempts`
  - 单个失败邮箱允许再次复用的最大失败次数
- `account_dir`
  - token JSON 文件统一保存目录
  - 默认值为 `account`
- `accounts_file`
  - 账号密码文本文件，默认 `accounts.txt`
- `csv_file`
  - 账号密码 CSV 文件，默认 `registered_accounts.csv`
- `details_file`
  - 账号创建明细 CSV，默认 `created_accounts_details.csv`
- `failed_task_dir`
  - 失败注册任务的全链路 JSON 缓存目录，默认 `failed_register_tasks`
- `reusable_pool_file`
  - 失败邮箱复用池文件，默认 `reusable_failed_accounts.json`
- `weekly_limit_report_file`
  - 周限额停用/恢复操作明细文件，默认 `weekly_limit_details.csv`
- `weekly_limit_state_file`
  - 周限额本地状态文件，默认 `weekly_limited_accounts.json`
- `ak_file`
  - access token 列表文件
- `rk_file`
  - refresh token 列表文件

## 打包执行文件

仓库已自带 macOS / Windows 打包脚本：

- macOS：`PyInstaller --onedir --console`
- Windows：`PyInstaller --onefile --console`

- macOS

```bash
bash packaging/build_macos.sh
```

- Windows

```powershell
powershell -ExecutionPolicy Bypass -File .\packaging\build_windows.ps1
```

打包完成后，产物默认位于：

- `dist/merge-mailtm/merge-mailtm`
- `dist/merge-mailtm.exe`

打包脚本会自动：

1. 创建独立构建虚拟环境
2. 安装 `requirements.txt` 与 `pyinstaller`
3. 带上 `chatgpt_register_old` 的 hidden import
4. 收集 `curl_cffi` 运行时资源
5. macOS 额外复制 `packaging/run_macos.sh` 与可双击的 `merge-mailtm.command` 到产物目录
6. 若仓库根目录存在 `config.json`
   - macOS：复制到 `dist/merge-mailtm/config.json`
   - Windows：复制到 `dist/config.json`，供 `dist/merge-mailtm.exe` 与 `dist/merge-mailtm.cmd` 默认读取
7. 自动创建日志目录
   - macOS：`dist/merge-mailtm/logs`
   - Windows：`dist/logs`
8. Windows 打包前会自动删除旧的 `dist/merge-mailtm/` 目录模式残留，避免误用 `_internal` 依赖布局

产物目录中的启动方式：

- macOS：
  - 命令行运行：`dist/merge-mailtm/run.sh`
  - Finder 双击：`dist/merge-mailtm/merge-mailtm.command`
- Windows：
  - 直接双击：`dist\\merge-mailtm.cmd`
  - 命令行运行：`dist\\merge-mailtm.exe`

Windows 单文件模式说明：

- `merge-mailtm.exe` 会默认读取它所在目录下的 `config.json`
- 默认日志目录为 exe 同目录下的 `logs`
- 若程序以冻结模式运行，本地输出目录也会优先落在 exe 同目录，避免写到临时解包目录或不可预期的当前工作目录
- `merge-mailtm.cmd` 会自动拉起命令行窗口执行，并在结束后保留窗口，方便直接查看输出
- 运行过程中按 `Ctrl+C` 会优雅中断当前任务，不再打印 PyInstaller traceback，退出码为 `130`
- 如果 exe 同目录下没有 `config.json`，程序会直接在控制台输出报错并以退出码 `2` 结束
- 如果你看到 `_internal/python313.dll` 之类错误，说明运行的不是单文件产物，而是旧的目录模式残留；请重新执行 `packaging/build_windows.ps1`

## 对外分享导出

如果你要把项目发给外部同事、朋友或公开存档，推荐不要直接压整个工作目录，而是使用仓库自带的脱敏导出脚本：

```bash
./.venv/bin/python packaging/export_share_zip.py
```

默认会生成：

- `dist/share/merge-mailtm-share.zip`

脚本会自动：

- 排除运行期目录，例如 `logs/`、`account/`、`output_fixed/`、`failed_register_tasks/`、`dist/`
- 排除 IDE 和虚拟环境目录，例如 `.idea/`、`.venv/`
- 把 `config.json` 改写成可分享的占位配置
- 脱敏示例文件中的管理地址、Bearer Token、邮箱站点密钥等信息
- 额外生成 `share_package_manifest.json`，记录本次包含/排除的文件列表

如果你想自定义 zip 文件名：

```bash
./.venv/bin/python packaging/export_share_zip.py --name merge-mailtm-public
```

## Docker 持续运行

如果你的目标是“持续巡检账号池，按固定间隔自动执行清理与补号”，推荐直接使用 Docker，而不是依赖桌面窗口常驻。

仓库已经提供：

- `Dockerfile`
- `docker-compose.yml`
- `docker/entrypoint.sh`

默认行为：

- 容器启动后先执行一轮
- 之后按 `INTERVAL_SECONDS` 间隔循环执行
- 单轮失败后按 `FAILURE_BACKOFF_SECONDS` 退避
- 容器退出会由 `restart: unless-stopped` 自动拉起

默认挂载目录：

- `./config.json` -> `/data/config.json`
- `./logs` -> `/data/logs`
- `./account` -> `/data/account`
- `./output_fixed` -> `/data/output_fixed`
- `./failed_register_tasks` -> `/data/failed_register_tasks`

启动方式：

```bash
docker compose up -d --build
```

如果本机没有 `docker compose` 插件，也可以直接使用原生命令：

```bash
docker build -t merge-mailtm:latest .
docker run -d --name merge-mailtm \
  -e TZ=Asia/Shanghai \
  -e CONFIG_PATH=/data/config.json \
  -e LOG_DIR=/data/logs \
  -e INTERVAL_SECONDS=900 \
  -e STARTUP_DELAY_SECONDS=5 \
  -e JITTER_SECONDS=30 \
  -e FAILURE_BACKOFF_SECONDS=120 \
  -e CONTINUE_ON_ERROR=1 \
  -e RUN_ONCE=0 \
  -v "$(pwd)/config.json:/data/config.json:ro" \
  -v "$(pwd)/logs:/data/logs" \
  -v "$(pwd)/account:/data/account" \
  -v "$(pwd)/output_fixed:/data/output_fixed" \
  -v "$(pwd)/failed_register_tasks:/data/failed_register_tasks" \
  merge-mailtm:latest
```

查看日志：

```bash
docker compose logs -f
```

如果使用上面的原生命令启动：

```bash
docker logs -f merge-mailtm
```

停止：

```bash
docker compose down
```

如果使用上面的原生命令启动：

```bash
docker stop merge-mailtm
docker rm merge-mailtm
```

关键环境变量：

- `INTERVAL_SECONDS`
  - 两次巡检之间的基础间隔秒数
  - 默认 `900`
- `STARTUP_DELAY_SECONDS`
  - 容器启动后第一次执行前的延迟
  - 默认 `5`
- `JITTER_SECONDS`
  - 在基础间隔上追加随机抖动，避免多实例同时打接口
  - 默认 `30`
- `FAILURE_BACKOFF_SECONDS`
  - 单轮失败后的退避等待时间
  - 默认 `120`
- `CONTINUE_ON_ERROR`
  - 单轮失败后是否继续下一轮
  - `1` 表示继续，`0` 表示退出容器
- `RUN_ONCE`
  - 设为 `1` 时容器只执行一轮，适合调试

如果要临时改成 15 分钟跑一次，可以直接改 `docker-compose.yml`：

```yaml
environment:
  INTERVAL_SECONDS: "900"
```

如果只想在容器里调试单轮执行：

```bash
docker compose run --rm -e RUN_ONCE=1 merge-mailtm
```

如果本机没有 `docker compose` 插件，可以改成：

```bash
docker run --rm \
  -e TZ=Asia/Shanghai \
  -e CONFIG_PATH=/data/config.json \
  -e LOG_DIR=/data/logs \
  -e RUN_ONCE=1 \
  -v "$(pwd)/config.json:/data/config.json:ro" \
  -v "$(pwd)/logs:/data/logs" \
  -v "$(pwd)/account:/data/account" \
  -v "$(pwd)/output_fixed:/data/output_fixed" \
  -v "$(pwd)/failed_register_tasks:/data/failed_register_tasks" \
  merge-mailtm:latest
```

## 本地输出文件说明

当 `output.save_accounts_local=true` 时，脚本会在 `output_fixed/` 下写入以下文件：

当 `output.save_local=true` 或 `output.save_token_file_local=true` 时，token JSON 文件会统一写入 `account/` 目录。

### `accounts.txt`

纯文本格式，每行一条：

```text
邮箱:密码
```

示例：

```text
demo@example.com:Passw0rd!23
```

### `registered_accounts.csv`

成功生成并完成 token 保存的账号会写入该文件，字段如下：

- `email`
- `password`
- `timestamp`

### `created_accounts_details.csv`

这是新增的账号创建明细文件，既记录成功，也记录中途失败的账号，便于排查问题。字段如下：

- `timestamp`
- `worker_id`
- `email`
- `password`
- `temp_mail_password`
- `provider`
- `email_api_base`
- `status`
- `failure_stage`
- `error_detail`
- `elapsed_seconds`

其中：

- `status=success` 表示全流程成功
- `status=flow_failed` 表示流程中途失败
- `status=token_save_failed` 表示 token 已拿到但落盘失败
- `status=skipped_target_reached` 表示并发场景下目标已达成，该账号被跳过

`failure_stage` 会尽量记录失败发生在哪个阶段，例如：

- `network_check`
- `create_temp_mail`
- `sentinel_request`
- `wait_email_otp`
- `create_openai_account`
- `workspace_select`

### `failed_register_tasks/`

这是每个失败注册任务的全链路缓存目录。只有失败任务会写入，成功任务不会生成这里的 JSON。

单个 JSON 里会尽量保存：

- 任务基础信息：`task_id`、`worker_id`、`run_label`
- 临时邮箱信息：`email`、`temp_mail_password`、`temp_mail_token`
- ChatGPT 账号资料：`account_password`、`full_name`、`birthdate`
- 失败位置：`failure_stage`、`failure_detail`
- 全链路事件：步骤日志、旧方案 HTTP 日志、关键响应预览
- 若流程已拿到 token，但后续本地落盘失败，也会把 `token_payload` 一并写入这里

### `reusable_failed_accounts.json`

这是失败邮箱复用池。脚本在下一次注册任务开始前，会优先从这里取出候选邮箱：

- 如果上次失败点已经接近成功，例如 `legacy_oauth`
  - 会优先跳过注册阶段，直接重试 OAuth
- 如果失败发生在更早阶段
  - 会重新登录临时邮箱，再按旧方案主流程继续尝试

当某个失败邮箱复用失败次数达到 `output.reuse_failed_mail_max_attempts` 时，脚本会自动把它移出复用池，避免无限重试。

### `weekly_limit_details.csv`

这是周限额动作明细文件。脚本在清理阶段会同步校验 `https://chatgpt.com/backend-api/wham/usage`：

- 已有周限额状态会先从远端 `status_message` 读取
- 当前运行中的实时限额状态会从 `wham/usage` 响应体读取
- 如果周限额已用尽
  - 记录账号、恢复时间、限额窗口信息
  - 调用管理端 `PATCH /v0/management/auth-files/status` 将账号设为停用
- 如果下次运行时已过恢复时间
  - 自动重新启用该账号
  - 同样写入明细文件

字段包括：

- `name`
- `email`
- `account_id`
- `auth_index`
- `action`
- `disabled_before`
- `disabled_after`
- `limit_source`
- `limit_scope`
- `plan_type`
- `used_percent`
- `limit_window_seconds`
- `reset_after_seconds`
- `reset_at`
- `reset_at_text`
- `status`
- `status_message`
- `error_detail`

常见 `action` 取值包括：

- `disable_known_weekly_limit`
  - 根据远端 `status_message` 识别到该账号已处于周限额状态，本次将其停用
- `disable_after_weekly_limit_probe`
  - 本次实时探测 `wham/usage` 命中周限额后，将其停用
- `reenable_after_weekly_reset`
  - 到达恢复时间后，自动重新启用
- 带 `_failed` 后缀的动作
  - 表示本次调用管理端状态更新接口失败

### `weekly_limited_accounts.json`

这是周限额本地状态文件，用来记住哪些账号是因为“周限额已用尽”而被脚本停用的。下一次执行时，脚本会优先读取这个文件，并结合远端 `status_message` / `next_retry_after` 判断：

- 还没到恢复时间：继续保持停用
- 已到恢复时间：自动重新启用

这样即使管理端只保留部分状态信息，脚本也能稳定完成“停用 → 到点恢复”的闭环。

本地状态里会至少保存：

- 账号文件名 `name`
- 邮箱 `email`
- 管理端索引 `auth_index`
- 周限额来源 `source`
- 周限额范围 `scope`
- `reset_at` / `reset_after_seconds`
- 最后更新时间 `updated_at`

## 日志说明

脚本会同时输出控制台日志和文件日志。

默认日志目录：

```text
logs/
```

日志中新增了更详细的步骤信息，例如：

- `步骤1/9: 检查代理出口与网络连通性`
- `步骤2/9: 创建 Mail.tm 临时邮箱`
- `步骤6/9: 发送并等待邮箱验证码`
- `步骤9/9: 选择工作区并换取 OAuth Token`
- `周限额步骤1/4: 拉取账号列表并检查已停用账号的恢复时间`
- `周限额步骤2/4: 探测当前可用账号的 401 与周限额状态`
- `周限额步骤3/4: 处理本轮新命中的周限额账号`
- `周限额步骤4/4: 对真实 401 账号执行 refresh/删除流程`

周限额相关日志还会额外输出：

- 命中账号的 `reset_at`
- 停用前后的 `disabled` 状态
- 是从 `status_message` 识别还是从 `wham/usage` 实时探测识别
- 重新启用是否成功

并发运行时，日志会带上类似下面的前缀，方便定位不同 worker：

```text
[worker-2]
```

## 运行后的目录结构示例

当 `save_local=true` 且 `save_accounts_local=true` 时，常见输出如下：

```text
logs/
account/
  xxx@example.com.json
  yyy@example.com.json
output_fixed/
  accounts.txt
  registered_accounts.csv
  created_accounts_details.csv
  failed_register_tasks/
  reusable_failed_accounts.json
  weekly_limit_details.csv
  weekly_limited_accounts.json
  ak.txt
  rk.txt
```

如果 `save_local=false`，则不会生成 `ak.txt`、`rk.txt`，但只要 `save_token_file_local=true`，仍会在 `account/` 下保留 token JSON 文件；只要 `save_accounts_local=true`，仍会生成账号相关文件。

当前仓库自带的示例配置就是：

- `email.provider=duckmail`
- `email.api_key` 已在当前示例配置中填写
- `email.worker_domain=https://api.duckmail.sbs`
- `output.save_local=false`
- `output.save_accounts_local=true`
- `output.save_token_file_local=true`

也就是说，默认会记录账号、密码和创建明细，并把 token JSON 文件统一写入 `account/` 目录；周限额动作会写入 `output_fixed/weekly_limit_details.csv`，周限额状态会写入 `output_fixed/weekly_limited_accounts.json`。

当前注册主流程已切换为 `chatgpt_register_old.py` 中验证过的旧方案，并在现有日志系统中输出更详细的步骤日志。

## 常见建议

- 优先使用 `./.venv/bin/python` 运行，避免系统 Python 缺少依赖
- 如果日志里显示 `loc=CN` 或 `loc=HK`，说明代理出口地区不符合当前脚本逻辑，需先检查代理
- 如果账号创建失败，先看 `logs/*.log`，再结合 `created_accounts_details.csv` 中的 `failure_stage` 定位问题
- 若只想记录账号和密码，不想保存 token 文件，可以设置：
  - `output.save_local=false`
  - `output.save_accounts_local=true`

## 示例命令

使用默认配置运行：

```bash
./.venv/bin/python auto_pool_maintainer_mailtm.py --config config.json
```

强制最小池子数量为 120：

```bash
./.venv/bin/python auto_pool_maintainer_mailtm.py --config config.json --min-candidates 120
```

指定日志目录：

```bash
./.venv/bin/python auto_pool_maintainer_mailtm.py --config config.json --log-dir ./logs_manual
```
