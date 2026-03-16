# OpenAI Pool Orchestrator

> 自动化 OpenAI 账号注册、Token 管理与多平台账号池维护的 Web 可视化工具。

## 功能概览

| 功能 | 说明 |
|------|------|
| 手动注册 | 通过 Web 界面手动触发 OpenAI 账号注册（临时邮箱 → OAuth → Token） |
| 多邮箱提供商 | 支持 Mail.tm、DuckMail、MoeMail，可轮询/随机/容错切换 |
| 多线程注册 | 支持 1~10 线程并发注册，提高出号效率 |
| Token 管理 | Web 界面查看、删除本地 Token 文件 |
| 多平台同步 | 支持向 Sub2Api 和 CPA 两个平台批量上传 Token |
| 账号池维护 | 探测无效账号、刷新/清理异常账号、手动或自动补号 |
| 实时日志 | 通过 SSE 推送注册过程的实时日志到 Web 前端 |
| 代理支持 | 支持 HTTP/SOCKS5 代理，自动检测 IP 所在地 |

---

## 系统要求

- **Python** >= 3.10
- **操作系统**：Windows / Linux / macOS
- **网络**：需要能访问 OpenAI 的代理（不支持 CN/HK IP）

---

## 安装部署

### 1. 克隆项目

```bash
git clone https://github.com/your-username/openai-pool-orchestrator.git
cd openai-pool-orchestrator
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 初始化配置

```bash
cp config/sync_config.example.json data/sync_config.json
```

> 首次启动时如果 `data/sync_config.json` 不存在，系统会自动使用默认配置创建。

依赖列表：

| 包名 | 最低版本 | 用途 |
|------|---------|------|
| `fastapi` | >= 0.110 | Web 框架 |
| `uvicorn[standard]` | >= 0.27 | ASGI 服务器 |
| `curl-cffi` | >= 0.6 | 带浏览器指纹的 HTTP 客户端（绕过 CF） |
| `aiohttp` | >= 3.9 | 异步 HTTP（账号池探测用） |
| `requests` | >= 2.31 | 同步 HTTP 客户端 |

---

## 快速启动

### Web 模式（推荐）

```bash
# 方式一：快速启动脚本
python run.py

# 方式二：模块方式运行
python -m openai_pool_orchestrator

# 方式三：pip 安装后使用命令
pip install -e .
openai-pool
```

启动后访问 **http://localhost:18421** 即可打开 Web 管理界面。

> **重要**：启动后默认处于空闲状态，不会自动执行任何注册操作。需要在 Web 界面手动配置代理并点击"启动"按钮才会开始注册。

### CLI 模式

```bash
# 单次注册
python run.py --cli --proxy http://127.0.0.1:7897 --once

# 循环注册（默认 5~30 秒间隔）
python run.py --cli --proxy http://127.0.0.1:7897

# 自定义间隔
python run.py --cli --proxy http://127.0.0.1:7897 --sleep-min 10 --sleep-max 60
```

CLI 参数：

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--proxy` | 代理地址 | 无 |
| `--once` | 只执行一次注册 | 否（循环模式） |
| `--sleep-min` | 循环最短等待秒数 | 5 |
| `--sleep-max` | 循环最长等待秒数 | 30 |

---

## 项目结构

```
openai-pool-orchestrator/
├── openai_pool_orchestrator/        # Python 包（源代码）
│   ├── __init__.py                  # 包初始化，定义版本和路径常量
│   ├── __main__.py                  # python -m 入口
│   ├── server.py                    # FastAPI 主服务（REST API + SSE）
│   ├── register.py                  # OpenAI 注册核心逻辑（OAuth + 邮箱验证）
│   ├── pool_maintainer.py           # 账号池维护模块（CPA + Sub2Api）
│   ├── mail_providers.py            # 邮箱提供商抽象层
│   └── static/                      # Web 前端静态文件
│       ├── index.html               # 主页面
│       ├── app.js                   # 前端逻辑
│       └── style.css                # 样式表
├── data/                            # 运行时数据（已 gitignore）
│   ├── sync_config.json             # 运行时配置
│   ├── state.json                   # 统计数据持久化
│   └── tokens/                      # Token 存储目录
│       └── token_xxx_yyy_123456.json
├── config/
│   └── sync_config.example.json     # 配置模板（不含敏感信息）
├── run.py                           # 快速启动脚本
├── pyproject.toml                   # 项目元数据和构建配置
├── requirements.txt                 # Python 依赖
├── .gitignore                       # Git 忽略规则
├── LICENSE                          # MIT 许可证
└── README.md                        # 本文件
```

---

## 配置说明

所有运行时配置保存在 `data/sync_config.json` 中，Web 界面修改配置后会自动持久化。

配置模板在 `config/sync_config.example.json`，首次使用请复制到 `data/` 目录。

### 默认行为

首次启动时，所有自动化功能**默认关闭**：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `auto_register` | `false` | 不自动注册，需手动点击"启动" |
| `auto_sync` | `"false"` | 不自动上传到 Sub2Api |
| `auto_maintain` | `false` | 不自动维护 CPA 池 |
| `sub2api_auto_maintain` | `false` | 不自动维护 Sub2Api 池 |

### 代理配置

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `proxy` | string | HTTP/SOCKS5 代理地址 | `http://127.0.0.1:7897` |

> 注册要求 IP 不在 CN/HK 地区，必须配置可用代理。

### 邮箱提供商配置

支持 3 种临时邮箱提供商：

| 提供商 | 标识名 | 说明 |
|--------|--------|------|
| Mail.tm | `mailtm` | 默认提供商，免费临时邮箱 API |
| DuckMail | `duckmail` | 兼容 Mail.tm API 的替代服务 |
| MoeMail | `moemail` | 需要自部署，带 API Key 认证 |

路由策略：

| 策略 | 标识 | 说明 |
|------|------|------|
| 轮询 | `round_robin` | 按顺序依次使用各提供商（默认） |
| 随机 | `random` | 随机选择一个提供商 |
| 容错 | `failover` | 优先使用失败次数最少的提供商 |

### Sub2Api 平台配置

| 字段 | 类型 | 说明 |
|------|------|------|
| `base_url` | string | Sub2Api 平台地址 |
| `bearer_token` | string | API 访问令牌 |
| `email` | string | 管理员邮箱 |
| `password` | string | 管理员密码 |
| `auto_sync` | string | 注册后是否自动同步（`"true"` / `"false"`） |

### CPA 平台配置

| 字段 | 类型 | 说明 |
|------|------|------|
| `cpa_base_url` | string | CPA 平台地址 |
| `cpa_token` | string | CPA API Bearer Token |
| `min_candidates` | int | 池最低账号数阈值（默认 800） |
| `used_percent_threshold` | int | 使用率超过此值视为无效（默认 95） |

### 上传模式

| 模式 | 标识 | 说明 |
|------|------|------|
| 串行模式 | `snapshot` | 注册成功后先传 CPA，再传 Sub2Api |
| 并行模式 | `decoupled` | 单账号同时上传到两个平台 |

### 多线程配置

| 字段 | 类型 | 说明 |
|------|------|------|
| `multithread` | bool | 是否启用多线程注册 |
| `thread_count` | int | 并发线程数（1~10，默认 3） |

---

## 使用方式

### Web 模式（推荐）

1. **启动服务**：`python run.py`
2. **打开浏览器**访问 `http://localhost:18421`
3. **配置代理**：在仪表盘左栏填写代理地址 → 点击"检测"→ 点击"保存"
4. **配置邮箱提供商**：在配置中心选择并配置临时邮箱服务
5. **手动注册**：在仪表盘点击 "▶ 启动" 按钮开始注册，实时查看日志
6. **停止注册**：点击 "⏹ 停止" 按钮随时中断
7. **查看 Token**：在右侧 Token 列表查看已注册的账号
8. **同步上传**（可选）：手动批量导入到 Sub2Api 或 CPA 平台

---

## API 接口文档

服务运行在 `http://localhost:18421`，以下为主要 API 端点：

### 注册控制

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/start` | 开始注册流程 |
| `POST` | `/api/stop` | 停止注册流程 |
| `GET` | `/api/status` | 获取当前注册状态 |
| `GET` | `/api/logs` | SSE 实时日志流 |

### 代理管理

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/proxy` | 获取当前代理配置 |
| `POST` | `/api/proxy/save` | 保存代理配置 |
| `POST` | `/api/check-proxy` | 测试代理连通性 |

### Token 管理

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/tokens` | 获取所有已注册的 Token 列表 |
| `DELETE` | `/api/tokens/{filename}` | 删除指定 Token 文件 |

### 平台同步

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/sync-config` | 获取同步配置 |
| `POST` | `/api/sync-config` | 保存同步配置 |
| `POST` | `/api/sync-now` | 立即同步单个 Token 到平台 |
| `POST` | `/api/sync-batch` | 批量同步 Token 到平台 |
| `POST` | `/api/upload-mode` | 切换上传模式 |

### 邮箱配置

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/mail/config` | 获取邮箱提供商配置 |
| `POST` | `/api/mail/config` | 保存邮箱提供商配置 |
| `POST` | `/api/mail/test` | 测试邮箱提供商连通性 |

### CPA 账号池

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/pool/config` | 获取 CPA 池配置 |
| `POST` | `/api/pool/config` | 保存 CPA 池配置 |
| `GET` | `/api/pool/status` | 获取 CPA 池状态 |
| `POST` | `/api/pool/check` | 测试 CPA 连接 |
| `POST` | `/api/pool/maintain` | 手动触发 CPA 池维护 |
| `POST` | `/api/pool/auto` | 开关 CPA 自动维护 |

### Sub2Api 账号池

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/sub2api/pool/status` | 获取 Sub2Api 池状态 |
| `POST` | `/api/sub2api/pool/check` | 测试 Sub2Api 连接 |
| `POST` | `/api/sub2api/pool/maintain` | 手动触发 Sub2Api 池维护 |

---

## 注册流程

每次注册经过 11 个步骤：

```
 1. 网络环境检查    →  通过 Cloudflare Trace 检测 IP 所在地（排除 CN/HK）
 2. 创建临时邮箱    →  调用邮箱提供商 API 创建一次性邮箱
 3. 生成 OAuth URL  →  构造 OpenAI OAuth 授权链接，获取 Device ID
 4. 获取 Sentinel   →  请求 OpenAI Sentinel Token（反机器人验证）
 5. 提交注册表单    →  使用临时邮箱发起 OpenAI 账号注册
 6. 发送验证码      →  触发 OpenAI 发送邮箱 OTP 验证码
 7. 轮询收验证码    →  轮询邮箱 API 等待验证码（最长约 120 秒）
 8. 提交验证码      →  将 6 位验证码提交给 OpenAI 完成验证
 9. 创建账户信息    →  填写用户名和生日完成账户创建
10. 解析 Workspace  →  从授权 Cookie 中提取 Workspace ID
11. 获取 Token      →  跟踪 OAuth 重定向链，换取最终 Access/Refresh Token
```

注册成功后，Token 以 JSON 文件形式保存到 `data/tokens/` 目录：

```json
{
  "id_token": "...",
  "access_token": "...",
  "refresh_token": "...",
  "account_id": "...",
  "email": "xxx@domain.com",
  "type": "codex",
  "expired": "2026-03-02T12:00:00Z",
  "last_refresh": "2026-03-02T11:00:00Z"
}
```

---

## 常见问题

### 1. 注册失败：网络环境检查不通过

确保代理配置正确，且代理出口 IP 不在中国大陆或香港地区。

### 2. 临时邮箱创建失败

- 检查邮箱提供商 API 是否可访问
- 尝试切换到其他邮箱提供商（如从 mailtm 切换到 duckmail）
- 使用 Web 界面的"测试连接"功能排查

### 3. 验证码超时未收到

- 邮箱服务可能延迟较大，可稍后重试
- 切换邮箱提供商重试
- 检查代理连通性

### 4. Sentinel Token 获取失败

OpenAI 的反机器人机制可能拦截了请求：
- 更换代理 IP
- 降低注册频率

### 5. Token 保存位置

所有注册成功的 Token 保存在 `data/tokens/` 目录，文件名格式：`token_{email}_{timestamp}.json`

### 6. 端口修改

默认端口 `18421`，修改方式：编辑 `openai_pool_orchestrator/__main__.py` 中 `uvicorn.run()` 的 `port` 参数。

---

## License

[MIT](LICENSE)
