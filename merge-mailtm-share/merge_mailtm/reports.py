from __future__ import annotations

import csv
import json
import os
import time
from typing import Any, Dict

from merge_mailtm.shared import ensure_parent_dir, trace_now_text


REFRESH_RECOVERY_HEADER = [
    "timestamp",
    "name",
    "email",
    "account_id",
    "auth_index",
    "local_token_file",
    "has_local_token",
    "has_refresh_token",
    "refresh_http_status",
    "reprobe_status",
    "action",
    "error_detail",
]


WEEKLY_LIMIT_HEADER = [
    "timestamp",
    "name",
    "email",
    "account_id",
    "auth_index",
    "action",
    "disabled_before",
    "disabled_after",
    "limit_source",
    "limit_scope",
    "plan_type",
    "used_percent",
    "limit_window_seconds",
    "reset_after_seconds",
    "reset_at",
    "reset_at_text",
    "status",
    "status_message",
    "error_detail",
]


def resolve_refresh_report_path(conf: Dict[str, Any]) -> str:
    """返回 401 刷新恢复明细文件路径。"""
    output_cfg = conf.get("output")
    if not isinstance(output_cfg, dict):
        output_cfg = {}
    fixed_out_dir = os.path.join(os.getcwd(), "output_fixed")
    os.makedirs(fixed_out_dir, exist_ok=True)
    value = str(output_cfg.get("refresh_report_file", "refresh_recovery_details.csv") or "refresh_recovery_details.csv")
    return value if os.path.isabs(value) else os.path.join(fixed_out_dir, value)


def append_refresh_report(report_path: str, row: Dict[str, Any]) -> None:
    """将 401 刷新恢复结果写入本地 CSV，便于后续排障。"""
    ensure_parent_dir(report_path)
    exists = os.path.exists(report_path)
    with open(report_path, "a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if not exists:
            writer.writerow(REFRESH_RECOVERY_HEADER)
        writer.writerow(
            [
                time.strftime("%Y-%m-%d %H:%M:%S"),
                row.get("name", ""),
                row.get("email", ""),
                row.get("account_id", ""),
                row.get("auth_index", ""),
                row.get("local_token_file", ""),
                row.get("has_local_token", ""),
                row.get("has_refresh_token", ""),
                row.get("refresh_http_status", ""),
                row.get("reprobe_status", ""),
                row.get("action", ""),
                row.get("error_detail", ""),
            ]
        )


def resolve_weekly_limit_report_path(conf: Dict[str, Any]) -> str:
    """返回周限额操作明细 CSV 路径。"""
    output_cfg = conf.get("output")
    if not isinstance(output_cfg, dict):
        output_cfg = {}
    fixed_out_dir = os.path.join(os.getcwd(), "output_fixed")
    os.makedirs(fixed_out_dir, exist_ok=True)
    value = str(output_cfg.get("weekly_limit_report_file", "weekly_limit_details.csv") or "weekly_limit_details.csv")
    return value if os.path.isabs(value) else os.path.join(fixed_out_dir, value)


def resolve_weekly_limit_state_path(conf: Dict[str, Any]) -> str:
    """返回周限额本地状态文件路径。"""
    output_cfg = conf.get("output")
    if not isinstance(output_cfg, dict):
        output_cfg = {}
    fixed_out_dir = os.path.join(os.getcwd(), "output_fixed")
    os.makedirs(fixed_out_dir, exist_ok=True)
    value = str(output_cfg.get("weekly_limit_state_file", "weekly_limited_accounts.json") or "weekly_limited_accounts.json")
    return value if os.path.isabs(value) else os.path.join(fixed_out_dir, value)


def append_weekly_limit_report(report_path: str, row: Dict[str, Any]) -> None:
    """将周限额停用/恢复动作写入本地 CSV。"""
    ensure_parent_dir(report_path)
    exists = os.path.exists(report_path)
    with open(report_path, "a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if not exists:
            writer.writerow(WEEKLY_LIMIT_HEADER)
        writer.writerow(
            [
                time.strftime("%Y-%m-%d %H:%M:%S"),
                row.get("name", ""),
                row.get("email", ""),
                row.get("account_id", ""),
                row.get("auth_index", ""),
                row.get("action", ""),
                row.get("disabled_before", ""),
                row.get("disabled_after", ""),
                row.get("limit_source", ""),
                row.get("limit_scope", ""),
                row.get("plan_type", ""),
                row.get("used_percent", ""),
                row.get("limit_window_seconds", ""),
                row.get("reset_after_seconds", ""),
                row.get("reset_at", ""),
                row.get("reset_at_text", ""),
                row.get("status", ""),
                row.get("status_message", ""),
                row.get("error_detail", ""),
            ]
        )


def load_weekly_limit_state(path: str) -> Dict[str, Dict[str, Any]]:
    """读取周限额本地状态映射。"""
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return {}
    if isinstance(payload, dict):
        accounts = payload.get("accounts")
        if isinstance(accounts, dict):
            return {str(key): value for key, value in accounts.items() if isinstance(value, dict)}
    return {}


def save_weekly_limit_state(path: str, accounts: Dict[str, Dict[str, Any]]) -> None:
    """写回周限额本地状态映射。"""
    ensure_parent_dir(path)
    payload = {
        "version": 1,
        "updated_at": trace_now_text(),
        "accounts": accounts,
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)

