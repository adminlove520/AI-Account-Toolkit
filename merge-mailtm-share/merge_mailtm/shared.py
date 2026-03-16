from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict


def trace_now_text() -> str:
    """返回当前本地时间的 ISO 文本，便于 trace 文件统一落盘。"""
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def sanitize_trace_component(value: Any) -> str:
    """清洗文件名片段，避免失败 trace 文件名包含非法字符。"""
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    return text.strip("._-") or "unknown"


def trace_preview(value: Any, limit: int = 1600) -> Any:
    """将任意对象转换成适合 trace 的预览文本，避免日志体积失控。"""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        try:
            text = json.dumps(value, ensure_ascii=False)
        except Exception:
            text = str(value)
    else:
        text = str(value)
    if len(text) > limit:
        return text[:limit] + "...(truncated)"
    return text


def parse_epoch_seconds(value: Any) -> int:
    """尽量将秒级时间戳解析为整数，失败时返回 0。"""
    try:
        if value is None or value == "":
            return 0
        return int(float(value))
    except Exception:
        return 0


def parse_iso_datetime_to_epoch(value: Any) -> int:
    """将 ISO 时间字符串转成秒级时间戳，失败时返回 0。"""
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        dt_obj = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return 0
    if dt_obj.tzinfo is None:
        dt_obj = dt_obj.replace(tzinfo=dt.timezone.utc)
    return int(dt_obj.timestamp())


def format_epoch_seconds(value: Any) -> str:
    """将秒级时间戳格式化为本地可读时间文本。"""
    ts = parse_epoch_seconds(value)
    if ts <= 0:
        return ""
    return dt.datetime.fromtimestamp(ts, tz=dt.datetime.now().astimezone().tzinfo).isoformat(timespec="seconds")


def ensure_parent_dir(path: str) -> None:
    """确保目标文件的父目录存在。"""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def resolve_program_dir(current_file: str) -> Path:
    """返回程序运行目录；冻结为可执行文件所在目录，普通脚本为源码所在目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(current_file).resolve().parent


def is_frozen_runtime() -> bool:
    """判断当前是否运行在 PyInstaller 等冻结环境中。"""
    return bool(getattr(sys, "frozen", False))


def safe_json_text(text: str) -> Dict[str, Any]:
    """安全解析 JSON 字符串，失败时统一返回空字典。"""
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def safe_response_json(resp: Any) -> Any:
    """安全解析响应体，避免第三方接口返回非 JSON 时直接抛错。"""
    try:
        return resp.json()
    except Exception:
        return {}


def pick_conf(root: Dict[str, Any], section: str, key: str, *legacy_keys: str, default: Any = None) -> Any:
    """按新键、兼容旧键、再回退顶层的顺序读取配置。"""
    sec = root.get(section)
    if not isinstance(sec, dict):
        sec = {}

    value = sec.get(key)
    if value is None:
        for legacy_key in legacy_keys:
            value = sec.get(legacy_key)
            if value is not None:
                break
    if value is not None:
        return value

    value = root.get(key)
    if value is None:
        for legacy_key in legacy_keys:
            value = root.get(legacy_key)
            if value is not None:
                break
    if value is not None:
        return value
    return default


def parse_boolish(value: Any, default: bool = False) -> bool:
    """统一解析布尔配置，兼容 true/1/on/yes 等写法。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if not text:
        return default
    if text in {"1", "true", "on", "yes", "y"}:
        return True
    if text in {"0", "false", "off", "no", "n"}:
        return False
    return default


def with_log_prefix(log_prefix: str, msg: str) -> str:
    """给日志消息追加前缀。"""
    return f"{log_prefix}{msg}" if log_prefix else msg


def zzz_log_info(logger: logging.Logger, msg: str) -> None:
    """旧流程普通信息日志。"""
    logger.info(msg)


def zzz_log_success(logger: logging.Logger, msg: str) -> None:
    """旧流程成功日志。"""
    logger.info(msg)


def zzz_log_error(logger: logging.Logger, msg: str) -> None:
    """旧流程错误日志。"""
    logger.error(msg)


def zzz_log_error_detail(logger: logging.Logger, msg: str) -> None:
    """旧流程错误详情日志。"""
    logger.error(msg)
