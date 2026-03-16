"""
允许通过 python -m openai_pool_orchestrator 启动服务。
"""

import sys
import os
import threading

import uvicorn

from . import __version__


def main():
    print("=" * 50)
    print(f"  OpenAI Pool Orchestrator v{__version__}")
    print("  访问: http://localhost:18421")
    print("  按 Ctrl+C 可退出")
    print("=" * 50)

    from .server import app

    # Windows 下 uvicorn 会吞掉 SIGINT，用守护线程兜底
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32

        def _ctrl_handler(ctrl_type):
            # CTRL_C_EVENT = 0, CTRL_BREAK_EVENT = 1
            if ctrl_type in (0, 1):
                print("\n正在退出...")
                os._exit(0)
            return False

        HANDLER_ROUTINE = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)
        _handler = HANDLER_ROUTINE(_ctrl_handler)
        kernel32.SetConsoleCtrlHandler(_handler, True)

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=18421,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
