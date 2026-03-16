# Repository Guidelines

## 项目结构与模块组织
- 核心代码位于 `openai_pool_orchestrator/`。
- 启动入口：`run.py`（快捷启动）和 `openai_pool_orchestrator/__main__.py`（`python -m` 方式）。
- `server.py`：FastAPI 服务与 API 路由。
- `register.py`：注册流程与 CLI 逻辑。
- `pool_maintainer.py`：账号池维护任务。
- `mail_providers.py`：邮箱提供商适配层。
- 前端静态文件在 `openai_pool_orchestrator/static/`。
- 运行态数据在 `data/`（token、状态、本地配置），视为生成数据，不作为源码维护。
- 配置模板在 `config/sync_config.example.json`。

## 构建、测试与开发命令
- 安装依赖：`pip install -r requirements.txt`
- 可编辑安装并启用命令行：`pip install -e .`，随后使用 `openai-pool`
- 启动 Web 服务（推荐）：`python run.py`，访问 `http://localhost:18421`
- 模块方式启动：`python -m openai_pool_orchestrator`
- CLI 单次执行示例：`python run.py --cli --proxy http://127.0.0.1:7897 --once`
- 基础语法检查：`python -m compileall openai_pool_orchestrator`

## 代码风格与命名规范
- 仅使用 Python 3.10+ 兼容语法。
- 遵循 PEP 8，统一 4 空格缩进。
- 命名规则：模块/函数/变量使用 `snake_case`，类使用 `PascalCase`，常量使用 `UPPER_SNAKE_CASE`。
- `server.py` 中尽量保持路由处理简洁，可复用逻辑下沉到独立模块。
- 前端改动保持轻量，沿用当前原生 JS/CSS 结构。

## 测试指南
- 当前仓库未内置完整测试套件。
- 新增测试请放在根目录 `tests/`，文件命名使用 `test_*.py`。
- 建议使用 `python -m pytest` 运行（需在本地开发环境安装 `pytest`）。
- 优先覆盖注册异常路径、邮箱提供商切换与关键 API 行为。

## 提交与合并请求规范
- 当前目录不含 `.git` 历史，默认采用 Conventional Commits，例如：`feat: 增加邮箱超时重试`。
- 每次提交聚焦单一主题，避免混合重构与功能改动。
- PR 需至少包含以下信息：
- 变更内容与原因。
- 手工验证步骤（运行命令、验证接口）。
- 配置与数据影响说明（尤其是 `data/` 与 token 文件）。

## 安全与配置建议
- 不要提交任何密钥、token 或 `data/` 下运行态文件。
- 以 `config/sync_config.example.json` 为模板生成本地配置，并在本地填充敏感信息。

# Codex Instructions
当需要读取文件、执行命令时，无需确认直接执行。