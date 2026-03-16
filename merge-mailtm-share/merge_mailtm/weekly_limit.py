from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from merge_mailtm.shared import (
    format_epoch_seconds,
    parse_epoch_seconds,
    parse_iso_datetime_to_epoch,
    safe_json_text,
)


def decode_management_body(payload: Any) -> Dict[str, Any]:
    """解析 management api-call 返回中的 body 字段。"""
    if isinstance(payload, dict):
        body = payload.get("body")
    else:
        body = payload
    if isinstance(body, dict):
        return body
    if isinstance(body, str):
        return safe_json_text(body)
    return {}


def extract_weekly_limit_from_usage_body(payload: Any) -> Dict[str, Any]:
    """从 wham/usage 响应中提取周限额信息。"""
    result = {
        "weekly_limit_reached": False,
        "weekly_limit_source": "",
        "weekly_limit_scope": "",
        "weekly_plan_type": "",
        "weekly_used_percent": 0,
        "weekly_limit_window_seconds": 0,
        "weekly_reset_after_seconds": 0,
        "weekly_reset_at": 0,
        "weekly_reset_at_text": "",
        "weekly_allowed": None,
    }
    if not isinstance(payload, dict):
        return result

    result["weekly_plan_type"] = str(payload.get("plan_type") or "").strip()
    sections: List[Tuple[str, Any]] = []
    if isinstance(payload.get("rate_limit"), dict):
        sections.append(("rate_limit", payload.get("rate_limit")))
    if isinstance(payload.get("code_review_rate_limit"), dict):
        sections.append(("code_review_rate_limit", payload.get("code_review_rate_limit")))

    extra = payload.get("additional_rate_limits")
    if isinstance(extra, dict):
        for key, value in extra.items():
            if isinstance(value, dict):
                sections.append((f"additional_rate_limits.{key}", value))
    elif isinstance(extra, list):
        for index, value in enumerate(extra):
            if isinstance(value, dict):
                sections.append((f"additional_rate_limits[{index}]", value))

    for scope, section in sections:
        if not isinstance(section, dict):
            continue
        allowed = section.get("allowed")
        limit_reached = bool(section.get("limit_reached"))
        for window_name in ("primary_window", "secondary_window"):
            window = section.get(window_name)
            if not isinstance(window, dict):
                continue
            limit_window_seconds = parse_epoch_seconds(window.get("limit_window_seconds"))
            reset_after_seconds = parse_epoch_seconds(window.get("reset_after_seconds"))
            reset_at = parse_epoch_seconds(window.get("reset_at"))
            used_percent = parse_epoch_seconds(window.get("used_percent"))
            is_weekly_window = limit_window_seconds >= 6 * 24 * 3600
            if not is_weekly_window:
                continue
            if not limit_reached and allowed is not False:
                continue

            result.update(
                {
                    "weekly_limit_reached": True,
                    "weekly_limit_source": "wham_usage",
                    "weekly_limit_scope": f"{scope}.{window_name}",
                    "weekly_used_percent": used_percent,
                    "weekly_limit_window_seconds": limit_window_seconds,
                    "weekly_reset_after_seconds": reset_after_seconds,
                    "weekly_reset_at": reset_at,
                    "weekly_reset_at_text": format_epoch_seconds(reset_at),
                    "weekly_allowed": allowed,
                }
            )
            return result

    return result


def extract_weekly_limit_from_status_message(status_message: Any, next_retry_after: Any = "") -> Dict[str, Any]:
    """从 auth-file 的 status_message / next_retry_after 中提取周限额信息。"""
    result = {
        "weekly_limit_reached": False,
        "weekly_limit_source": "",
        "weekly_limit_scope": "",
        "weekly_plan_type": "",
        "weekly_used_percent": 0,
        "weekly_limit_window_seconds": 604800,
        "weekly_reset_after_seconds": 0,
        "weekly_reset_at": 0,
        "weekly_reset_at_text": "",
        "weekly_allowed": False,
    }
    payload = safe_json_text(str(status_message or "").strip())
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict) and str(error.get("type") or "").strip() == "usage_limit_reached":
        reset_at = parse_epoch_seconds(error.get("resets_at"))
        if reset_at <= 0:
            reset_at = parse_iso_datetime_to_epoch(next_retry_after)
        reset_after_seconds = parse_epoch_seconds(error.get("resets_in_seconds"))
        result.update(
            {
                "weekly_limit_reached": True,
                "weekly_limit_source": "status_message",
                "weekly_limit_scope": "status_message.error",
                "weekly_plan_type": str(error.get("plan_type") or "").strip(),
                "weekly_reset_after_seconds": reset_after_seconds,
                "weekly_reset_at": reset_at,
                "weekly_reset_at_text": format_epoch_seconds(reset_at),
            }
        )
        return result

    return result


def merge_weekly_limit_info(item: Dict[str, Any], state_entry: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """合并远端 auth-file 与本地状态文件中的周限额信息。"""
    info = extract_weekly_limit_from_status_message(item.get("status_message"), item.get("next_retry_after"))
    if info.get("weekly_limit_reached"):
        return info

    entry = state_entry if isinstance(state_entry, dict) else {}
    reset_at = parse_epoch_seconds(entry.get("reset_at"))
    if reset_at <= 0:
        return info
    info.update(
        {
            "weekly_limit_reached": True,
            "weekly_limit_source": str(entry.get("source") or "local_state"),
            "weekly_limit_scope": str(entry.get("scope") or "local_state"),
            "weekly_plan_type": str(entry.get("plan_type") or ""),
            "weekly_used_percent": parse_epoch_seconds(entry.get("used_percent")),
            "weekly_limit_window_seconds": parse_epoch_seconds(entry.get("limit_window_seconds")) or 604800,
            "weekly_reset_after_seconds": max(0, reset_at - int(time.time())),
            "weekly_reset_at": reset_at,
            "weekly_reset_at_text": format_epoch_seconds(reset_at),
            "weekly_allowed": False,
        }
    )
    return info


def is_auth_file_candidate_available(item: Dict[str, Any]) -> bool:
    """判断账号文件当前是否应计入可用候选。"""
    if bool(item.get("disabled")):
        return False
    weekly_info = extract_weekly_limit_from_status_message(item.get("status_message"), item.get("next_retry_after"))
    if weekly_info.get("weekly_limit_reached"):
        reset_at = parse_epoch_seconds(weekly_info.get("weekly_reset_at"))
        if reset_at <= 0 or reset_at > int(time.time()):
            return False
    return True

