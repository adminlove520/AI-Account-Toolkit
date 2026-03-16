"""
OpenAI Pool Orchestrator
========================
自动化 OpenAI 账号注册、Token 管理与多平台账号池维护工具。
"""

__version__ = "2.0.0"
__author__ = "OpenAI Pool Orchestrator Contributors"

import os
from pathlib import Path

# 项目根目录（包目录的上一级）
PACKAGE_DIR = Path(__file__).parent
PROJECT_ROOT = PACKAGE_DIR.parent

# 运行时数据目录
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

TOKENS_DIR = DATA_DIR / "tokens"
TOKENS_DIR.mkdir(exist_ok=True)

CONFIG_FILE = DATA_DIR / "sync_config.json"
STATE_FILE = DATA_DIR / "state.json"

# 前端静态文件目录
STATIC_DIR = PACKAGE_DIR / "static"
