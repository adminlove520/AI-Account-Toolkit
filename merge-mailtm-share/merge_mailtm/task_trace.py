from __future__ import annotations

import datetime as dt
import json
import uuid
from typing import Any, Dict, List, Optional

from merge_mailtm.shared import trace_now_text, trace_preview


DEFAULT_EMAIL_PROVIDER = "mailtm"


def make_temp_mail_snapshot(account: Any) -> Dict[str, Any]:
    """将临时邮箱账号对象转成可序列化快照。"""
    if not account:
        return {}
    return {
        "email": getattr(account, "email", ""),
        "password": getattr(account, "password", ""),
        "token": getattr(account, "token", ""),
        "provider": getattr(account, "provider", ""),
    }


def append_register_task_event(trace: Dict[str, Any], kind: str, message: str, **extra: Any) -> None:
    """向单次注册任务 trace 中追加事件。"""
    if not isinstance(trace, dict):
        return
    event: Dict[str, Any] = {
        "timestamp": trace_now_text(),
        "kind": str(kind or "event"),
        "message": str(message or ""),
    }
    for key, value in extra.items():
        if value is None or value == "":
            continue
        event[key] = trace_preview(value) if key.endswith("_preview") else value
    events = trace.setdefault("events", [])
    if isinstance(events, list):
        events.append(event)
    trace["last_event_at"] = event["timestamp"]


def build_register_task_trace(
    *,
    worker_id: int = 0,
    run_label: str = "",
    proxy: Optional[str],
    email_provider: str,
    email_base: str,
    email_domains: Optional[List[str]],
    email_api_key: str,
    oauth_issuer: str,
    oauth_client_id: str,
    oauth_redirect_uri: str,
    reused_candidate: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构造单次注册任务的全链路缓存骨架。"""
    candidate = reused_candidate or {}
    return {
        "trace_version": 1,
        "task_id": uuid.uuid4().hex,
        "started_at": trace_now_text(),
        "ended_at": "",
        "elapsed_seconds": 0.0,
        "status": "running",
        "worker_id": worker_id,
        "run_label": run_label,
        "proxy": str(proxy or ""),
        "email_provider": email_provider,
        "email_api_base": email_base,
        "email_domains": [str(item) for item in (email_domains or []) if str(item).strip()],
        "email_api_key_present": bool(str(email_api_key or "").strip()),
        "oauth": {
            "issuer": str(oauth_issuer or "").rstrip("/"),
            "client_id": str(oauth_client_id or "").strip(),
            "redirect_uri": str(oauth_redirect_uri or "").strip(),
        },
        "reused_candidate": bool(candidate),
        "reuse_count": int(candidate.get("reuse_count") or 0) if isinstance(candidate, dict) else 0,
        "reuse_source": candidate if isinstance(candidate, dict) else {},
        "temp_mail_account": {},
        "account_password": "",
        "profile": {"full_name": "", "birthdate": ""},
        "failure_stage": "",
        "failure_detail": "",
        "events": [],
    }


def finalize_register_task_trace(
    trace: Dict[str, Any],
    *,
    status: str,
    failure_stage: str = "",
    failure_detail: str = "",
    token_json: Optional[str] = None,
    temp_mail_account: Any = None,
    account_password: str = "",
    full_name: str = "",
    birthdate: str = "",
) -> Dict[str, Any]:
    """结束单次注册任务 trace，并补齐最终状态。"""
    if not isinstance(trace, dict):
        return {}

    if temp_mail_account:
        trace["temp_mail_account"] = make_temp_mail_snapshot(temp_mail_account)
    if account_password:
        trace["account_password"] = account_password
    profile = trace.setdefault("profile", {})
    if isinstance(profile, dict):
        if full_name:
            profile["full_name"] = full_name
        if birthdate:
            profile["birthdate"] = birthdate

    if token_json is not None:
        try:
            parsed = json.loads(token_json)
            trace["token_payload"] = parsed
        except Exception:
            trace["token_payload_raw"] = token_json

    trace["status"] = status
    trace["failure_stage"] = str(failure_stage or trace.get("failure_stage") or "")
    trace["failure_detail"] = str(failure_detail or trace.get("failure_detail") or "")
    ended_at = trace_now_text()
    trace["ended_at"] = ended_at
    try:
        started_at = dt.datetime.fromisoformat(str(trace.get("started_at") or ended_at))
        finished_at = dt.datetime.fromisoformat(ended_at)
        trace["elapsed_seconds"] = round((finished_at - started_at).total_seconds(), 3)
    except Exception:
        pass
    return trace


def build_reusable_failed_mail_candidate(trace: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """从失败任务 trace 中提取可复用邮箱候选。"""
    if not isinstance(trace, dict):
        return None
    temp_mail = trace.get("temp_mail_account")
    if not isinstance(temp_mail, dict):
        return None

    email = str(temp_mail.get("email") or "").strip()
    temp_password = str(temp_mail.get("password") or "").strip()
    if not email or not temp_password:
        return None

    profile = trace.get("profile")
    if not isinstance(profile, dict):
        profile = {}

    failure_stage = str(trace.get("failure_stage") or "").strip()
    return {
        "candidate_id": str(trace.get("task_id") or uuid.uuid4().hex),
        "email": email,
        "temp_mail_password": temp_password,
        "temp_mail_token": str(temp_mail.get("token") or "").strip(),
        "provider": str(temp_mail.get("provider") or trace.get("email_provider") or DEFAULT_EMAIL_PROVIDER).strip(),
        "email_api_base": str(trace.get("email_api_base") or "").strip(),
        "account_password": str(trace.get("account_password") or "").strip(),
        "full_name": str(profile.get("full_name") or "").strip(),
        "birthdate": str(profile.get("birthdate") or "").strip(),
        "failure_stage": failure_stage,
        "failure_detail": str(trace.get("failure_detail") or "").strip(),
        "task_id": str(trace.get("task_id") or "").strip(),
        "source_run_label": str(trace.get("run_label") or "").strip(),
        "source_trace_file": str(trace.get("trace_file") or "").strip(),
        "last_failure_at": str(trace.get("ended_at") or trace_now_text()),
        "reuse_count": int(trace.get("reuse_count") or 0) + (1 if trace.get("reused_candidate") else 0),
        "oauth_only_hint": failure_stage in {"legacy_oauth", "normalize_token_json", "save_raw_token_json"},
    }

