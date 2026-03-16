#!/usr/bin/env python3
"""
快速启动脚本 - OpenAI Pool Orchestrator

用法:
    python run.py              # 启动 Web 服务
    python run.py --cli        # CLI 模式（单次注册）
    python run.py --cli --proxy http://127.0.0.1:7897
"""

import sys
import os

# 将项目根目录加入 Python 路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    if "--cli" in sys.argv:
        # CLI 模式：直接调用注册脚本
        sys.argv.remove("--cli")
        from openai_pool_orchestrator.register import main as cli_main
        cli_main()
    else:
        # Web 模式：启动 FastAPI 服务
        from openai_pool_orchestrator.__main__ import main as web_main
        web_main()


if __name__ == "__main__":
    main()
