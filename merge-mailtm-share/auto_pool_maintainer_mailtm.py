#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""账号池自动维护脚本。

该模块负责三段主流程：
1. 通过管理接口探测并清理失效的账号文件；
2. 通过临时邮箱 + OpenAI OAuth 流程补充新账号；
3. 将成功产出的账号与 token 文件收敛到本地或远端存储。

其中临时邮箱链路默认使用 Mail.tm，当前同时兼容 DuckMail 与 CFMail，并在内部统一域名、
账号创建、令牌获取、收件箱轮询、邮件详情读取等数据结构，尽量不影响现有下游逻辑。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import csv
import datetime as dt
import hashlib
import importlib
import json
import logging
import os
import random
import re
import secrets
import string
import sys
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from merge_mailtm.reports import (
    append_refresh_report,
    append_weekly_limit_report,
    load_weekly_limit_state,
    resolve_refresh_report_path,
    resolve_weekly_limit_report_path,
    resolve_weekly_limit_state_path,
    save_weekly_limit_state,
)
from merge_mailtm.shared import (
    ensure_parent_dir,
    format_epoch_seconds,
    is_frozen_runtime,
    parse_boolish,
    parse_epoch_seconds,
    parse_iso_datetime_to_epoch,
    pick_conf,
    resolve_program_dir,
    safe_json_text,
    safe_response_json,
    sanitize_trace_component,
    trace_now_text,
    trace_preview,
    with_log_prefix,
    zzz_log_error,
    zzz_log_error_detail,
    zzz_log_info,
    zzz_log_success,
)
from merge_mailtm.task_trace import (
    append_register_task_event,
    build_register_task_trace,
    build_reusable_failed_mail_candidate,
    finalize_register_task_trace,
    make_temp_mail_snapshot,
)
from merge_mailtm.temp_mail import (
    DEFAULT_EMAIL_PROVIDER,
    TempMailAccount,
    TempMailConfig,
    build_temp_mail_account_create_payload,
    build_temp_mail_headers,
    build_temp_mail_token_payload,
    create_temp_email,
    default_email_base,
    extract_temp_mail_account_email,
    extract_temp_mail_account_password,
    extract_temp_mail_error,
    extract_temp_mail_message_rows,
    extract_temp_mail_token,
    fetch_email_detail,
    fetch_emails,
    get_email_provider_label,
    get_mailtm_domains,
    get_temp_mail_account_create_path,
    get_temp_mail_domain_path,
    get_temp_mail_message_detail_path,
    get_temp_mail_messages_path,
    get_temp_mail_token_path,
    make_temp_mail_config,
    normalize_email_base,
    normalize_email_provider,
    normalize_mailtm_base,
    normalize_temp_mail_domains,
    normalize_temp_mail_message,
    resolve_temp_mail_config,
    temp_mail_request,
    wait_for_verification_code,
)
from merge_mailtm.weekly_limit import (
    decode_management_body,
    extract_weekly_limit_from_status_message,
    extract_weekly_limit_from_usage_body,
    is_auth_file_candidate_available,
    merge_weekly_limit_info,
)

try:
    from curl_cffi import requests as curl_requests
except Exception:
    curl_requests = None

try:
    import aiohttp
except Exception:
    aiohttp = None


OPENAI_AUTH_BASE = "https://auth.openai.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.0.0 Safari/537.36"
)
DEFAULT_MGMT_UA = "codex_cli_rs/0.76.0 (Debian 13.0.0; x86_64) WindowsTerminal"

COMMON_HEADERS = {
    "accept": "application/json",
    "accept-language": "en-US,en;q=0.9",
    "content-type": "application/json",
    "origin": OPENAI_AUTH_BASE,
    "user-agent": USER_AGENT,
    "sec-ch-ua": '"Google Chrome";v="145", "Not?A_Brand";v="8", "Chromium";v="145"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}
NAVIGATE_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "user-agent": USER_AGENT,
    "sec-ch-ua": '"Google Chrome";v="145", "Not?A_Brand";v="8", "Chromium";v="145"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "same-origin",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
}

@dataclass(frozen=True)
class RegisterAttemptResult:
    """单次补号尝试的输出，兼容成功与中途失败场景。"""

    token_json: Optional[str]
    temp_mail_account: Optional[TempMailAccount]
    account_password: str = ""
    failure_stage: str = ""
    failure_detail: str = ""
    task_trace: Optional[Dict[str, Any]] = None


ACCOUNT_DETAILS_HEADER = [
    "timestamp",
    "worker_id",
    "email",
    "password",
    "temp_mail_password",
    "provider",
    "email_api_base",
    "status",
    "failure_stage",
    "error_detail",
    "elapsed_seconds",
]


class ConsoleColorFormatter(logging.Formatter):
    """为控制台日志增加颜色，仅增强终端显示，不影响文件日志。"""

    RESET = "\033[0m"
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        if not sys.stdout.isatty():
            return text

        # Make progress counters stand out without affecting file logs.
        text = re.sub(r"【成功:\d+】", lambda m: f"{self.GREEN}{m.group(0)}{self.RESET}", text)
        text = re.sub(r"【失败:\d+】", lambda m: f"{self.RED}{m.group(0)}{self.RESET}", text)
        text = re.sub(r"【跳过:\d+】", lambda m: f"{self.YELLOW}{m.group(0)}{self.RESET}", text)
        text = re.sub(r"token \d+/\d+", lambda m: f"{self.CYAN}{m.group(0)}{self.RESET}", text)
        return text


def load_json(path: Path) -> Dict[str, Any]:
    """读取统一配置文件，要求顶层必须是 JSON 对象。"""
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise RuntimeError(f"配置文件格式错误，顶层必须是对象: {path}")
    return data


def setup_logger(log_dir: Path) -> tuple[logging.Logger, Path]:
    """初始化控制台与文件双通道日志，并返回日志对象与日志文件路径。"""
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"pool_maintainer_{ts}.log"

    logger = logging.getLogger("pool_maintainer")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(ConsoleColorFormatter("%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(sh)
    return logger, log_path


def ensure_parent_dir(path: str) -> None:
    """确保目标文件的父目录存在，便于后续写入输出文件。"""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def mgmt_headers(token: str) -> Dict[str, str]:
    """生成管理接口鉴权头。"""
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def get_item_type(item: Dict[str, Any]) -> str:
    """兼容不同字段名，统一提取账号文件类型。"""
    return str(item.get("type") or item.get("typo") or "")


def extract_chatgpt_account_id(item: Dict[str, Any]) -> Optional[str]:
    """兼容多种命名方式，尽量提取 chatgpt_account_id。"""
    for key in ("chatgpt_account_id", "chatgptAccountId", "account_id", "accountId"):
        val = item.get(key)
        if val:
            return str(val)
    return None

def build_standard_token_json(email: str, tokens: Dict[str, Any], previous_data: Optional[Dict[str, Any]] = None) -> str:
    """将 OAuth token 响应统一转换成当前项目使用的标准 token_json 格式。"""
    previous_data = previous_data or {}
    access_token = str(tokens.get("access_token") or previous_data.get("access_token") or "").strip()
    refresh_token = str(tokens.get("refresh_token") or previous_data.get("refresh_token") or "").strip()
    id_token = str(tokens.get("id_token") or previous_data.get("id_token") or "").strip()
    expires_in = zzz_to_int(tokens.get("expires_in"))

    access_payload = decode_jwt_payload(access_token)
    id_claims = zzz_jwt_claims_no_verify(id_token)

    normalized_email = (
        str(email or "").strip()
        or str(previous_data.get("email") or "").strip()
        or str(id_claims.get("email") or "").strip()
    )

    auth_claims = {}
    if isinstance(access_payload.get("https://api.openai.com/auth"), dict):
        auth_claims = access_payload.get("https://api.openai.com/auth") or {}
    elif isinstance(id_claims.get("https://api.openai.com/auth"), dict):
        auth_claims = id_claims.get("https://api.openai.com/auth") or {}
    account_id = str((auth_claims or {}).get("chatgpt_account_id") or previous_data.get("account_id") or "").strip()

    expired_str = ""
    exp_timestamp = zzz_to_int(access_payload.get("exp"))
    if exp_timestamp > 0:
        expired_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(exp_timestamp))
    elif expires_in > 0:
        expired_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(time.time()) + expires_in))

    token_json = {
        "id_token": id_token,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "account_id": account_id,
        "last_refresh": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "email": normalized_email,
        "type": "codex",
        "expired": expired_str,
    }
    return json.dumps(token_json, ensure_ascii=False, separators=(",", ":"))


def run_legacy_register_flow(
    proxy: Optional[str],
    logger: logging.Logger,
    *,
    email_provider: str = DEFAULT_EMAIL_PROVIDER,
    email_base: str = "",
    email_domains: Optional[List[str]] = None,
    email_api_key: str = "",
    oauth_issuer: str = OPENAI_AUTH_BASE,
    oauth_client_id: str = "app_EMoamEEZ73f0CkXaXp7hrann",
    oauth_redirect_uri: str = "http://localhost:1455/auth/callback",
    run_label: str = "",
    worker_id: int = 0,
    reused_candidate: Optional[Dict[str, Any]] = None,
) -> RegisterAttemptResult:
    """使用旧版 chatgpt_register_old.py 的注册/OAuth 方案执行完整流程。"""
    proxies: Any = {"http": proxy, "https": proxy} if proxy else None
    mail_config = make_temp_mail_config(
        provider=email_provider,
        worker_domain=email_base,
        api_key=email_api_key,
    )
    provider_label = get_email_provider_label(mail_config.provider)
    log_prefix = f"[{run_label}] " if run_label else ""
    trace = build_register_task_trace(
        worker_id=worker_id,
        run_label=run_label,
        proxy=proxy,
        email_provider=mail_config.provider,
        email_base=mail_config.base_url,
        email_domains=email_domains,
        email_api_key=mail_config.api_key,
        oauth_issuer=oauth_issuer,
        oauth_client_id=oauth_client_id,
        oauth_redirect_uri=oauth_redirect_uri,
        reused_candidate=reused_candidate,
    )

    def log_step(step_no: int, total_steps: int, message: str) -> None:
        append_register_task_event(trace, "step", message, step_no=step_no, total_steps=total_steps)
        zzz_log_info(logger, with_log_prefix(log_prefix, f"旧方案步骤{step_no}/{total_steps}: {message}"))

    def legacy_print(msg: str) -> None:
        text = str(msg or "").strip()
        if not text:
            return
        lowered = text.lower()
        level = "warning" if any(key in lowered for key in (" fail", "error", "异常", "失败", "超时", "未获取", "未能")) else "info"
        append_register_task_event(trace, "legacy_print", text, level=level)
        if any(key in lowered for key in (" fail", "error", "异常", "失败", "超时", "未获取", "未能")):
            zzz_log_error(logger, with_log_prefix(log_prefix, f"[旧方案] {text}"))
        else:
            zzz_log_info(logger, with_log_prefix(log_prefix, f"[旧方案] {text}"))

    def legacy_http_log(step: str, method: str, url: str, status: Any, body: Any = None) -> None:
        append_register_task_event(
            trace,
            "legacy_http",
            step,
            method=method,
            url=url,
            status=status,
            body_preview=trace_preview(body, limit=2200),
        )
        zzz_log_info(
            logger,
            with_log_prefix(log_prefix, f"[旧方案HTTP] {step} | {method} {url} | status={status}"),
        )
        if body is not None:
            try:
                preview = json.dumps(body, ensure_ascii=False)
            except Exception:
                preview = str(body)
            zzz_log_info(
                logger,
                with_log_prefix(log_prefix, f"[旧方案HTTP] 响应预览: {preview[:600]}"),
            )

    temp_mail_account: Optional[TempMailAccount] = None
    account_password = ""
    full_name = ""
    birthdate = ""

    def failure_result(stage: str, detail: Any) -> RegisterAttemptResult:
        text = str(detail or "")
        trace["failure_stage"] = stage
        trace["failure_detail"] = text
        append_register_task_event(trace, "failure", f"{stage}: {text}", stage=stage)
        final_trace = finalize_register_task_trace(
            trace,
            status="failed",
            failure_stage=stage,
            failure_detail=text,
            temp_mail_account=temp_mail_account,
            account_password=account_password,
            full_name=full_name,
            birthdate=birthdate,
        )
        return RegisterAttemptResult(
            token_json=None,
            temp_mail_account=temp_mail_account,
            account_password=account_password,
            failure_stage=stage,
            failure_detail=text,
            task_trace=final_trace,
        )

    total_steps = 5
    try:
        log_step(1, total_steps, "检查代理出口与网络连通性")
        trace_resp = zzz_http_request("get", "https://cloudflare.com/cdn-cgi/trace", timeout=10, proxies=proxies)
        trace_text = str(getattr(trace_resp, "text", "") or "")
        loc_match = re.search(r"^loc=(.+)$", trace_text, re.MULTILINE)
        loc = loc_match.group(1) if loc_match else None
        append_register_task_event(trace, "network", "代理出口检查完成", location=loc, trace_preview=trace_text[:300])
        zzz_log_info(logger, with_log_prefix(log_prefix, f"当前 IP 所在地: {loc}"))
        if loc in ("CN", "HK"):
            raise RuntimeError("检查代理哦w - 所在地不支持")
    except Exception as e:
        zzz_log_error(logger, with_log_prefix(log_prefix, f"网络连接检查失败: {e}"))
        return failure_result("network_check", e)

    resume_hint = reused_candidate if isinstance(reused_candidate, dict) else {}
    if resume_hint:
        reuse_stage = str(resume_hint.get("failure_stage") or "").strip() or "unknown"
        reuse_count = int(resume_hint.get("reuse_count") or 0)
        append_register_task_event(
            trace,
            "reuse",
            "本次任务优先复用失败邮箱",
            email=resume_hint.get("email"),
            failure_stage=reuse_stage,
            reuse_count=reuse_count,
        )
        log_step(2, total_steps, f"复用失败 {provider_label} 邮箱并恢复邮箱 token")
        temp_mail_account = zzz_restore_temp_mail_account(
            str(resume_hint.get("email") or ""),
            str(resume_hint.get("temp_mail_password") or ""),
            str(resume_hint.get("temp_mail_token") or ""),
            proxies,
            logger,
            provider=mail_config.provider,
            base_url=mail_config.base_url,
            api_key=mail_config.api_key,
            log_prefix=log_prefix,
        )
        if not temp_mail_account:
            return failure_result("reuse_temp_mail", "未能复用失败邮箱或重新获取邮箱 token")
    else:
        log_step(2, total_steps, f"创建 {provider_label} 临时邮箱")
        temp_mail_account = zzz_get_email_and_token(
            proxies,
            logger,
            provider=mail_config.provider,
            base_url=mail_config.base_url,
            email_domains=email_domains,
            api_key=mail_config.api_key,
            log_prefix=log_prefix,
        )
    if not temp_mail_account:
        return failure_result("create_temp_mail", "未能创建临时邮箱或获取邮箱 token")
    trace["temp_mail_account"] = make_temp_mail_snapshot(temp_mail_account)

    try:
        legacy = importlib.import_module("chatgpt_register_old")
    except Exception as e:
        zzz_log_error(logger, with_log_prefix(log_prefix, f"加载旧方案模块失败: {e}"))
        return failure_result("load_legacy_module", e)

    legacy.DUCKMAIL_API_BASE = mail_config.base_url
    legacy.DUCKMAIL_BEARER = str(mail_config.api_key or "").strip()
    legacy.OAUTH_ISSUER = str(oauth_issuer or OPENAI_AUTH_BASE).rstrip("/")
    legacy.OAUTH_CLIENT_ID = str(oauth_client_id or ZZZ_CLIENT_ID).strip()
    legacy.OAUTH_REDIRECT_URI = str(oauth_redirect_uri or ZZZ_DEFAULT_REDIRECT_URI).strip()
    legacy.ENABLE_OAUTH = True
    legacy.OAUTH_REQUIRED = True

    try:
        legacy_reg = legacy.ChatGPTRegister(proxy=proxy, tag=run_label or "legacy")
        legacy_reg._print = legacy_print
        legacy_reg._log = legacy_http_log
        legacy_reg._fetch_emails_duckmail = lambda mail_token: zzz_fetch_temp_mail_messages(
            mail_token,
            proxies,
            logger,
            provider=mail_config.provider,
            base_url=mail_config.base_url,
            api_key=mail_config.api_key,
        )
        legacy_reg._fetch_email_detail_duckmail = lambda mail_token, msg_id: zzz_fetch_temp_mail_detail(
            mail_token,
            msg_id,
            proxies,
            logger,
            provider=mail_config.provider,
            base_url=mail_config.base_url,
            api_key=mail_config.api_key,
        )
    except Exception as e:
        zzz_log_error(logger, with_log_prefix(log_prefix, f"初始化旧方案注册器失败: {e}"))
        return RegisterAttemptResult(
            token_json=None,
            temp_mail_account=temp_mail_account,
            failure_stage="init_legacy_register",
            failure_detail=str(e),
        )

    account_password = legacy._generate_password() if hasattr(legacy, "_generate_password") else generate_random_password()
    full_name = legacy._random_name() if hasattr(legacy, "_random_name") else "Alex Smith"
    birthdate = legacy._random_birthdate() if hasattr(legacy, "_random_birthdate") else "1998-08-08"
    if resume_hint:
        account_password = str(resume_hint.get("account_password") or "").strip() or account_password
        full_name = str(resume_hint.get("full_name") or "").strip() or full_name
        birthdate = str(resume_hint.get("birthdate") or "").strip() or birthdate
    trace["account_password"] = account_password
    trace["profile"] = {"full_name": full_name, "birthdate": birthdate}

    zzz_log_info(
        logger,
        with_log_prefix(log_prefix, f"成功获取 {provider_label} 邮箱与授权: {temp_mail_account.email}"),
    )
    zzz_log_info(
        logger,
        with_log_prefix(log_prefix, f"本次待注册账号密码已生成，姓名={full_name}，生日={birthdate}"),
    )
    append_register_task_event(
        trace,
        "account_profile",
        "已准备账号资料",
        email=temp_mail_account.email,
        account_password=account_password,
        full_name=full_name,
        birthdate=birthdate,
    )

    resume_oauth_only = bool(
        resume_hint
        and account_password
        and (
            bool(resume_hint.get("oauth_only_hint"))
            or str(resume_hint.get("failure_stage") or "").strip() in {"legacy_oauth", "normalize_token_json", "save_raw_token_json"}
        )
    )
    if resume_oauth_only:
        zzz_log_info(
            logger,
            with_log_prefix(
                log_prefix,
                f"复用失败邮箱命中 OAuth-only 提示，跳过注册阶段，直接重试 OAuth: {temp_mail_account.email}",
            ),
        )
        append_register_task_event(
            trace,
            "decision",
            "复用失败邮箱，直接从 OAuth 阶段继续",
            failure_stage=resume_hint.get("failure_stage"),
            reuse_count=resume_hint.get("reuse_count"),
        )

    if resume_oauth_only:
        log_step(3, total_steps, "复用失败邮箱，跳过注册阶段直接进入 OAuth")
    else:
        try:
            log_step(3, total_steps, "执行旧版 ChatGPT 注册流程")
            legacy_reg.run_register(
                temp_mail_account.email,
                account_password,
                full_name,
                birthdate,
                temp_mail_account.token,
            )
        except Exception as e:
            zzz_log_error(logger, with_log_prefix(log_prefix, f"旧版注册流程失败: {e}"))
            return failure_result("legacy_register", e)

    try:
        log_step(4, total_steps, "执行旧版 Codex OAuth 流程")
        tokens = legacy_reg.perform_codex_oauth_login_http(
            temp_mail_account.email,
            account_password,
            mail_token=temp_mail_account.token,
        )
    except Exception as e:
        zzz_log_error(logger, with_log_prefix(log_prefix, f"旧版 OAuth 流程异常: {e}"))
        return failure_result("legacy_oauth", e)

    if not tokens or not str(tokens.get("access_token") or "").strip():
        detail = "perform_codex_oauth_login_http 返回空结果或缺少 access_token"
        zzz_log_error(logger, with_log_prefix(log_prefix, detail))
        return failure_result("legacy_oauth", detail)

    try:
        log_step(5, total_steps, "标准化 token 数据并返回")
        token_json = build_standard_token_json(temp_mail_account.email, tokens)
        append_register_task_event(trace, "success", "旧方案完整流程成功", email=temp_mail_account.email)
        zzz_log_success(logger, with_log_prefix(log_prefix, f"旧方案完整流程成功: {temp_mail_account.email}"))
        final_trace = finalize_register_task_trace(
            trace,
            status="success",
            token_json=token_json,
            temp_mail_account=temp_mail_account,
            account_password=account_password,
            full_name=full_name,
            birthdate=birthdate,
        )
        return RegisterAttemptResult(
            token_json=token_json,
            temp_mail_account=temp_mail_account,
            account_password=account_password,
            task_trace=final_trace,
        )
    except Exception as e:
        zzz_log_error(logger, with_log_prefix(log_prefix, f"标准化 token 数据失败: {e}"))
        return failure_result("normalize_token_json", e)


def zzz_http_request(
    method: str,
    url: str,
    *,
    session: Any = None,
    headers: Optional[Dict[str, str]] = None,
    json_body: Any = None,
    data: Any = None,
    proxies: Any = None,
    timeout: int = 15,
    allow_redirects: Optional[bool] = None,
) -> Any:
    """统一封装 zzz 流程的 HTTP 请求，兼容 curl_cffi 与 requests。"""
    kwargs: Dict[str, Any] = {"timeout": timeout}
    if headers is not None:
        kwargs["headers"] = headers
    if json_body is not None:
        kwargs["json"] = json_body
    if data is not None:
        kwargs["data"] = data
    if proxies is not None:
        kwargs["proxies"] = proxies
    if allow_redirects is not None:
        kwargs["allow_redirects"] = allow_redirects
    if curl_requests is not None:
        kwargs["impersonate"] = "chrome"

    client = session if session is not None else (curl_requests if curl_requests is not None else requests)
    fn = getattr(client, method.lower())
    return fn(url, **kwargs)


def zzz_create_session(proxies: Any = None) -> Any:
    """创建兼容 curl_cffi / requests 的会话对象，用于 zzz OAuth 流程。"""
    if curl_requests is not None:
        return curl_requests.Session(proxies=proxies, impersonate="chrome")
    s = requests.Session()
    if proxies:
        s.proxies = proxies
    return s


def zzz_mailtm_headers(*, token: str = "", use_json: bool = False) -> Dict[str, str]:
    """兼容旧接口名，默认生成 Mail.tm 风格请求头。"""
    return build_temp_mail_headers(provider="mailtm", token=token, use_json=use_json)


def zzz_temp_mail_request(
    method: str,
    config: TempMailConfig,
    path: str,
    *,
    token: str = "",
    json_body: Any = None,
    data: Any = None,
    proxies: Any = None,
    timeout: int = 15,
    logger: Optional[logging.Logger] = None,
    retries: int = 2,
) -> Any:
    """对临时邮箱 API 做统一请求，并为超时/5xx/429 提供轻量重试。"""
    url = f"{config.base_url}{path}"
    last_resp = None
    last_error: Optional[Exception] = None

    for attempt in range(retries + 1):
        try:
            resp = zzz_http_request(
                method,
                url,
                headers=build_temp_mail_headers(
                    provider=config.provider,
                    token=token,
                    api_key=config.api_key,
                    use_json=json_body is not None,
                ),
                json_body=json_body,
                data=data,
                proxies=proxies,
                timeout=timeout,
            )
            last_resp = resp
            if resp.status_code in (408, 429) or resp.status_code >= 500:
                if attempt < retries:
                    time.sleep(1 + attempt)
                    continue
            return resp
        except Exception as e:
            last_error = e
            if attempt >= retries:
                break
            if logger:
                zzz_log_error(
                    logger,
                    f"{get_email_provider_label(config.provider)} 请求异常，准备重试 {attempt + 1}/{retries}: {e}",
                )
            time.sleep(1 + attempt)

    if last_resp is not None:
        return last_resp
    raise RuntimeError(f"{get_email_provider_label(config.provider)} 请求失败: {last_error}")


def zzz_mailtm_domains(
    proxies: Any,
    logger: logging.Logger,
    *,
    provider: str = DEFAULT_EMAIL_PROVIDER,
    base_url: str = "",
    api_key: str = "",
) -> List[str]:
    """获取可用域名列表，并统一处理 Mail.tm / DuckMail 的返回结构。"""
    mail_config = resolve_temp_mail_config(
        make_temp_mail_config(provider=provider, worker_domain=base_url, api_key=api_key)
    )
    resp = zzz_temp_mail_request(
        "get",
        mail_config,
        get_temp_mail_domain_path(mail_config.provider),
        proxies=proxies,
        timeout=15,
        logger=logger,
    )
    if resp.status_code != 200:
        detail = extract_temp_mail_error(resp)
        raise RuntimeError(
            f"获取 {get_email_provider_label(mail_config.provider)} 域名失败，状态码: {resp.status_code}，详情: {detail}"
        )

    settings_payload = safe_response_json(resp)
    domains = normalize_temp_mail_domains(settings_payload, mail_config.provider)
    if not domains:
        if mail_config.provider == "cfmail" and isinstance(settings_payload, dict):
            need_auth = bool(settings_payload.get("needAuth"))
            enable_create = settings_payload.get("enableUserCreateEmail")
            zzz_log_error(
                logger,
                (
                    f"{get_email_provider_label(mail_config.provider)} 没有可用域名"
                    f" needAuth={need_auth} enableUserCreateEmail={enable_create} "
                    f"domains={trace_preview(settings_payload.get('domains'))} "
                    f"defaultDomains={trace_preview(settings_payload.get('defaultDomains'))}"
                ),
            )
        else:
            zzz_log_error(logger, f"{get_email_provider_label(mail_config.provider)} 没有可用域名")
    return domains


def zzz_get_email_and_token(
    proxies: Any,
    logger: logging.Logger,
    *,
    provider: str = DEFAULT_EMAIL_PROVIDER,
    base_url: str = "",
    email_domains: Optional[List[str]] = None,
    api_key: str = "",
    log_prefix: str = "",
) -> Optional[TempMailAccount]:
    """创建临时邮箱并返回邮箱地址、密码与访问 token。"""
    mail_config = resolve_temp_mail_config(
        make_temp_mail_config(provider=provider, worker_domain=base_url, api_key=api_key)
    )
    try:
        domains = zzz_mailtm_domains(
            proxies,
            logger,
            provider=mail_config.provider,
            base_url=mail_config.base_url,
            api_key=mail_config.api_key,
        )
        preferred = [str(x).strip().lower() for x in (email_domains or []) if str(x).strip()]
        if preferred:
            filtered = [d for d in domains if d.lower() in preferred]
            if filtered:
                domains = filtered
        if not domains:
            return None

        provider_label = get_email_provider_label(mail_config.provider)
        zzz_log_info(
            logger,
            with_log_prefix(log_prefix, f"准备创建 {provider_label} 邮箱，候选域名数量: {len(domains)}"),
        )
        for attempt in range(1, 6):
            domain = random.choice(domains)
            local = f"oc{secrets.token_hex(5)}"
            requested_email = f"{local}@{domain}"
            requested_password = secrets.token_urlsafe(18)
            zzz_log_info(
                logger,
                with_log_prefix(log_prefix, f"{provider_label} 邮箱创建尝试 {attempt}/5: {requested_email}"),
            )

            create_resp = zzz_temp_mail_request(
                "post",
                mail_config,
                get_temp_mail_account_create_path(mail_config.provider),
                json_body=build_temp_mail_account_create_payload(
                    mail_config.provider,
                    requested_email,
                    requested_password,
                ),
                proxies=proxies,
                timeout=15,
                logger=logger,
            )
            if create_resp.status_code not in (200, 201):
                detail = extract_temp_mail_error(create_resp)
                zzz_log_error_detail(
                    logger,
                    with_log_prefix(
                        log_prefix,
                        f"{provider_label} 邮箱创建失败，状态码: {create_resp.status_code}，详情: {detail}",
                    ),
                )
                continue

            create_data = safe_response_json(create_resp)
            final_email = extract_temp_mail_account_email(create_data, requested_email)
            final_password = extract_temp_mail_account_password(create_data, requested_password)
            token = extract_temp_mail_token(create_data)
            token_path = get_temp_mail_token_path(mail_config.provider)
            if not token and token_path and final_password:
                token_resp = zzz_temp_mail_request(
                    "post",
                    mail_config,
                    token_path,
                    json_body=build_temp_mail_token_payload(
                        mail_config.provider,
                        final_email,
                        final_password,
                    ),
                    proxies=proxies,
                    timeout=15,
                    logger=logger,
                )
                if token_resp.status_code != 200:
                    detail = extract_temp_mail_error(token_resp)
                    zzz_log_error_detail(
                        logger,
                        with_log_prefix(
                            log_prefix,
                            f"{provider_label} 邮箱 token 获取失败，状态码: {token_resp.status_code}，详情: {detail}",
                        ),
                    )
                    continue
                token = extract_temp_mail_token(safe_response_json(token_resp))
            if token:
                zzz_log_info(logger, with_log_prefix(log_prefix, f"创建 {provider_label} 邮箱成功: {final_email}"))
                return TempMailAccount(
                    email=final_email,
                    password=final_password,
                    token=token,
                    provider=mail_config.provider,
                )

        zzz_log_error(logger, with_log_prefix(log_prefix, f"{provider_label} 创建邮箱成功但取 token 失败"))
        return None
    except Exception as e:
        zzz_log_error(
            logger,
            with_log_prefix(log_prefix, f"请求 {get_email_provider_label(mail_config.provider)} API 出错: {e}"),
        )
        return None


def zzz_restore_temp_mail_account(
    email: str,
    password: str,
    cached_token: str,
    proxies: Any,
    logger: logging.Logger,
    *,
    provider: str = DEFAULT_EMAIL_PROVIDER,
    base_url: str = "",
    api_key: str = "",
    log_prefix: str = "",
) -> Optional[TempMailAccount]:
    """复用失败任务留下的邮箱，并尽量重新获取可用 token。"""
    normalized_email = str(email or "").strip()
    normalized_password = str(password or "").strip()
    stored_token = str(cached_token or "").strip()
    if not normalized_email or not normalized_password:
        return None

    mail_config = resolve_temp_mail_config(
        make_temp_mail_config(provider=provider, worker_domain=base_url, api_key=api_key)
    )
    provider_label = get_email_provider_label(mail_config.provider)
    token_path = get_temp_mail_token_path(mail_config.provider)

    if token_path:
        try:
            token_resp = zzz_temp_mail_request(
                "post",
                mail_config,
                token_path,
                json_body=build_temp_mail_token_payload(
                    mail_config.provider,
                    normalized_email,
                    normalized_password,
                ),
                proxies=proxies,
                timeout=15,
                logger=logger,
            )
            if token_resp.status_code == 200:
                token = extract_temp_mail_token(safe_response_json(token_resp))
                if token:
                    zzz_log_info(
                        logger,
                        with_log_prefix(log_prefix, f"复用 {provider_label} 邮箱成功并重新获取 token: {normalized_email}"),
                    )
                    return TempMailAccount(
                        email=normalized_email,
                        password=normalized_password,
                        token=token,
                        provider=mail_config.provider,
                    )
            detail = extract_temp_mail_error(token_resp)
            zzz_log_error_detail(
                logger,
                with_log_prefix(
                    log_prefix,
                    f"复用 {provider_label} 邮箱重新登录失败，状态码: {token_resp.status_code}，详情: {detail}",
                ),
            )
        except Exception as e:
            zzz_log_error_detail(
                logger,
                with_log_prefix(log_prefix, f"复用 {provider_label} 邮箱重新登录异常: {e}"),
            )

    if stored_token:
        zzz_log_info(
            logger,
            with_log_prefix(log_prefix, f"复用 {provider_label} 邮箱回退使用缓存 token: {normalized_email}"),
        )
        return TempMailAccount(
            email=normalized_email,
            password=normalized_password,
            token=stored_token,
            provider=mail_config.provider,
        )

    zzz_log_error(logger, with_log_prefix(log_prefix, f"复用 {provider_label} 邮箱失败，未能获得有效 token: {normalized_email}"))
    return None


def zzz_fetch_temp_mail_messages(
    token: str,
    proxies: Any,
    logger: logging.Logger,
    *,
    provider: str = DEFAULT_EMAIL_PROVIDER,
    base_url: str = "",
    api_key: str = "",
) -> List[Dict[str, Any]]:
    """拉取收件箱列表，并统一为 Mail.tm 风格的消息对象。"""
    mail_config = resolve_temp_mail_config(
        make_temp_mail_config(provider=provider, worker_domain=base_url, api_key=api_key)
    )
    resp = zzz_temp_mail_request(
        "get",
        mail_config,
        get_temp_mail_messages_path(mail_config.provider),
        token=token,
        proxies=proxies,
        timeout=15,
        logger=logger,
    )
    if resp.status_code != 200:
        return []

    rows = extract_temp_mail_message_rows(safe_response_json(resp), mail_config.provider)
    return [msg for msg in (normalize_temp_mail_message(row, mail_config.provider) for row in rows) if msg.get("id")]


def zzz_fetch_temp_mail_detail(
    token: str,
    msg_id: str,
    proxies: Any,
    logger: logging.Logger,
    *,
    provider: str = DEFAULT_EMAIL_PROVIDER,
    base_url: str = "",
    api_key: str = "",
) -> Dict[str, Any]:
    """读取单封邮件详情，并兼容字段缺失或邮件不存在的情况。"""
    if not msg_id:
        return {}
    mail_config = resolve_temp_mail_config(
        make_temp_mail_config(provider=provider, worker_domain=base_url, api_key=api_key)
    )
    resp = zzz_temp_mail_request(
        "get",
        mail_config,
        get_temp_mail_message_detail_path(mail_config.provider, msg_id),
        token=token,
        proxies=proxies,
        timeout=15,
        logger=logger,
    )
    if resp.status_code != 200:
        return {}

    raw_detail = safe_response_json(resp)
    if isinstance(raw_detail, dict) and isinstance(raw_detail.get("data"), dict):
        raw_detail = raw_detail.get("data") or raw_detail
    if raw_detail is None:
        return {}
    detail = normalize_temp_mail_message(raw_detail, mail_config.provider)
    if not detail.get("id"):
        detail["id"] = msg_id
    return detail


def zzz_get_oai_code(
    token: str,
    email: str,
    proxies: Any,
    logger: logging.Logger,
    *,
    provider: str = DEFAULT_EMAIL_PROVIDER,
    base_url: str = "",
    api_key: str = "",
    log_prefix: str = "",
) -> str:
    """轮询收件箱，读取最新邮件详情并提取 OpenAI 六位验证码。"""
    regex = r"(?<!\d)(\d{6})(?!\d)"
    seen_ids: set[str] = set()
    provider_label = get_email_provider_label(normalize_email_provider(provider))
    zzz_log_info(logger, with_log_prefix(log_prefix, f"正在等待 {provider_label} 邮箱 {email} 的验证码"))

    for poll_index in range(1, 41):
        try:
            if poll_index == 1 or poll_index % 5 == 0:
                zzz_log_info(
                    logger,
                    with_log_prefix(log_prefix, f"验证码轮询进度 {poll_index}/40: {email}"),
                )
            messages = zzz_fetch_temp_mail_messages(
                token,
                proxies,
                logger,
                provider=provider,
                base_url=base_url,
                api_key=api_key,
            )
            for msg in messages:
                msg_id = str(msg.get("id") or "").strip()
                if not msg_id or msg_id in seen_ids:
                    continue
                seen_ids.add(msg_id)

                mail_data = zzz_fetch_temp_mail_detail(
                    token,
                    msg_id,
                    proxies,
                    logger,
                    provider=provider,
                    base_url=base_url,
                    api_key=api_key,
                )
                sender = str(((mail_data.get("from") or {}).get("address") or "")).lower()
                subject = str(mail_data.get("subject") or "")
                intro = str(mail_data.get("intro") or "")
                text = str(mail_data.get("text") or "")
                html = mail_data.get("html") or ""
                if isinstance(html, list):
                    html = "\n".join(str(x) for x in html)
                content = "\n".join([subject, intro, text, str(html)])

                # 只处理来自 OpenAI 或正文中明显属于 OpenAI 的邮件，避免误读其他验证码。
                if "openai" not in sender and "openai" not in content.lower():
                    continue

                m = re.search(regex, content)
                if m:
                    code = m.group(1)
                    zzz_log_success(logger, with_log_prefix(log_prefix, f"抓到验证码: {code}"))
                    return code
        except Exception:
            pass
        time.sleep(3)

    zzz_log_error(logger, with_log_prefix(log_prefix, f"{provider_label} 超时，未收到验证码"))
    return ""


ZZZ_AUTH_URL = "https://auth.openai.com/oauth/authorize"
ZZZ_TOKEN_URL = "https://auth.openai.com/oauth/token"
ZZZ_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
ZZZ_DEFAULT_REDIRECT_URI = "http://localhost:1455/auth/callback"
ZZZ_DEFAULT_SCOPE = "openid email profile offline_access"


def zzz_b64url_no_pad(raw: bytes) -> str:
    """生成不带填充的 base64url 字符串。"""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def zzz_sha256_b64url_no_pad(value: str) -> str:
    """计算 PKCE 所需的 SHA256 code challenge。"""
    return zzz_b64url_no_pad(hashlib.sha256(value.encode("ascii")).digest())


def zzz_random_state(nbytes: int = 16) -> str:
    """生成 OAuth state，防止回调串改。"""
    return secrets.token_urlsafe(nbytes)


def zzz_pkce_verifier() -> str:
    """生成 OAuth PKCE verifier。"""
    return secrets.token_urlsafe(64)


def zzz_parse_callback_url(callback_url: str) -> Dict[str, str]:
    """兼容 query/fragment/裸参数形式，统一解析 OAuth 回调。"""
    candidate = callback_url.strip()
    if not candidate:
        return {"code": "", "state": "", "error": "", "error_description": ""}

    if "://" not in candidate:
        if candidate.startswith("?"):
            candidate = f"http://localhost{candidate}"
        elif any(ch in candidate for ch in "/?#") or ":" in candidate:
            candidate = f"http://{candidate}"
        elif "=" in candidate:
            candidate = f"http://localhost/?{candidate}"

    parsed = urlparse(candidate)
    query = parse_qs(parsed.query, keep_blank_values=True)
    fragment = parse_qs(parsed.fragment, keep_blank_values=True)
    for key, values in fragment.items():
        if key not in query or not query[key] or not (query[key][0] or "").strip():
            query[key] = values

    def get1(k: str) -> str:
        v = query.get(k, [""])
        return (v[0] or "").strip()

    code = get1("code")
    state = get1("state")
    error = get1("error")
    error_description = get1("error_description")
    if code and not state and "#" in code:
        code, state = code.split("#", 1)
    if not error and error_description:
        error, error_description = error_description, ""

    return {"code": code, "state": state, "error": error, "error_description": error_description}


def zzz_jwt_claims_no_verify(id_token: str) -> Dict[str, Any]:
    """在不校验签名的前提下读取 JWT claims，仅用于提取非敏感字段。"""
    if not id_token or id_token.count(".") < 2:
        return {}
    payload_b64 = id_token.split(".")[1]
    pad = "=" * ((4 - (len(payload_b64) % 4)) % 4)
    try:
        payload = base64.urlsafe_b64decode((payload_b64 + pad).encode("ascii"))
        data = json.loads(payload.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def zzz_decode_jwt_segment(seg: str) -> Dict[str, Any]:
    """解码 JWT 某一段，失败时返回空对象。"""
    raw = (seg or "").strip()
    if not raw:
        return {}
    pad = "=" * ((4 - (len(raw) % 4)) % 4)
    try:
        decoded = base64.urlsafe_b64decode((raw + pad).encode("ascii"))
        data = json.loads(decoded.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def zzz_to_int(v: Any) -> int:
    """安全转 int，异常时返回 0。"""
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def zzz_post_form(url: str, data: Dict[str, str], proxies: Any = None, timeout: int = 30) -> Dict[str, Any]:
    """提交 OAuth 表单换 token，请求失败时抛出明确异常。"""
    # 这里显式走 requests，避免 curl_cffi 在部分环境中对 OpenAI OAuth token 交换出现 TLS 兼容问题。
    resp = requests.post(
        url,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        data=data,
        proxies=proxies,
        timeout=timeout,
    )
    if resp.status_code != 200:
        text = (resp.text or "")[:300]
        raise RuntimeError(f"token exchange failed: {resp.status_code}: {text}")
    data_obj = resp.json()
    return data_obj if isinstance(data_obj, dict) else {}


@dataclass(frozen=True)
class ZZZOAuthStart:
    """保存 OAuth 启动阶段生成的 URL 与 PKCE/State 参数。"""

    auth_url: str
    state: str
    code_verifier: str
    redirect_uri: str


def zzz_generate_oauth_url(
    *,
    redirect_uri: str = ZZZ_DEFAULT_REDIRECT_URI,
    scope: str = ZZZ_DEFAULT_SCOPE,
) -> ZZZOAuthStart:
    """生成 OpenAI OAuth 授权 URL 及关联的 PKCE 参数。"""
    state = zzz_random_state()
    code_verifier = zzz_pkce_verifier()
    code_challenge = zzz_sha256_b64url_no_pad(code_verifier)
    params = {
        "client_id": ZZZ_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "prompt": "login",
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
    }
    auth_url = f"{ZZZ_AUTH_URL}?{urlencode(params)}"
    return ZZZOAuthStart(
        auth_url=auth_url,
        state=state,
        code_verifier=code_verifier,
        redirect_uri=redirect_uri,
    )


def zzz_submit_callback_url(
    *,
    callback_url: str,
    expected_state: str,
    code_verifier: str,
    redirect_uri: str = ZZZ_DEFAULT_REDIRECT_URI,
    proxies: Any = None,
) -> str:
    """校验 callback 参数并完成授权码换 token，返回标准 token JSON 字符串。"""
    cb = zzz_parse_callback_url(callback_url)
    if cb["error"]:
        desc = cb["error_description"]
        raise RuntimeError(f"oauth error: {cb['error']}: {desc}".strip())
    if not cb["code"]:
        raise ValueError("callback url missing ?code=")
    if not cb["state"]:
        raise ValueError("callback url missing ?state=")
    if cb["state"] != expected_state:
        raise ValueError("state mismatch")

    token_resp = zzz_post_form(
        ZZZ_TOKEN_URL,
        {
            "grant_type": "authorization_code",
            "client_id": ZZZ_CLIENT_ID,
            "code": cb["code"],
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        },
        proxies=proxies,
    )

    access_token = str(token_resp.get("access_token") or "").strip()
    refresh_token = str(token_resp.get("refresh_token") or "").strip()
    id_token = str(token_resp.get("id_token") or "").strip()
    expires_in = zzz_to_int(token_resp.get("expires_in"))

    claims = zzz_jwt_claims_no_verify(id_token)
    email = str(claims.get("email") or "").strip()
    auth_claims = claims.get("https://api.openai.com/auth") or {}
    account_id = str((auth_claims or {}).get("chatgpt_account_id") or "").strip()

    now = int(time.time())
    expired_rfc3339 = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + max(expires_in, 0)))
    now_rfc3339 = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    config = {
        "id_token": id_token,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "account_id": account_id,
        "last_refresh": now_rfc3339,
        "email": email,
        "type": "codex",
        "expired": expired_rfc3339,
    }
    return json.dumps(config, ensure_ascii=False, separators=(",", ":"))


def zzz_run_full_flow(
    proxy: Optional[str],
    logger: logging.Logger,
    *,
    email_provider: str = DEFAULT_EMAIL_PROVIDER,
    email_base: str = "",
    email_domains: Optional[List[str]] = None,
    email_api_key: str = "",
    run_label: str = "",
) -> RegisterAttemptResult:
    """执行完整的临时邮箱注册 + OpenAI OAuth 换 token 流程。"""
    proxies: Any = {"http": proxy, "https": proxy} if proxy else None
    session = zzz_create_session(proxies=proxies)
    mail_config = make_temp_mail_config(
        provider=email_provider,
        worker_domain=email_base,
        api_key=email_api_key,
    )
    provider_label = get_email_provider_label(mail_config.provider)
    log_prefix = f"[{run_label}] " if run_label else ""
    temp_mail_account: Optional[TempMailAccount] = None

    def log_step(step_no: int, total_steps: int, message: str) -> None:
        zzz_log_info(logger, with_log_prefix(log_prefix, f"步骤{step_no}/{total_steps}: {message}"))

    total_steps = 9

    try:
        log_step(1, total_steps, "检查代理出口与网络连通性")
        trace_resp = zzz_http_request("get", "https://cloudflare.com/cdn-cgi/trace", session=session, timeout=10)
        trace_text = str(getattr(trace_resp, "text", "") or "")
        loc_match = re.search(r"^loc=(.+)$", trace_text, re.MULTILINE)
        loc = loc_match.group(1) if loc_match else None
        zzz_log_info(logger, with_log_prefix(log_prefix, f"当前 IP 所在地: {loc}"))
        if loc in ("CN", "HK"):
            raise RuntimeError("检查代理哦w - 所在地不支持")
    except Exception as e:
        zzz_log_error(logger, with_log_prefix(log_prefix, f"网络连接检查失败: {e}"))
        return RegisterAttemptResult(token_json=None, temp_mail_account=None, failure_stage="network_check")

    log_step(2, total_steps, f"创建 {provider_label} 临时邮箱")
    temp_mail_account = zzz_get_email_and_token(
        proxies,
        logger,
        provider=mail_config.provider,
        base_url=mail_config.base_url,
        email_domains=email_domains,
        api_key=mail_config.api_key,
        log_prefix=log_prefix,
    )
    if not temp_mail_account:
        return RegisterAttemptResult(token_json=None, temp_mail_account=None, failure_stage="create_temp_mail")
    zzz_log_info(
        logger,
        with_log_prefix(log_prefix, f"成功获取 {provider_label} 邮箱与授权: {temp_mail_account.email}"),
    )

    log_step(3, total_steps, "初始化 OpenAI OAuth 会话")
    oauth = zzz_generate_oauth_url()
    try:
        zzz_http_request("get", oauth.auth_url, session=session, timeout=15)
        did = session.cookies.get("oai-did")
        zzz_log_info(logger, with_log_prefix(log_prefix, f"Device ID: {did}"))

        log_step(4, total_steps, "申请 Sentinel 校验令牌")
        signup_body = f'{{"username":{{"value":"{temp_mail_account.email}","kind":"email"}},"screen_hint":"signup"}}'
        sen_req_body = f'{{"p":"","id":"{did}","flow":"authorize_continue"}}'
        sen_resp = zzz_http_request(
            "post",
            "https://sentinel.openai.com/backend-api/sentinel/req",
            headers={
                "origin": "https://sentinel.openai.com",
                "referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html?sv=20260219f9f6",
                "content-type": "text/plain;charset=UTF-8",
            },
            data=sen_req_body,
            proxies=proxies,
            timeout=15,
        )
        if sen_resp.status_code != 200:
            zzz_log_error(logger, with_log_prefix(log_prefix, f"Sentinel 异常拦截，状态码: {sen_resp.status_code}"))
            return RegisterAttemptResult(
                token_json=None,
                temp_mail_account=temp_mail_account,
                failure_stage="sentinel_request",
            )
        sen_token = str((sen_resp.json() or {}).get("token") or "")
        sentinel = f'{{"p": "", "t": "", "c": "{sen_token}", "id": "{did}", "flow": "authorize_continue"}}'

        log_step(5, total_steps, "提交邮箱注册请求")
        signup_resp = zzz_http_request(
            "post",
            "https://auth.openai.com/api/accounts/authorize/continue",
            session=session,
            headers={
                "referer": "https://auth.openai.com/create-account",
                "accept": "application/json",
                "content-type": "application/json",
                "openai-sentinel-token": sentinel,
            },
            data=signup_body,
            timeout=15,
        )
        zzz_log_info(logger, with_log_prefix(log_prefix, f"提交注册表单状态: {signup_resp.status_code}"))

        log_step(6, total_steps, "发送并等待邮箱验证码")
        otp_resp = zzz_http_request(
            "post",
            "https://auth.openai.com/api/accounts/passwordless/send-otp",
            session=session,
            headers={
                "referer": "https://auth.openai.com/create-account/password",
                "accept": "application/json",
                "content-type": "application/json",
            },
            timeout=15,
        )
        zzz_log_info(logger, with_log_prefix(log_prefix, f"验证码发送状态: {otp_resp.status_code}"))

        code = zzz_get_oai_code(
            temp_mail_account.token,
            temp_mail_account.email,
            proxies,
            logger,
            provider=mail_config.provider,
            base_url=mail_config.base_url,
            api_key=mail_config.api_key,
            log_prefix=log_prefix,
        )
        if not code:
            return RegisterAttemptResult(
                token_json=None,
                temp_mail_account=temp_mail_account,
                failure_stage="wait_email_otp",
            )

        log_step(7, total_steps, "校验邮箱验证码")
        code_resp = zzz_http_request(
            "post",
            "https://auth.openai.com/api/accounts/email-otp/validate",
            session=session,
            headers={
                "referer": "https://auth.openai.com/email-verification",
                "accept": "application/json",
                "content-type": "application/json",
            },
            data=f'{{"code":"{code}"}}',
            timeout=15,
        )
        zzz_log_info(logger, with_log_prefix(log_prefix, f"验证码校验状态: {code_resp.status_code}"))

        log_step(8, total_steps, "创建 OpenAI 账号")
        create_resp = zzz_http_request(
            "post",
            "https://auth.openai.com/api/accounts/create_account",
            session=session,
            headers={
                "referer": "https://auth.openai.com/about-you",
                "accept": "application/json",
                "content-type": "application/json",
            },
            data='{"name":"Neo","birthdate":"2000-02-20"}',
            timeout=15,
        )
        if create_resp.status_code != 200:
            zzz_log_error(logger, with_log_prefix(log_prefix, f"账户创建状态: {create_resp.status_code}"))
            zzz_log_error_detail(
                logger,
                with_log_prefix(log_prefix, str(getattr(create_resp, "text", "") or "")),
            )
            return RegisterAttemptResult(
                token_json=None,
                temp_mail_account=temp_mail_account,
                failure_stage="create_openai_account",
            )

        auth_cookie = session.cookies.get("oai-client-auth-session")
        if not auth_cookie:
            zzz_log_error(logger, with_log_prefix(log_prefix, "未能获取到授权 Cookie"))
            return RegisterAttemptResult(
                token_json=None,
                temp_mail_account=temp_mail_account,
                failure_stage="missing_auth_cookie",
            )

        auth_json = zzz_decode_jwt_segment(auth_cookie.split(".")[0])
        workspaces = auth_json.get("workspaces") or []
        if not workspaces:
            zzz_log_error(logger, with_log_prefix(log_prefix, "授权 Cookie 里没有 workspace 信息"))
            return RegisterAttemptResult(
                token_json=None,
                temp_mail_account=temp_mail_account,
                failure_stage="missing_workspace",
            )
        workspace_id = str((workspaces[0] or {}).get("id") or "").strip()
        if not workspace_id:
            zzz_log_error(logger, with_log_prefix(log_prefix, "无法解析 workspace_id"))
            return RegisterAttemptResult(
                token_json=None,
                temp_mail_account=temp_mail_account,
                failure_stage="parse_workspace_id",
            )

        log_step(9, total_steps, "选择工作区并换取 OAuth Token")
        select_resp = zzz_http_request(
            "post",
            "https://auth.openai.com/api/accounts/workspace/select",
            session=session,
            headers={
                "referer": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                "content-type": "application/json",
            },
            data=f'{{"workspace_id":"{workspace_id}"}}',
            timeout=15,
        )
        if select_resp.status_code != 200:
            zzz_log_error(
                logger,
                with_log_prefix(log_prefix, f"选择 workspace 失败，状态码: {select_resp.status_code}"),
            )
            zzz_log_error_detail(
                logger,
                with_log_prefix(log_prefix, str(getattr(select_resp, "text", "") or "")),
            )
            return RegisterAttemptResult(
                token_json=None,
                temp_mail_account=temp_mail_account,
                failure_stage="workspace_select",
            )

        continue_url = str((select_resp.json() or {}).get("continue_url") or "").strip()
        if not continue_url:
            zzz_log_error(logger, with_log_prefix(log_prefix, "workspace/select 响应里缺少 continue_url"))
            return RegisterAttemptResult(
                token_json=None,
                temp_mail_account=temp_mail_account,
                failure_stage="missing_continue_url",
            )

        current_url = continue_url
        for _ in range(6):
            final_resp = zzz_http_request(
                "get",
                current_url,
                session=session,
                timeout=15,
                allow_redirects=False,
            )
            location = final_resp.headers.get("Location") or ""
            if final_resp.status_code not in [301, 302, 303, 307, 308]:
                break
            if not location:
                break
            next_url = urljoin(current_url, location)
            if "code=" in next_url and "state=" in next_url:
                token_json = zzz_submit_callback_url(
                    callback_url=next_url,
                    expected_state=oauth.state,
                    code_verifier=oauth.code_verifier,
                    redirect_uri=oauth.redirect_uri,
                    proxies=proxies,
                )
                zzz_log_success(
                    logger,
                    with_log_prefix(log_prefix, f"OAuth Token 获取成功: {temp_mail_account.email}"),
                )
                return RegisterAttemptResult(token_json=token_json, temp_mail_account=temp_mail_account)
            current_url = next_url

        zzz_log_error(logger, with_log_prefix(log_prefix, "未能在重定向链中捕获到最终 Callback URL"))
        return RegisterAttemptResult(
            token_json=None,
            temp_mail_account=temp_mail_account,
            failure_stage="missing_callback_url",
        )
    except Exception as e:
        err = str(e)
        m = re.search(r"(https?://localhost[^\s'\"\\]+)", err)
        if m:
            try:
                token_json = zzz_submit_callback_url(
                    callback_url=m.group(1),
                    expected_state=oauth.state,
                    code_verifier=oauth.code_verifier,
                    redirect_uri=oauth.redirect_uri,
                    proxies=proxies,
                )
                zzz_log_success(
                    logger,
                    with_log_prefix(log_prefix, f"OAuth Token 获取成功: {temp_mail_account.email if temp_mail_account else ''}"),
                )
                return RegisterAttemptResult(token_json=token_json, temp_mail_account=temp_mail_account)
            except Exception:
                pass
        zzz_log_error(logger, with_log_prefix(log_prefix, f"运行时发生错误: {e}"))
        return RegisterAttemptResult(
            token_json=None,
            temp_mail_account=temp_mail_account,
            failure_stage="runtime_exception",
        )


def get_candidates_count(base_url: str, token: str, target_type: str, timeout: int) -> tuple[int, int]:
    """统计管理端全部账号数与目标类型账号数。"""
    url = f"{base_url.rstrip('/')}/v0/management/auth-files"
    resp = requests.get(url, headers=mgmt_headers(token), timeout=timeout)
    resp.raise_for_status()
    raw = resp.json()
    payload = raw if isinstance(raw, dict) else {}
    files = payload.get("files", []) if isinstance(payload, dict) else []
    candidates = []
    for f in files:
        if get_item_type(f).lower() != target_type.lower():
            continue
        if not is_auth_file_candidate_available(f):
            continue
        candidates.append(f)
    return len(files), len(candidates)


def create_session(proxy: str = "") -> requests.Session:
    """创建带轻量重试的 requests.Session，用于常规 HTTP 流程。"""
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    return s


def generate_pkce() -> tuple[str, str]:
    """生成 OAuth 登录所需的 PKCE verifier / challenge。"""
    code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


def generate_datadog_trace() -> Dict[str, str]:
    """模拟前端埋点链路头，降低部分接口的风控异常概率。"""
    trace_id = str(random.getrandbits(64))
    parent_id = str(random.getrandbits(64))
    trace_hex = format(int(trace_id), "016x")
    parent_hex = format(int(parent_id), "016x")
    return {
        "traceparent": f"00-0000000000000000{trace_hex}-{parent_hex}-01",
        "tracestate": "dd=s:1;o:rum",
        "x-datadog-origin": "rum",
        "x-datadog-parent-id": parent_id,
        "x-datadog-sampling-priority": "1",
        "x-datadog-trace-id": trace_id,
    }


def generate_random_password(length: int = 16) -> str:
    """生成符合复杂度要求的随机密码。"""
    chars = string.ascii_letters + string.digits + "!@#$%"
    pwd = list(
        secrets.choice(string.ascii_uppercase)
        + secrets.choice(string.ascii_lowercase)
        + secrets.choice(string.digits)
        + secrets.choice("!@#$%")
        + "".join(secrets.choice(chars) for _ in range(length - 4))
    )
    random.shuffle(pwd)
    return "".join(pwd)


def generate_random_name() -> tuple[str, str]:
    """随机生成英文名，用于 about-you 阶段。"""
    first = ["James", "Robert", "John", "Michael", "David", "Mary", "Jennifer", "Linda", "Emma", "Olivia"]
    last = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller"]
    return random.choice(first), random.choice(last)


def generate_random_birthday() -> str:
    """随机生成生日，保持在常见成年区间。"""
    year = random.randint(1996, 2006)
    month = random.randint(1, 12)
    day = random.randint(1, 28)
    return f"{year:04d}-{month:02d}-{day:02d}"


class SentinelTokenGenerator:
    """生成 OpenAI Sentinel 所需的 proof-of-work 令牌。"""

    MAX_ATTEMPTS = 500000
    ERROR_PREFIX = "wQ8Lk5FbGpA2NcR9dShT6gYjU7VxZ4D"

    def __init__(self, device_id: Optional[str] = None):
        self.device_id = device_id or str(uuid.uuid4())
        self.requirements_seed = str(random.random())
        self.sid = str(uuid.uuid4())

    @staticmethod
    def _fnv1a_32(text: str) -> str:
        h = 2166136261
        for ch in text:
            h ^= ord(ch)
            h = (h * 16777619) & 0xFFFFFFFF
        h ^= (h >> 16)
        h = (h * 2246822507) & 0xFFFFFFFF
        h ^= (h >> 13)
        h = (h * 3266489909) & 0xFFFFFFFF
        h ^= (h >> 16)
        h &= 0xFFFFFFFF
        return format(h, "08x")

    @staticmethod
    def _base64_encode(data: Any) -> str:
        js = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
        return base64.b64encode(js.encode("utf-8")).decode("ascii")

    def _get_config(self) -> List[Any]:
        now = dt.datetime.now(dt.timezone.utc).strftime("%a %b %d %Y %H:%M:%S GMT+0000 (Coordinated Universal Time)")
        perf_now = random.uniform(1000, 50000)
        time_origin = time.time() * 1000 - perf_now
        return [
            "1920x1080",
            now,
            4294705152,
            random.random(),
            USER_AGENT,
            "https://sentinel.openai.com/sentinel/20260124ceb8/sdk.js",
            None,
            None,
            "en-US",
            "en-US,en",
            random.random(),
            "vendorSub−undefined",
            "location",
            "Object",
            perf_now,
            self.sid,
            "",
            random.choice([4, 8, 12, 16]),
            time_origin,
        ]

    def _run_check(self, start_time: float, seed: str, difficulty: str, config: List[Any], nonce: int) -> Optional[str]:
        config[3] = nonce
        config[9] = round((time.time() - start_time) * 1000)
        data = self._base64_encode(config)
        hash_hex = self._fnv1a_32(seed + data)
        if hash_hex[: len(difficulty)] <= difficulty:
            return data + "~S"
        return None

    def generate_requirements_token(self) -> str:
        cfg = self._get_config()
        cfg[3] = 1
        cfg[9] = round(random.uniform(5, 50))
        return "gAAAAAC" + self._base64_encode(cfg)

    def generate_token(self, seed: Optional[str] = None, difficulty: Optional[str] = None) -> str:
        if seed is None:
            seed = self.requirements_seed
            difficulty = difficulty or "0"
        cfg = self._get_config()
        start = time.time()
        for i in range(self.MAX_ATTEMPTS):
            result = self._run_check(start, seed, difficulty or "0", cfg, i)
            if result:
                return "gAAAAAB" + result
        return "gAAAAAB" + self.ERROR_PREFIX + self._base64_encode(str(None))


def fetch_sentinel_challenge(session: requests.Session, device_id: str, flow: str = "authorize_continue") -> Optional[Dict[str, Any]]:
    """请求 Sentinel challenge，失败时返回 None 以便上层决定是否回退。"""
    gen = SentinelTokenGenerator(device_id=device_id)
    body = {"p": gen.generate_requirements_token(), "id": device_id, "flow": flow}
    headers = {
        "Content-Type": "text/plain;charset=UTF-8",
        "Referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html",
        "User-Agent": USER_AGENT,
        "Origin": "https://sentinel.openai.com",
        "sec-ch-ua": '"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }
    try:
        resp = session.post(
            "https://sentinel.openai.com/backend-api/sentinel/req",
            data=json.dumps(body),
            headers=headers,
            timeout=15,
            verify=False,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def build_sentinel_token(session: requests.Session, device_id: str, flow: str = "authorize_continue") -> Optional[str]:
    """根据 challenge 构造最终 Sentinel token。"""
    challenge = fetch_sentinel_challenge(session, device_id, flow)
    if not challenge:
        return None
    c_value = challenge.get("token", "")
    pow_data = challenge.get("proofofwork", {})
    gen = SentinelTokenGenerator(device_id=device_id)
    if isinstance(pow_data, dict) and pow_data.get("required") and pow_data.get("seed"):
        p_value = gen.generate_token(seed=pow_data.get("seed"), difficulty=pow_data.get("difficulty", "0"))
    else:
        p_value = gen.generate_requirements_token()
    return json.dumps({"p": p_value, "t": "", "c": c_value, "id": device_id, "flow": flow})

class ProtocolRegistrar:
    """保留的协议级注册实现，按页面流程串联 OpenAI 注册步骤。"""

    def __init__(self, proxy: str, logger: logging.Logger):
        self.session = create_session(proxy=proxy)
        self.device_id = str(uuid.uuid4())
        self.logger = logger
        self.sentinel_gen = SentinelTokenGenerator(device_id=self.device_id)
        self.code_verifier: Optional[str] = None
        self.state: Optional[str] = None

    def _build_headers(self, referer: str, with_sentinel: bool = False) -> Dict[str, str]:
        h = dict(COMMON_HEADERS)
        h["referer"] = referer
        h["oai-device-id"] = self.device_id
        h.update(generate_datadog_trace())
        if with_sentinel:
            h["openai-sentinel-token"] = self.sentinel_gen.generate_token()
        return h

    def step0_init_oauth_session(self, email: str, client_id: str, redirect_uri: str) -> bool:
        self.session.cookies.set("oai-did", self.device_id, domain=".auth.openai.com")
        self.session.cookies.set("oai-did", self.device_id, domain="auth.openai.com")

        code_verifier, code_challenge = generate_pkce()
        self.code_verifier = code_verifier
        self.state = secrets.token_urlsafe(32)

        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": "openid profile email offline_access",
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": self.state,
            "screen_hint": "signup",
            "prompt": "login",
        }

        url = f"{OPENAI_AUTH_BASE}/oauth/authorize?{urlencode(params)}"
        try:
            resp = self.session.get(url, headers=NAVIGATE_HEADERS, allow_redirects=True, verify=False, timeout=30)
        except Exception as e:
            self.logger.warning("步骤0a失败: %s", e)
            return False
        if resp.status_code not in (200, 302):
            self.logger.warning(
                "步骤0a失败: OAuth初始化状态码异常 status=%s, url=%s, 响应预览=%s",
                resp.status_code,
                str(resp.url),
                (resp.text or "")[:300].replace("\n", " "),
            )
            return False

        has_login_session = any(c.name == "login_session" for c in self.session.cookies)
        if not has_login_session:
            cookie_names = [c.name for c in self.session.cookies]
            self.logger.warning(
                "步骤0a失败: 未获取 login_session cookie, cookies=%s, status=%s, url=%s, 响应预览=%s",
                cookie_names,
                resp.status_code,
                str(resp.url),
                (resp.text or "")[:300].replace("\n", " "),
            )
            return False

        headers = self._build_headers(f"{OPENAI_AUTH_BASE}/create-account")
        sentinel = build_sentinel_token(self.session, self.device_id, flow="authorize_continue")
        if sentinel:
            headers["openai-sentinel-token"] = sentinel
        try:
            r2 = self.session.post(
                f"{OPENAI_AUTH_BASE}/api/accounts/authorize/continue",
                json={"username": {"kind": "email", "value": email}, "screen_hint": "signup"},
                headers=headers,
                verify=False,
                timeout=30,
            )
            if r2.status_code != 200:
                self.logger.warning(
                    "步骤0b失败: authorize/continue 返回异常 status=%s, email=%s, 响应预览=%s",
                    r2.status_code,
                    email,
                    (r2.text or "")[:300].replace("\n", " "),
                )
            return r2.status_code == 200
        except Exception as e:
            self.logger.warning("步骤0b异常: %s | email=%s", e, email)
            return False

    def step2_register_user(self, email: str, password: str) -> bool:
        headers = self._build_headers(
            f"{OPENAI_AUTH_BASE}/create-account/password",
            with_sentinel=True,
        )
        try:
            resp = self.session.post(
                f"{OPENAI_AUTH_BASE}/api/accounts/user/register",
                json={"username": email, "password": password},
                headers=headers,
                verify=False,
                timeout=30,
            )
            if resp.status_code == 200:
                return True
            if resp.status_code in (301, 302):
                loc = resp.headers.get("Location", "")
                ok_redirect = "email-otp" in loc or "email-verification" in loc
                if not ok_redirect:
                    self.logger.warning(
                        "步骤2失败: register重定向异常 status=%s, location=%s, email=%s",
                        resp.status_code,
                        loc,
                        email,
                    )
                return ok_redirect
            self.logger.warning(
                "步骤2失败: register返回异常 status=%s, email=%s, 响应预览=%s",
                resp.status_code,
                email,
                (resp.text or "")[:300].replace("\n", " "),
            )
            return False
        except Exception as e:
            self.logger.warning("步骤2异常: %s | email=%s", e, email)
            return False

    def step3_send_otp(self) -> bool:
        try:
            h = dict(NAVIGATE_HEADERS)
            h["referer"] = f"{OPENAI_AUTH_BASE}/create-account/password"
            r_send = self.session.get(
                f"{OPENAI_AUTH_BASE}/api/accounts/email-otp/send",
                headers=h,
                verify=False,
                timeout=30,
                allow_redirects=True,
            )
            r_page = self.session.get(
                f"{OPENAI_AUTH_BASE}/email-verification",
                headers=h,
                verify=False,
                timeout=30,
                allow_redirects=True,
            )
            if r_send.status_code >= 400 or r_page.status_code >= 400:
                self.logger.warning(
                    "步骤3告警: 发送OTP或进入验证页状态异常 send=%s page=%s",
                    r_send.status_code,
                    r_page.status_code,
                )
            return True
        except Exception as e:
            self.logger.warning("步骤3异常: %s", e)
            return False

    def step4_validate_otp(self, code: str) -> bool:
        h = self._build_headers(f"{OPENAI_AUTH_BASE}/email-verification")
        try:
            r = self.session.post(
                f"{OPENAI_AUTH_BASE}/api/accounts/email-otp/validate",
                json={"code": code},
                headers=h,
                verify=False,
                timeout=30,
            )
            if r.status_code != 200:
                self.logger.warning(
                    "步骤4失败: OTP验证失败 status=%s, code=%s, 响应预览=%s",
                    r.status_code,
                    code,
                    (r.text or "")[:300].replace("\n", " "),
                )
            return r.status_code == 200
        except Exception as e:
            self.logger.warning("步骤4异常: %s", e)
            return False

    def step5_create_account(self, first_name: str, last_name: str, birthdate: str) -> bool:
        h = self._build_headers(f"{OPENAI_AUTH_BASE}/about-you")
        body = {"name": f"{first_name} {last_name}", "birthdate": birthdate}
        try:
            r = self.session.post(
                f"{OPENAI_AUTH_BASE}/api/accounts/create_account",
                json=body,
                headers=h,
                verify=False,
                timeout=30,
            )
            if r.status_code == 200:
                return True
            if r.status_code == 403 and "sentinel" in r.text.lower():
                self.logger.warning("步骤5告警: create_account 命中sentinel风控，尝试重试")
                h["openai-sentinel-token"] = SentinelTokenGenerator(self.device_id).generate_token()
                rr = self.session.post(
                    f"{OPENAI_AUTH_BASE}/api/accounts/create_account",
                    json=body,
                    headers=h,
                    verify=False,
                    timeout=30,
                )
                if rr.status_code != 200:
                    self.logger.warning(
                        "步骤5失败: sentinel重试后仍失败 status=%s, 响应预览=%s",
                        rr.status_code,
                        (rr.text or "")[:300].replace("\n", " "),
                    )
                return rr.status_code == 200
            if r.status_code not in (301, 302):
                self.logger.warning(
                    "步骤5失败: create_account返回异常 status=%s, 响应预览=%s",
                    r.status_code,
                    (r.text or "")[:300].replace("\n", " "),
                )
            return r.status_code in (301, 302)
        except Exception as e:
            self.logger.warning("步骤5异常: %s", e)
            return False

    def register(
        self,
        email: str,
        worker_domain: str,
        cf_token: str,
        password: str,
        client_id: str,
        redirect_uri: str,
        *,
        email_provider: str = DEFAULT_EMAIL_PROVIDER,
        email_api_key: str = "",
    ) -> bool:
        """执行协议级注册流程，并在验证码阶段访问配置的临时邮箱 provider。"""
        first_name, last_name = generate_random_name()
        birthdate = generate_random_birthday()
        if not self.step0_init_oauth_session(email, client_id, redirect_uri):
            self.logger.warning("注册失败: step0_init_oauth_session | email=%s", email)
            return False
        time.sleep(1)
        if not self.step2_register_user(email, password):
            self.logger.warning("注册失败: step2_register_user | email=%s", email)
            return False
        time.sleep(1)
        if not self.step3_send_otp():
            self.logger.warning("注册失败: step3_send_otp | email=%s", email)
            return False
        mail_session = create_session()
        code = wait_for_verification_code(
            mail_session,
            worker_domain,
            cf_token,
            provider=email_provider,
            api_key=email_api_key,
        )
        if not code:
            self.logger.warning("注册失败: 未收到验证码 | email=%s", email)
            return False
        if not self.step4_validate_otp(code):
            self.logger.warning("注册失败: step4_validate_otp | email=%s", email)
            return False
        time.sleep(1)
        ok = self.step5_create_account(first_name, last_name, birthdate)
        if not ok:
            self.logger.warning("注册失败: step5_create_account | email=%s", email)
        return ok


def codex_exchange_code(
    code: str,
    code_verifier: str,
    oauth_issuer: str,
    oauth_client_id: str,
    oauth_redirect_uri: str,
    proxy: str,
) -> Optional[Dict[str, Any]]:
    """使用授权码换取 access/refresh/id token。"""
    session = create_session(proxy=proxy)
    try:
        resp = session.post(
            f"{oauth_issuer}/oauth/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": oauth_redirect_uri,
                "client_id": oauth_client_id,
                "code_verifier": code_verifier,
            },
            verify=False,
            timeout=60,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data if isinstance(data, dict) else None
        return None
    except Exception:
        return None


def perform_codex_oauth_login_http(
    email: str,
    password: str,
    cf_token: str,
    worker_domain: str,
    oauth_issuer: str,
    oauth_client_id: str,
    oauth_redirect_uri: str,
    proxy: str,
    *,
    email_provider: str = DEFAULT_EMAIL_PROVIDER,
    email_api_key: str = "",
) -> Optional[Dict[str, Any]]:
    """执行 HTTP 版 OAuth 登录，在需要时从临时邮箱读取验证码。"""
    session = create_session(proxy=proxy)
    device_id = str(uuid.uuid4())

    session.cookies.set("oai-did", device_id, domain=".auth.openai.com")
    session.cookies.set("oai-did", device_id, domain="auth.openai.com")

    code_verifier, code_challenge = generate_pkce()
    state = secrets.token_urlsafe(32)

    authorize_params = {
        "response_type": "code",
        "client_id": oauth_client_id,
        "redirect_uri": oauth_redirect_uri,
        "scope": "openid profile email offline_access",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    authorize_url = f"{oauth_issuer}/oauth/authorize?{urlencode(authorize_params)}"

    try:
        session.get(
            authorize_url,
            headers=NAVIGATE_HEADERS,
            allow_redirects=True,
            verify=False,
            timeout=30,
        )
    except Exception:
        return None

    headers = dict(COMMON_HEADERS)
    headers["referer"] = f"{oauth_issuer}/log-in"
    headers["oai-device-id"] = device_id
    headers.update(generate_datadog_trace())

    sentinel_email = build_sentinel_token(session, device_id, flow="authorize_continue")
    if not sentinel_email:
        return None
    headers["openai-sentinel-token"] = sentinel_email

    try:
        resp = session.post(
            f"{oauth_issuer}/api/accounts/authorize/continue",
            json={"username": {"kind": "email", "value": email}},
            headers=headers,
            verify=False,
            timeout=30,
        )
    except Exception:
        return None

    if resp.status_code != 200:
        return None

    headers["referer"] = f"{oauth_issuer}/log-in/password"
    headers.update(generate_datadog_trace())

    sentinel_pwd = build_sentinel_token(session, device_id, flow="password_verify")
    if not sentinel_pwd:
        return None
    headers["openai-sentinel-token"] = sentinel_pwd

    try:
        resp = session.post(
            f"{oauth_issuer}/api/accounts/password/verify",
            json={"password": password},
            headers=headers,
            verify=False,
            timeout=30,
            allow_redirects=False,
        )
    except Exception:
        return None

    if resp.status_code != 200:
        return None

    continue_url = None
    page_type = ""
    try:
        data = resp.json()
        continue_url = str(data.get("continue_url") or "")
        page_type = str(((data.get("page") or {}).get("type")) or "")
    except Exception:
        pass

    if not continue_url:
        return None

    if page_type == "email_otp_verification" or "email-verification" in continue_url:
        if not cf_token:
            return None

        mail_session = create_session(proxy=proxy)
        h_val = dict(COMMON_HEADERS)
        h_val["referer"] = f"{oauth_issuer}/email-verification"
        h_val["oai-device-id"] = device_id
        h_val.update(generate_datadog_trace())

        code = wait_for_verification_code(
            mail_session,
            worker_domain,
            cf_token,
            timeout=120,
            provider=email_provider,
            api_key=email_api_key,
        )
        if not code:
            return None
        try:
            resp_val = session.post(
                f"{oauth_issuer}/api/accounts/email-otp/validate",
                json={"code": code},
                headers=h_val,
                verify=False,
                timeout=30,
            )
            if resp_val.status_code != 200:
                return None
            data = resp_val.json()
            continue_url = str(data.get("continue_url") or continue_url)
            page_type = str(((data.get("page") or {}).get("type")) or page_type)
        except Exception:
            return None

        if "about-you" in continue_url:
            h_about = dict(NAVIGATE_HEADERS)
            h_about["referer"] = f"{oauth_issuer}/email-verification"
            try:
                resp_about = session.get(
                    f"{oauth_issuer}/about-you",
                    headers=h_about,
                    verify=False,
                    timeout=30,
                    allow_redirects=True,
                )
            except Exception:
                return None

            if "consent" in str(resp_about.url) or "organization" in str(resp_about.url):
                continue_url = str(resp_about.url)
            else:
                first_name, last_name = generate_random_name()
                birthdate = generate_random_birthday()

                h_create = dict(COMMON_HEADERS)
                h_create["referer"] = f"{oauth_issuer}/about-you"
                h_create["oai-device-id"] = device_id
                h_create.update(generate_datadog_trace())

                resp_create = session.post(
                    f"{oauth_issuer}/api/accounts/create_account",
                    json={"name": f"{first_name} {last_name}", "birthdate": birthdate},
                    headers=h_create,
                    verify=False,
                    timeout=30,
                )

                if resp_create.status_code == 200:
                    try:
                        data = resp_create.json()
                        continue_url = str(data.get("continue_url") or "")
                    except Exception:
                        pass
                elif resp_create.status_code == 400 and "already_exists" in resp_create.text:
                    continue_url = f"{oauth_issuer}/sign-in-with-chatgpt/codex/consent"

        if "consent" in page_type:
            continue_url = f"{oauth_issuer}/sign-in-with-chatgpt/codex/consent"

        if not continue_url or "email-verification" in continue_url:
            return None

    if continue_url.startswith("/"):
        consent_url = f"{oauth_issuer}{continue_url}"
    else:
        consent_url = continue_url

    def _extract_code_from_url(url: str) -> Optional[str]:
        if not url or "code=" not in url:
            return None
        try:
            return parse_qs(urlparse(url).query).get("code", [None])[0]
        except Exception:
            return None

    def _decode_auth_session(session_obj: requests.Session) -> Optional[Dict[str, Any]]:
        for c in session_obj.cookies:
            if c.name == "oai-client-auth-session":
                val = c.value
                first_part = val.split(".")[0] if "." in val else val
                pad = 4 - len(first_part) % 4
                if pad != 4:
                    first_part += "=" * pad
                try:
                    raw = base64.urlsafe_b64decode(first_part)
                    d = json.loads(raw.decode("utf-8"))
                    return d if isinstance(d, dict) else None
                except Exception:
                    pass
        return None

    def _follow_and_extract_code(session_obj: requests.Session, url: str, max_depth: int = 10) -> Optional[str]:
        if max_depth <= 0:
            return None
        try:
            r = session_obj.get(
                url,
                headers=NAVIGATE_HEADERS,
                verify=False,
                timeout=15,
                allow_redirects=False,
            )
            if r.status_code in (301, 302, 303, 307, 308):
                loc = r.headers.get("Location", "")
                code = _extract_code_from_url(loc)
                if code:
                    return code
                if loc.startswith("/"):
                    loc = f"{oauth_issuer}{loc}"
                return _follow_and_extract_code(session_obj, loc, max_depth - 1)
            if r.status_code == 200:
                return _extract_code_from_url(str(r.url))
        except requests.exceptions.ConnectionError as e:
            m = re.search(r'(https?://localhost[^\s\'"]+)', str(e))
            if m:
                return _extract_code_from_url(m.group(1))
        except Exception:
            pass
        return None

    auth_code = None

    try:
        resp_consent = session.get(
            consent_url,
            headers=NAVIGATE_HEADERS,
            verify=False,
            timeout=30,
            allow_redirects=False,
        )
        if resp_consent.status_code in (301, 302, 303, 307, 308):
            loc = resp_consent.headers.get("Location", "")
            auth_code = _extract_code_from_url(loc)
            if not auth_code:
                auth_code = _follow_and_extract_code(session, loc)
    except requests.exceptions.ConnectionError as e:
        m = re.search(r'(https?://localhost[^\s\'"]+)', str(e))
        if m:
            auth_code = _extract_code_from_url(m.group(1))
    except Exception:
        pass

    if not auth_code:
        session_data = _decode_auth_session(session)
        workspace_id = None
        if session_data:
            workspaces = session_data.get("workspaces", [])
            if isinstance(workspaces, list) and workspaces:
                workspace_id = (workspaces[0] or {}).get("id")

        if workspace_id:
            h_consent = dict(COMMON_HEADERS)
            h_consent["referer"] = consent_url
            h_consent["oai-device-id"] = device_id
            h_consent.update(generate_datadog_trace())

            try:
                resp_ws = session.post(
                    f"{oauth_issuer}/api/accounts/workspace/select",
                    json={"workspace_id": workspace_id},
                    headers=h_consent,
                    verify=False,
                    timeout=30,
                    allow_redirects=False,
                )
                if resp_ws.status_code in (301, 302, 303, 307, 308):
                    loc = resp_ws.headers.get("Location", "")
                    auth_code = _extract_code_from_url(loc)
                    if not auth_code:
                        auth_code = _follow_and_extract_code(session, loc)
                elif resp_ws.status_code == 200:
                    ws_data = resp_ws.json()
                    ws_next = str(ws_data.get("continue_url") or "")
                    ws_page = str(((ws_data.get("page") or {}).get("type")) or "")

                    if "organization" in ws_next or "organization" in ws_page:
                        org_url = ws_next if ws_next.startswith("http") else f"{oauth_issuer}{ws_next}"

                        org_id = None
                        project_id = None
                        ws_orgs = (ws_data.get("data") or {}).get("orgs", []) if isinstance(ws_data, dict) else []
                        if ws_orgs:
                            org_id = (ws_orgs[0] or {}).get("id")
                            projects = (ws_orgs[0] or {}).get("projects", [])
                            if projects:
                                project_id = (projects[0] or {}).get("id")

                        if org_id:
                            body = {"org_id": org_id}
                            if project_id:
                                body["project_id"] = project_id

                            h_org = dict(COMMON_HEADERS)
                            h_org["referer"] = org_url
                            h_org["oai-device-id"] = device_id
                            h_org.update(generate_datadog_trace())

                            resp_org = session.post(
                                f"{oauth_issuer}/api/accounts/organization/select",
                                json=body,
                                headers=h_org,
                                verify=False,
                                timeout=30,
                                allow_redirects=False,
                            )
                            if resp_org.status_code in (301, 302, 303, 307, 308):
                                loc = resp_org.headers.get("Location", "")
                                auth_code = _extract_code_from_url(loc)
                                if not auth_code:
                                    auth_code = _follow_and_extract_code(session, loc)
                            elif resp_org.status_code == 200:
                                org_data = resp_org.json()
                                org_next = str(org_data.get("continue_url") or "")
                                if org_next:
                                    full_next = org_next if org_next.startswith("http") else f"{oauth_issuer}{org_next}"
                                    auth_code = _follow_and_extract_code(session, full_next)
                        else:
                            auth_code = _follow_and_extract_code(session, org_url)
                    else:
                        if ws_next:
                            full_next = ws_next if ws_next.startswith("http") else f"{oauth_issuer}{ws_next}"
                            auth_code = _follow_and_extract_code(session, full_next)
            except Exception:
                pass

    if not auth_code:
        try:
            resp_fallback = session.get(
                consent_url,
                headers=NAVIGATE_HEADERS,
                verify=False,
                timeout=30,
                allow_redirects=True,
            )
            auth_code = _extract_code_from_url(str(resp_fallback.url))
            if not auth_code and resp_fallback.history:
                for hist in resp_fallback.history:
                    loc = hist.headers.get("Location", "")
                    auth_code = _extract_code_from_url(loc)
                    if auth_code:
                        break
        except requests.exceptions.ConnectionError as e:
            m = re.search(r'(https?://localhost[^\s\'"]+)', str(e))
            if m:
                auth_code = _extract_code_from_url(m.group(1))
        except Exception:
            pass

    if not auth_code:
        return None

    return codex_exchange_code(
        auth_code,
        code_verifier,
        oauth_issuer=oauth_issuer,
        oauth_client_id=oauth_client_id,
        oauth_redirect_uri=oauth_redirect_uri,
        proxy=proxy,
    )


def decode_jwt_payload(token: str) -> Dict[str, Any]:
    """读取 access token payload，用于提取过期时间与 account_id。"""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        payload = parts[1]
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += "=" * padding
        decoded = base64.urlsafe_b64decode(payload)
        data = json.loads(decoded)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


class RegisterRuntime:
    """维护补号运行态，包括并发控制、输出路径与邮箱/OAuth 配置。"""

    def __init__(self, conf: Dict[str, Any], target_tokens: int, logger: logging.Logger):
        self.conf = conf
        self.target_tokens = target_tokens
        self.logger = logger

        self.file_lock = threading.Lock()
        self.counter_lock = threading.Lock()
        self.token_success_count = 0
        self.stop_event = threading.Event()

        run_workers = int(pick_conf(conf, "run", "workers", default=1) or 1)
        self.concurrent_workers = max(1, run_workers)
        self.proxy = str(pick_conf(conf, "run", "proxy", default="") or "")
        run_sleep_min = int(pick_conf(conf, "run", "sleep_min", default=5) or 5)
        run_sleep_max = int(pick_conf(conf, "run", "sleep_max", default=30) or 30)
        self.sleep_min = max(1, run_sleep_min)
        self.sleep_max = max(self.sleep_min, run_sleep_max)

        # provider 默认为 mailtm，未显式配置时完全保持现有行为。
        self.email_provider = normalize_email_provider(
            pick_conf(conf, "email", "provider", default=DEFAULT_EMAIL_PROVIDER)
        )
        email_base_default = default_email_base(self.email_provider)
        self.worker_domain = normalize_email_base(
            str(pick_conf(conf, "email", "worker_domain", default=email_base_default) or email_base_default),
            provider=self.email_provider,
        )
        old_domain = str(pick_conf(conf, "email", "email_domain", default="") or "")
        domains = pick_conf(conf, "email", "email_domains", default=None)
        parsed_domains: List[str] = []
        if isinstance(domains, list):
            parsed_domains = [str(x).strip() for x in domains if str(x).strip()]
        if old_domain and not parsed_domains:
            parsed_domains = [old_domain]
        self.email_domains = parsed_domains
        self.admin_password = str(pick_conf(conf, "email", "admin_password", default="") or "")
        self.email_api_key = ""
        if self.email_provider == "duckmail":
            # 优先读取 DuckMail 官方 API Key；仅为兼容旧配置时才回退 admin_password。
            self.email_api_key = str(pick_conf(conf, "email", "api_key", "duckmail_api_key", default="") or "").strip()
            if not self.email_api_key:
                self.email_api_key = self.admin_password
        elif self.email_provider == "cfmail":
            # CFMail 使用 x-custom-auth 访问私有站点；优先读语义更清晰的 site_password/custom_auth，
            # 同时兼容沿用 api_key 字段的旧配置方式。
            self.email_api_key = str(
                pick_conf(conf, "email", "site_password", "custom_auth", "api_key", default="") or ""
            ).strip()

        self.oauth_issuer = str(pick_conf(conf, "oauth", "issuer", default="https://auth.openai.com") or "https://auth.openai.com")
        self.oauth_client_id = str(
            pick_conf(conf, "oauth", "client_id", default="app_EMoamEEZ73f0CkXaXp7hrann") or "app_EMoamEEZ73f0CkXaXp7hrann"
        )
        self.oauth_redirect_uri = str(
            pick_conf(conf, "oauth", "redirect_uri", default="http://localhost:1455/auth/callback")
            or "http://localhost:1455/auth/callback"
        )
        self.oauth_retry_attempts = int(pick_conf(conf, "oauth", "retry_attempts", default=3) or 3)
        self.oauth_retry_backoff_base = float(pick_conf(conf, "oauth", "retry_backoff_base", default=2.0) or 2.0)
        self.oauth_retry_backoff_max = float(pick_conf(conf, "oauth", "retry_backoff_max", default=15.0) or 15.0)
        self.zzz_run = lambda proxy, local_logger, worker_id=0, reusable_candidate=None: run_legacy_register_flow(
            proxy,
            local_logger,
            email_provider=self.email_provider,
            email_base=self.worker_domain,
            email_domains=self.email_domains,
            email_api_key=self.email_api_key,
            oauth_issuer=self.oauth_issuer,
            oauth_client_id=self.oauth_client_id,
            oauth_redirect_uri=self.oauth_redirect_uri,
            run_label=f"worker-{worker_id}" if worker_id else "",
            worker_id=worker_id,
            reused_candidate=reusable_candidate,
        )

        upload_base = str(pick_conf(conf, "upload", "cli_proxy_api_base", "base_url", default="") or "").strip()
        if not upload_base:
            upload_base = str(pick_conf(conf, "clean", "base_url", default="") or "").strip()
        self.cli_proxy_api_base = upload_base.rstrip("/")

        upload_token = str(pick_conf(conf, "upload", "token", "cpa_password", default="") or "").strip()
        if not upload_token:
            upload_token = str(pick_conf(conf, "clean", "token", "cpa_password", default="") or "").strip()
        self.upload_api_token = upload_token

        self.upload_url = f"{self.cli_proxy_api_base}/v0/management/auth-files" if self.cli_proxy_api_base else ""

        output_cfg = conf.get("output")
        if not isinstance(output_cfg, dict):
            output_cfg = {}

        self.save_local = parse_boolish(output_cfg.get("save_local", True), default=True)
        self.save_token_file_local = parse_boolish(output_cfg.get("save_token_file_local", True), default=True)
        self.save_accounts_local = parse_boolish(output_cfg.get("save_accounts_local", True), default=True)
        self.reuse_failed_mail = parse_boolish(output_cfg.get("reuse_failed_mail", True), default=True)
        self.reuse_failed_mail_max_attempts = max(
            1,
            int(output_cfg.get("reuse_failed_mail_max_attempts", 2) or 2),
        )

        self.script_dir = str(resolve_program_dir(__file__))
        self.run_dir = self.script_dir if is_frozen_runtime() else os.getcwd()
        account_dir_value = str(output_cfg.get("account_dir", "account") or "account")
        self.account_dir = account_dir_value if os.path.isabs(account_dir_value) else os.path.join(self.run_dir, account_dir_value)
        if self.save_local or self.save_token_file_local:
            os.makedirs(self.account_dir, exist_ok=True)
        self.fixed_out_dir = os.path.join(self.run_dir, "output_fixed") if (
            self.save_local or self.save_accounts_local or self.reuse_failed_mail
        ) else ""
        if self.fixed_out_dir:
            os.makedirs(self.fixed_out_dir, exist_ok=True)

        if self.save_local:
            self.fixed_out_dir = os.path.join(self.run_dir, "output_fixed")
            self.tokens_parent_dir = self.account_dir
            self.tokens_out_dir = self.account_dir
            self.ak_file = self._resolve_output_path(str(output_cfg.get("ak_file", "ak.txt")))
            self.rk_file = self._resolve_output_path(str(output_cfg.get("rk_file", "rk.txt")))
        else:
            self.tokens_parent_dir = ""
            self.tokens_out_dir = self.account_dir if self.save_token_file_local else ""
            self.ak_file = ""
            self.rk_file = ""

        if self.save_accounts_local:
            self.accounts_file = self._resolve_output_path(str(output_cfg.get("accounts_file", "accounts.txt")))
            self.csv_file = self._resolve_output_path(str(output_cfg.get("csv_file", "registered_accounts.csv")))
            self.details_file = self._resolve_output_path(
                str(output_cfg.get("details_file", "created_accounts_details.csv"))
            )
        else:
            self.accounts_file = ""
            self.csv_file = ""
            self.details_file = ""

        self.failed_task_dir = self._resolve_output_path(str(output_cfg.get("failed_task_dir", "failed_register_tasks")))
        self.reusable_pool_file = self._resolve_output_path(
            str(output_cfg.get("reusable_pool_file", "reusable_failed_accounts.json"))
        )
        if self.failed_task_dir:
            os.makedirs(self.failed_task_dir, exist_ok=True)
        if self.reusable_pool_file:
            ensure_parent_dir(self.reusable_pool_file)

    def build_cwd_token_filename(self, email: str) -> str:
        safe_email = re.sub(r"[\\/]+", "_", (email or "unknown").strip()) or "unknown"
        return os.path.join(self.account_dir, f"{safe_email}.json")

    def sleep_random_interval(self) -> None:
        wait_time = random.randint(self.sleep_min, self.sleep_max)
        self.logger.info("休息 %s 秒...", wait_time)
        time.sleep(wait_time)

    def _resolve_output_path(self, value: str) -> str:
        if os.path.isabs(value):
            return value
        return os.path.join(self.fixed_out_dir, value)

    def _ensure_unique_dir(self, parent_dir: str, base_name: str) -> str:
        os.makedirs(parent_dir, exist_ok=True)

        candidates = [os.path.join(parent_dir, base_name)] + [
            os.path.join(parent_dir, f"{base_name}-{idx}") for idx in range(1, 1000000)
        ]
        for candidate in candidates:
            try:
                os.makedirs(candidate)
                return candidate
            except FileExistsError:
                continue
        raise RuntimeError(f"无法创建唯一目录: {parent_dir}/{base_name}")

    def get_token_success_count(self) -> int:
        with self.counter_lock:
            return self.token_success_count

    def claim_token_slot(self) -> tuple[bool, int]:
        with self.counter_lock:
            if self.token_success_count >= self.target_tokens:
                return False, self.token_success_count
            self.token_success_count += 1
            if self.token_success_count >= self.target_tokens:
                self.stop_event.set()
            return True, self.token_success_count

    def release_token_slot(self) -> None:
        with self.counter_lock:
            if self.token_success_count > 0:
                self.token_success_count -= 1
            if self.token_success_count < self.target_tokens:
                self.stop_event.clear()

    def save_token_json(self, email: str, access_token: str, refresh_token: str = "", id_token: str = "") -> bool:
        try:
            payload = decode_jwt_payload(access_token)
            auth_info = payload.get("https://api.openai.com/auth", {})
            account_id = auth_info.get("chatgpt_account_id", "") if isinstance(auth_info, dict) else ""

            exp_timestamp = payload.get("exp", 0)
            expired_str = ""
            if exp_timestamp:
                exp_dt = dt.datetime.fromtimestamp(exp_timestamp, tz=dt.timezone(dt.timedelta(hours=8)))
                expired_str = exp_dt.strftime("%Y-%m-%dT%H:%M:%S+08:00")

            now = dt.datetime.now(tz=dt.timezone(dt.timedelta(hours=8)))
            token_data = {
                "type": "codex",
                "email": email,
                "expired": expired_str,
                "id_token": id_token or "",
                "account_id": account_id,
                "access_token": access_token,
                "last_refresh": now.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
                "refresh_token": refresh_token or "",
            }

            filename = ""
            if (self.save_local or self.save_token_file_local) and self.account_dir:
                filename = self.build_cwd_token_filename(email)
                with self.file_lock:
                    ensure_parent_dir(filename)
                    with open(filename, "w", encoding="utf-8") as f:
                        json.dump(token_data, f, ensure_ascii=False)

            if self.upload_url and self.upload_api_token:
                if filename:
                    self.upload_token_json(filename)
                else:
                    self.upload_token_data(f"{email}.json", token_data)

            if filename:
                self.logger.info("本地已保存 token 文件到 account 目录: %s", filename)

            return True
        except Exception as e:
            self.logger.warning("保存 Token JSON 失败: %s", e)
            return False

    def save_raw_token_json(self, token_json: str) -> tuple[bool, str]:
        try:
            data = json.loads(token_json)
            if not isinstance(data, dict):
                self.logger.warning("token_json 解析失败：不是对象")
                return False, ""

            email = str(data.get("email") or "").strip()
            if not email:
                self.logger.warning("token_json 缺少 email 字段")
                return False, ""

            access_token = str(data.get("access_token") or "")
            refresh_token = str(data.get("refresh_token") or "")
            if not access_token:
                self.logger.warning("token_json 缺少 access_token 字段")
                return False, ""

            filename = ""
            if self.save_local or self.save_token_file_local:
                filename = self.build_cwd_token_filename(email)
                with self.file_lock:
                    ensure_parent_dir(filename)
                    with open(filename, "w", encoding="utf-8") as f:
                        f.write(token_json)

            if self.save_local:
                with self.file_lock:
                    if access_token:
                        ensure_parent_dir(self.ak_file)
                        with open(self.ak_file, "a", encoding="utf-8") as f:
                            f.write(f"{access_token}\n")
                    if refresh_token:
                        ensure_parent_dir(self.rk_file)
                        with open(self.rk_file, "a", encoding="utf-8") as f:
                            f.write(f"{refresh_token}\n")

                if self.upload_url and self.upload_api_token:
                    self.upload_token_json(filename)
            else:
                if self.upload_url and self.upload_api_token:
                    self.upload_token_data(f"{email}.json", data)

            if filename:
                self.logger.info("本地已保存 token 文件到 account 目录: %s", filename)
            return True, email
        except Exception as e:
            self.logger.warning("保存原始 token_json 失败: %s", e)
            return False, ""

    def upload_token_json(self, filename: str) -> None:
        if not self.upload_url or not self.upload_api_token:
            return
        try:
            s = create_session(proxy=self.proxy)
            with open(filename, "rb") as f:
                files = {"file": (os.path.basename(filename), f, "application/json")}
                headers = {"Authorization": f"Bearer {self.upload_api_token}"}
                resp = s.post(self.upload_url, files=files, headers=headers, verify=False, timeout=30)
                if resp.status_code != 200:
                    self.logger.warning("上传 token 失败: %s %s", resp.status_code, resp.text[:200])
        except Exception as e:
            self.logger.warning("上传 token 异常: %s", e)

    def upload_token_data(self, filename: str, token_data: Dict[str, Any]) -> None:
        if not self.upload_url or not self.upload_api_token:
            return
        try:
            s = create_session(proxy=self.proxy)
            content = json.dumps(token_data, ensure_ascii=False).encode("utf-8")
            files = {"file": (filename, content, "application/json")}
            headers = {"Authorization": f"Bearer {self.upload_api_token}"}
            resp = s.post(self.upload_url, files=files, headers=headers, verify=False, timeout=30)
            if resp.status_code != 200:
                self.logger.warning("上传 token 失败: %s %s", resp.status_code, resp.text[:200])
        except Exception as e:
            self.logger.warning("上传 token 异常: %s", e)

    def save_tokens(self, email: str, tokens: Dict[str, Any]) -> bool:
        access_token = str(tokens.get("access_token") or "")
        refresh_token = str(tokens.get("refresh_token") or "")
        id_token = str(tokens.get("id_token") or "")

        if self.save_local:
            try:
                with self.file_lock:
                    if access_token:
                        ensure_parent_dir(self.ak_file)
                        with open(self.ak_file, "a", encoding="utf-8") as f:
                            f.write(f"{access_token}\n")
                    if refresh_token:
                        ensure_parent_dir(self.rk_file)
                        with open(self.rk_file, "a", encoding="utf-8") as f:
                            f.write(f"{refresh_token}\n")
            except Exception as e:
                self.logger.warning("AK/RK 保存失败: %s", e)
                return False

        if access_token:
            return self.save_token_json(email, access_token, refresh_token, id_token)
        return False

    def save_account(self, email: str, password: str) -> None:
        if not self.save_accounts_local:
            return

        try:
            with self.file_lock:
                ensure_parent_dir(self.accounts_file)
                ensure_parent_dir(self.csv_file)

                with open(self.accounts_file, "a", encoding="utf-8") as f:
                    f.write(f"{email}:{password}\n")

                file_exists = os.path.exists(self.csv_file)
                with open(self.csv_file, "a", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    if not file_exists:
                        writer.writerow(["email", "password", "timestamp"])
                    writer.writerow([email, password, time.strftime("%Y-%m-%d %H:%M:%S")])
        except Exception as e:
            self.logger.warning("保存账号信息失败: %s", e)

    def append_account_detail(
        self,
        *,
        email: str,
        password: str,
        temp_mail_password: str = "",
        provider: str,
        status: str,
        failure_stage: str = "",
        error_detail: str = "",
        worker_id: int = 0,
        elapsed_seconds: float = 0.0,
    ) -> None:
        """将创建出的邮箱账号详情落到本地明细文件，便于排障与回溯。"""
        if not self.save_accounts_local or not email:
            return

        try:
            with self.file_lock:
                ensure_parent_dir(self.details_file)
                self._normalize_details_file_schema()
                file_exists = os.path.exists(self.details_file)
                with open(self.details_file, "a", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    if not file_exists:
                        writer.writerow(ACCOUNT_DETAILS_HEADER)
                    writer.writerow(
                        [
                            time.strftime("%Y-%m-%d %H:%M:%S"),
                            worker_id,
                            email,
                            password,
                            temp_mail_password,
                            provider,
                            self.worker_domain,
                            status,
                            failure_stage,
                            error_detail,
                            f"{elapsed_seconds:.1f}",
                        ]
                    )
        except Exception as e:
            self.logger.warning("保存账号详情失败: %s", e)

    def _normalize_details_file_schema(self) -> None:
        """将旧版账号明细 CSV 迁移到当前列结构，便于后续统一分析。"""
        if not self.details_file or not os.path.exists(self.details_file):
            return

        try:
            with open(self.details_file, "r", newline="", encoding="utf-8") as f:
                rows = list(csv.reader(f))
            if not rows:
                return

            header = rows[0]
            if header == ACCOUNT_DETAILS_HEADER:
                return

            index_map = {name: idx for idx, name in enumerate(header)}
            migrated_rows = [ACCOUNT_DETAILS_HEADER]
            for row in rows[1:]:
                migrated_rows.append(
                    [
                        row[index_map["timestamp"]] if "timestamp" in index_map and len(row) > index_map["timestamp"] else "",
                        row[index_map["worker_id"]] if "worker_id" in index_map and len(row) > index_map["worker_id"] else "",
                        row[index_map["email"]] if "email" in index_map and len(row) > index_map["email"] else "",
                        row[index_map["password"]] if "password" in index_map and len(row) > index_map["password"] else "",
                        row[index_map["temp_mail_password"]] if "temp_mail_password" in index_map and len(row) > index_map["temp_mail_password"] else "",
                        row[index_map["provider"]] if "provider" in index_map and len(row) > index_map["provider"] else "",
                        row[index_map["email_api_base"]] if "email_api_base" in index_map and len(row) > index_map["email_api_base"] else "",
                        row[index_map["status"]] if "status" in index_map and len(row) > index_map["status"] else "",
                        row[index_map["failure_stage"]] if "failure_stage" in index_map and len(row) > index_map["failure_stage"] else "",
                        row[index_map["error_detail"]] if "error_detail" in index_map and len(row) > index_map["error_detail"] else "",
                        row[index_map["elapsed_seconds"]] if "elapsed_seconds" in index_map and len(row) > index_map["elapsed_seconds"] else "",
                    ]
                )

            with open(self.details_file, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerows(migrated_rows)
        except Exception as e:
            self.logger.warning("迁移账号详情文件结构失败: %s", e)

    def build_task_trace(self, *, worker_id: int = 0, reused_candidate: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """构造当前 runtime 下的任务 trace，用于兜底异常落盘。"""
        return build_register_task_trace(
            worker_id=worker_id,
            run_label=f"worker-{worker_id}" if worker_id else "",
            proxy=self.proxy or None,
            email_provider=self.email_provider,
            email_base=self.worker_domain,
            email_domains=self.email_domains,
            email_api_key=self.email_api_key,
            oauth_issuer=self.oauth_issuer,
            oauth_client_id=self.oauth_client_id,
            oauth_redirect_uri=self.oauth_redirect_uri,
            reused_candidate=reused_candidate,
        )

    def _read_reusable_candidates_locked(self) -> List[Dict[str, Any]]:
        """在 file_lock 内读取失败邮箱复用池。"""
        if not self.reusable_pool_file or not os.path.exists(self.reusable_pool_file):
            return []
        try:
            with open(self.reusable_pool_file, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            return []
        if isinstance(payload, dict):
            candidates = payload.get("candidates") or []
        elif isinstance(payload, list):
            candidates = payload
        else:
            candidates = []
        return [row for row in candidates if isinstance(row, dict)]

    def _write_reusable_candidates_locked(self, candidates: List[Dict[str, Any]]) -> None:
        """在 file_lock 内写回复用池。"""
        if not self.reusable_pool_file:
            return
        ensure_parent_dir(self.reusable_pool_file)
        payload = {
            "version": 1,
            "updated_at": trace_now_text(),
            "candidates": candidates,
        }
        with open(self.reusable_pool_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def claim_reusable_candidate(self, worker_id: int = 0) -> Optional[Dict[str, Any]]:
        """从失败邮箱复用池中取一个最优候选，供本次注册任务优先使用。"""
        if not self.reuse_failed_mail:
            return None

        chosen: Optional[Dict[str, Any]] = None
        with self.file_lock:
            candidates = self._read_reusable_candidates_locked()
            cleaned: List[Dict[str, Any]] = []
            best_idx: Optional[int] = None
            best_key: Optional[Tuple[int, int, int]] = None

            for original_idx, candidate in enumerate(candidates):
                email = str(candidate.get("email") or "").strip()
                temp_password = str(candidate.get("temp_mail_password") or "").strip()
                reuse_count = int(candidate.get("reuse_count") or 0)
                if not email or not temp_password:
                    continue
                if reuse_count >= self.reuse_failed_mail_max_attempts:
                    self.logger.info(
                        "[worker-%s] 失败邮箱达到复用上限，移出复用池: %s | reuse_count=%s",
                        worker_id,
                        email,
                        reuse_count,
                    )
                    continue

                cleaned.append(candidate)
                failure_stage = str(candidate.get("failure_stage") or "").strip()
                priority = 2
                if str(candidate.get("oauth_only_hint")).lower() in {"1", "true", "yes"} or failure_stage in {
                    "legacy_oauth",
                    "normalize_token_json",
                    "save_raw_token_json",
                }:
                    priority = 0
                elif failure_stage in {"legacy_register", "init_legacy_register", "load_legacy_module"}:
                    priority = 1

                local_idx = len(cleaned) - 1
                key = (priority, reuse_count, -original_idx)
                if best_key is None or key < best_key:
                    best_key = key
                    best_idx = local_idx

            if best_idx is not None:
                chosen = cleaned.pop(best_idx)
            if cleaned or candidates:
                self._write_reusable_candidates_locked(cleaned)

        if chosen:
            self.logger.info(
                "[worker-%s] 命中失败邮箱复用: %s | 来源阶段=%s | reuse_count=%s",
                worker_id,
                chosen.get("email"),
                chosen.get("failure_stage"),
                chosen.get("reuse_count"),
            )
        return chosen

    def push_reusable_candidate(self, candidate: Optional[Dict[str, Any]], worker_id: int = 0) -> None:
        """将失败邮箱重新放回复用池，供后续任务优先尝试。"""
        if not self.reuse_failed_mail or not candidate:
            return

        email = str(candidate.get("email") or "").strip()
        temp_password = str(candidate.get("temp_mail_password") or "").strip()
        reuse_count = int(candidate.get("reuse_count") or 0)
        if not email or not temp_password:
            return
        if reuse_count >= self.reuse_failed_mail_max_attempts:
            self.logger.info(
                "[worker-%s] 失败邮箱已达到复用上限，不再写回复用池: %s | reuse_count=%s",
                worker_id,
                email,
                reuse_count,
            )
            return

        candidate = dict(candidate)
        candidate["updated_at"] = trace_now_text()

        with self.file_lock:
            candidates = self._read_reusable_candidates_locked()
            deduped = [
                row for row in candidates if str(row.get("email") or "").strip().lower() != email.lower()
            ]
            deduped.append(candidate)
            self._write_reusable_candidates_locked(deduped)

        self.logger.info(
            "[worker-%s] 已回收失败邮箱到复用池: %s | stage=%s | reuse_count=%s",
            worker_id,
            email,
            candidate.get("failure_stage"),
            reuse_count,
        )

    def record_failed_register_task(
        self,
        trace: Optional[Dict[str, Any]],
        *,
        worker_id: int = 0,
        override_stage: str = "",
        override_detail: str = "",
    ) -> str:
        """将失败任务全链路信息写入本地 JSON，并尝试回收失败邮箱。"""
        if not trace:
            return ""

        try:
            snapshot = json.loads(json.dumps(trace, ensure_ascii=False, default=str))
        except Exception:
            snapshot = dict(trace)

        stage = str(override_stage or snapshot.get("failure_stage") or "unknown").strip() or "unknown"
        detail = str(override_detail or snapshot.get("failure_detail") or "").strip()
        snapshot = finalize_register_task_trace(
            snapshot,
            status="failed",
            failure_stage=stage,
            failure_detail=detail,
        )

        temp_mail = snapshot.get("temp_mail_account")
        email = temp_mail.get("email") if isinstance(temp_mail, dict) else ""
        filename = "_".join(
            [
                dt.datetime.now().strftime("%Y%m%d_%H%M%S"),
                sanitize_trace_component(snapshot.get("task_id")),
                sanitize_trace_component(email),
                sanitize_trace_component(stage),
            ]
        ) + ".json"
        trace_path = os.path.join(self.failed_task_dir, filename) if self.failed_task_dir else os.path.join(self.run_dir, filename)
        snapshot["trace_file"] = trace_path

        with self.file_lock:
            ensure_parent_dir(trace_path)
            with open(trace_path, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, ensure_ascii=False, indent=2)

        self.logger.warning("[worker-%s] 已写入失败任务全链路缓存: %s", worker_id, trace_path)

        candidate = build_reusable_failed_mail_candidate(snapshot)
        if candidate:
            candidate["source_trace_file"] = trace_path
            self.push_reusable_candidate(candidate, worker_id=worker_id)
        else:
            self.logger.info("[worker-%s] 本次失败缺少可复用邮箱信息，不加入复用池", worker_id)
        return trace_path

    def collect_token_emails(self) -> set[str]:
        emails = set()
        if not os.path.isdir(self.tokens_out_dir):
            return emails
        for name in os.listdir(self.tokens_out_dir):
            if not name.endswith(".json"):
                continue
            path = os.path.join(self.tokens_out_dir, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                email = data.get("email") or name[:-5]
                if email:
                    emails.add(str(email))
            except Exception:
                continue
        return emails

    def reconcile_account_outputs_from_tokens(self) -> int:
        if not self.save_accounts_local:
            return 0
        if not self.save_local:
            if not os.path.exists(self.accounts_file):
                return 0
            try:
                with open(self.accounts_file, "r", encoding="utf-8") as f:
                    return sum(1 for line in f if line.strip())
            except Exception:
                return 0

        token_emails = self.collect_token_emails()

        pwd_map: Dict[str, str] = {}
        if os.path.exists(self.accounts_file):
            try:
                with open(self.accounts_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or ":" not in line:
                            continue
                        email, pwd = line.split(":", 1)
                        pwd_map[email] = pwd
            except Exception:
                pass

        ordered_emails = sorted(token_emails)
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

        with self.file_lock:
            ensure_parent_dir(self.accounts_file)
            ensure_parent_dir(self.csv_file)

            with open(self.accounts_file, "w", encoding="utf-8") as f:
                for email in ordered_emails:
                    f.write(f"{email}:{pwd_map.get(email, '')}\n")

            with open(self.csv_file, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["email", "password", "timestamp"])
                for email in ordered_emails:
                    writer.writerow([email, pwd_map.get(email, ""), timestamp])

        return len(ordered_emails)

    def oauth_login_with_retry(self, email: str, password: str, cf_token: str) -> Optional[Dict[str, Any]]:
        attempts = max(1, self.oauth_retry_attempts)
        for attempt in range(1, attempts + 1):
            if self.stop_event.is_set() and self.get_token_success_count() >= self.target_tokens:
                return None

            self.logger.info("OAuth 尝试 %s/%s: %s", attempt, attempts, email)
            tokens = perform_codex_oauth_login_http(
                email=email,
                password=password,
                cf_token=cf_token,
                worker_domain=self.worker_domain,
                oauth_issuer=self.oauth_issuer,
                oauth_client_id=self.oauth_client_id,
                oauth_redirect_uri=self.oauth_redirect_uri,
                proxy=self.proxy,
                email_provider=self.email_provider,
                email_api_key=self.email_api_key,
            )
            if tokens:
                return tokens
            if attempt < attempts:
                backoff = min(self.oauth_retry_backoff_max, self.oauth_retry_backoff_base ** (attempt - 1))
                jitter = random.uniform(0.2, 0.8)
                time.sleep(backoff + jitter)
        return None


def register_one(runtime: RegisterRuntime, worker_id: int = 0) -> tuple[Optional[str], Optional[bool], float, float]:
    """执行一次补号尝试，并返回邮箱、结果及耗时统计。"""
    if runtime.stop_event.is_set() and runtime.get_token_success_count() >= runtime.target_tokens:
        return None, None, 0.0, 0.0

    t_start = time.time()
    reusable_candidate = runtime.claim_reusable_candidate(worker_id=worker_id)
    try:
        attempt_result = runtime.zzz_run(
            runtime.proxy or None,
            runtime.logger,
            worker_id=worker_id,
            reusable_candidate=reusable_candidate,
        )
    except Exception as e:
        runtime.logger.warning("[worker-%s] zzz.py 执行异常: %s", worker_id, e)
        trace = runtime.build_task_trace(worker_id=worker_id, reused_candidate=reusable_candidate)
        append_register_task_event(trace, "failure", f"runtime_exception: {e}", stage="runtime_exception")
        runtime.record_failed_register_task(
            trace,
            worker_id=worker_id,
            override_stage="runtime_exception",
            override_detail=str(e),
        )
        return None, False, 0.0, time.time() - t_start

    t_total = time.time() - t_start
    t_reg = t_total
    temp_mail_account = attempt_result.temp_mail_account
    result_password = attempt_result.account_password or (temp_mail_account.password if temp_mail_account else "")
    token_json = attempt_result.token_json
    if not token_json:
        trace_path = runtime.record_failed_register_task(
            attempt_result.task_trace,
            worker_id=worker_id,
            override_stage=attempt_result.failure_stage or "unknown",
            override_detail=attempt_result.failure_detail,
        )
        detail_with_trace = attempt_result.failure_detail
        if trace_path:
            detail_with_trace = (
                f"{detail_with_trace} | trace_file={trace_path}" if detail_with_trace else f"trace_file={trace_path}"
            )
        if temp_mail_account:
            runtime.append_account_detail(
                email=temp_mail_account.email,
                password=result_password,
                temp_mail_password=temp_mail_account.password,
                provider=temp_mail_account.provider,
                status="flow_failed",
                failure_stage=attempt_result.failure_stage or "unknown",
                error_detail=detail_with_trace,
                worker_id=worker_id,
                elapsed_seconds=t_total,
            )
            return temp_mail_account.email, False, t_reg, t_total
        return None, False, t_reg, t_total

    email_hint: Optional[str] = temp_mail_account.email if temp_mail_account else None
    try:
        maybe = json.loads(token_json)
        if isinstance(maybe, dict):
            email_hint = str(maybe.get("email") or "").strip() or None
    except Exception:
        pass

    claimed, current = runtime.claim_token_slot()
    if not claimed:
        if temp_mail_account:
            runtime.append_account_detail(
                email=temp_mail_account.email,
                password=result_password,
                temp_mail_password=temp_mail_account.password,
                provider=temp_mail_account.provider,
                status="skipped_target_reached",
                failure_stage="target_reached",
                error_detail=attempt_result.failure_detail,
                worker_id=worker_id,
                elapsed_seconds=t_total,
            )
        return email_hint, None, t_reg, t_total

    saved, email = runtime.save_raw_token_json(token_json)
    if not saved:
        trace_path = runtime.record_failed_register_task(
            attempt_result.task_trace,
            worker_id=worker_id,
            override_stage="save_raw_token_json",
            override_detail=attempt_result.failure_detail or "save_raw_token_json 返回失败",
        )
        detail_with_trace = attempt_result.failure_detail or "save_raw_token_json 返回失败"
        if trace_path:
            detail_with_trace = f"{detail_with_trace} | trace_file={trace_path}"
        if temp_mail_account:
            runtime.append_account_detail(
                email=email or temp_mail_account.email,
                password=result_password,
                temp_mail_password=temp_mail_account.password,
                provider=temp_mail_account.provider,
                status="token_save_failed",
                failure_stage="save_raw_token_json",
                error_detail=detail_with_trace,
                worker_id=worker_id,
                elapsed_seconds=t_total,
            )
        runtime.release_token_slot()
        return email or None, False, t_reg, t_total

    account_password = result_password
    runtime.save_account(email, account_password)
    if temp_mail_account:
        runtime.append_account_detail(
            email=email,
            password=account_password,
            temp_mail_password=temp_mail_account.password,
            provider=temp_mail_account.provider,
            status="success",
            worker_id=worker_id,
            elapsed_seconds=t_total,
        )
    runtime.logger.info(
        "[worker-%s] 全流程成功: %s | 用时 %.1fs | token %s/%s",
        worker_id,
        email,
        t_reg,
        current,
        runtime.target_tokens,
    )
    return email, True, t_reg, t_total


def run_batch_register(conf: Dict[str, Any], target_tokens: int, logger: logging.Logger) -> tuple[int, int, int]:
    """按目标缺口批量补号，支持单线程与并发模式。"""
    if target_tokens <= 0:
        return 0, 0, 0

    runtime = RegisterRuntime(conf=conf, target_tokens=target_tokens, logger=logger)
    workers = runtime.concurrent_workers

    logger.info(
        "开始补号: 目标 token=%s, 并发=%s",
        target_tokens,
        workers,
    )
    if runtime.save_accounts_local:
        logger.info(
            "本地账号输出: accounts=%s, csv=%s, details=%s",
            runtime.accounts_file,
            runtime.csv_file,
            runtime.details_file,
        )
    if runtime.save_local:
        logger.info("本地 token 输出目录: %s", runtime.tokens_out_dir)
    logger.info("失败任务 trace 目录: %s", runtime.failed_task_dir)
    if runtime.reuse_failed_mail:
        logger.info(
            "失败邮箱复用池: %s | 最大复用失败次数=%s",
            runtime.reusable_pool_file,
            runtime.reuse_failed_mail_max_attempts,
        )

    ok = 0
    fail = 0
    skip = 0
    attempts = 0
    reg_times: List[float] = []
    total_times: List[float] = []
    lock = threading.Lock()
    batch_start = time.time()

    if workers == 1:
        while runtime.get_token_success_count() < target_tokens:
            attempts += 1
            email, success, t_reg, t_total = register_one(runtime, worker_id=1)
            if success is True:
                ok += 1
                reg_times.append(t_reg)
                total_times.append(t_total)
            elif success is False:
                fail += 1
            else:
                skip += 1
            logger.info(
                "补号进度: token %s/%s | 【成功:%s】 【失败:%s】 【跳过:%s】 | 用时 %.1fs",
                runtime.get_token_success_count(),
                target_tokens,
                ok,
                fail,
                skip,
                time.time() - batch_start,
            )
            if runtime.get_token_success_count() >= target_tokens:
                break
            runtime.sleep_random_interval()
    else:
        def worker_task(task_index: int, worker_id: int):
            if task_index > 1:
                jitter = random.uniform(0.5, 2.0) * worker_id
                time.sleep(jitter)
            if runtime.stop_event.is_set() and runtime.get_token_success_count() >= target_tokens:
                return task_index, None, None, 0.0, 0.0
            email, success, t_reg, t_total = register_one(runtime, worker_id=worker_id)
            return task_index, email, success, t_reg, t_total

        executor = ThreadPoolExecutor(max_workers=workers)
        futures = {}
        next_task_index = 1

        def submit_one() -> bool:
            nonlocal next_task_index
            remaining = target_tokens - runtime.get_token_success_count()
            if remaining <= 0:
                return False
            if len(futures) >= remaining:
                return False

            wid = ((next_task_index - 1) % workers) + 1
            fut = executor.submit(worker_task, next_task_index, wid)
            futures[fut] = next_task_index
            next_task_index += 1
            return True

        try:
            for _ in range(min(workers, target_tokens)):
                if not submit_one():
                    break

            while futures:
                if runtime.get_token_success_count() >= target_tokens:
                    runtime.stop_event.set()
                    break

                done_set, _ = wait(list(futures.keys()), return_when=FIRST_COMPLETED, timeout=1.0)
                if not done_set:
                    continue

                for fut in done_set:
                    _ = futures.pop(fut, None)
                    attempts += 1
                    try:
                        _, _, success, t_reg, t_total = fut.result()
                    except Exception:
                        success, t_reg, t_total = False, 0.0, 0.0

                    with lock:
                        if success is True:
                            ok += 1
                            reg_times.append(t_reg)
                            total_times.append(t_total)
                        elif success is False:
                            fail += 1
                        else:
                            skip += 1

                        logger.info(
                            "补号进度: token %s/%s | 【成功:%s】 【失败:%s】 【跳过:%s】 | 用时 %.1fs",
                            runtime.get_token_success_count(),
                            target_tokens,
                            ok,
                            fail,
                            skip,
                            time.time() - batch_start,
                        )

                    if runtime.get_token_success_count() < target_tokens:
                        submit_one()
        finally:
            runtime.stop_event.set()
            for f in list(futures.keys()):
                f.cancel()
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                executor.shutdown(wait=False)

    synced = runtime.reconcile_account_outputs_from_tokens()
    elapsed = time.time() - batch_start
    avg_reg = (sum(reg_times) / len(reg_times)) if reg_times else 0
    avg_total = (sum(total_times) / len(total_times)) if total_times else 0
    logger.info(
        "补号完成: token=%s/%s, fail=%s, skip=%s, attempts=%s, elapsed=%.1fs, avg(注册)=%.1fs, avg(总)=%.1fs, 收敛账号=%s",
        runtime.get_token_success_count(),
        target_tokens,
        fail,
        skip,
        attempts,
        elapsed,
        avg_reg,
        avg_total,
        synced,
    )
    return runtime.get_token_success_count(), fail, synced

def build_weekly_limit_state_entry(item: Dict[str, Any], info: Dict[str, Any]) -> Dict[str, Any]:
    """构造单个周限额状态条目。"""
    return {
        "name": str(item.get("name") or item.get("id") or ""),
        "email": str(item.get("account") or item.get("email") or ""),
        "account_id": str(extract_chatgpt_account_id(item) or ""),
        "auth_index": str(item.get("auth_index") or ""),
        "status": str(item.get("status") or ""),
        "status_message": str(item.get("status_message") or ""),
        "source": str(info.get("weekly_limit_source") or ""),
        "scope": str(info.get("weekly_limit_scope") or ""),
        "plan_type": str(info.get("weekly_plan_type") or ""),
        "used_percent": parse_epoch_seconds(info.get("weekly_used_percent")),
        "limit_window_seconds": parse_epoch_seconds(info.get("weekly_limit_window_seconds")),
        "reset_after_seconds": parse_epoch_seconds(info.get("weekly_reset_after_seconds")),
        "reset_at": parse_epoch_seconds(info.get("weekly_reset_at")),
        "reset_at_text": str(info.get("weekly_reset_at_text") or ""),
        "updated_at": trace_now_text(),
    }


def patch_auth_file_disabled(
    base_url: str,
    token: str,
    name: str,
    *,
    disabled: bool,
    timeout: int,
) -> tuple[bool, str]:
    """调用管理端接口更新认证文件 disabled 状态。"""
    if not name:
        return False, "missing name"
    try:
        resp = requests.patch(
            f"{base_url.rstrip('/')}/v0/management/auth-files/status",
            headers={**mgmt_headers(token), "Content-Type": "application/json"},
            json={"name": name, "disabled": bool(disabled)},
            timeout=timeout,
        )
    except Exception as e:
        return False, str(e)
    if resp.status_code != 200:
        return False, f"http {resp.status_code}: {(resp.text or '')[:200]}"
    return True, ""


def recover_weekly_limit_accounts(
    *,
    files: List[Dict[str, Any]],
    conf: Dict[str, Any],
    base_url: str,
    token: str,
    target_type: str,
    timeout: int,
    logger: logging.Logger,
) -> tuple[List[Dict[str, Any]], Dict[str, int], Dict[str, Dict[str, Any]]]:
    """在探测前处理已停用的周限额账号：过期则恢复，未过期继续停用。"""
    report_path = resolve_weekly_limit_report_path(conf)
    state_path = resolve_weekly_limit_state_path(conf)
    state = load_weekly_limit_state(state_path)
    now_ts = int(time.time())
    stats = {
        "reenabled": 0,
        "kept_disabled": 0,
        "disabled_from_status": 0,
        "action_failed": 0,
    }

    seen_names = set()
    for item in files:
        if str(get_item_type(item)).lower() != target_type.lower():
            continue
        name = str(item.get("name") or item.get("id") or "").strip()
        if not name:
            continue
        seen_names.add(name)
        info = merge_weekly_limit_info(item, state.get(name))
        reset_at = parse_epoch_seconds(info.get("weekly_reset_at"))
        disabled_before = bool(item.get("disabled"))

        if disabled_before and info.get("weekly_limit_reached"):
            if reset_at > 0 and now_ts >= reset_at:
                logger.info(
                    "周限额恢复检查: 账号已过恢复时间，准备重新启用 | name=%s email=%s reset_at=%s",
                    name,
                    item.get("account") or item.get("email") or "",
                    info.get("weekly_reset_at_text") or reset_at,
                )
                ok, error = patch_auth_file_disabled(
                    base_url=base_url,
                    token=token,
                    name=name,
                    disabled=False,
                    timeout=max(10, timeout),
                )
                row = {
                    "name": name,
                    "email": item.get("account") or item.get("email") or "",
                    "account_id": extract_chatgpt_account_id(item) or "",
                    "auth_index": item.get("auth_index") or "",
                    "action": "reenable_after_weekly_reset" if ok else "reenable_after_weekly_reset_failed",
                    "disabled_before": True,
                    "disabled_after": False if ok else True,
                    "limit_source": info.get("weekly_limit_source") or "",
                    "limit_scope": info.get("weekly_limit_scope") or "",
                    "plan_type": info.get("weekly_plan_type") or "",
                    "used_percent": info.get("weekly_used_percent") or "",
                    "limit_window_seconds": info.get("weekly_limit_window_seconds") or "",
                    "reset_after_seconds": max(0, reset_at - now_ts) if reset_at > 0 else "",
                    "reset_at": reset_at or "",
                    "reset_at_text": info.get("weekly_reset_at_text") or "",
                    "status": item.get("status") or "",
                    "status_message": trace_preview(item.get("status_message") or "", limit=800),
                    "error_detail": error,
                }
                append_weekly_limit_report(report_path, row)
                if ok:
                    item["disabled"] = False
                    item["status"] = "active"
                    state.pop(name, None)
                    stats["reenabled"] += 1
                else:
                    stats["action_failed"] += 1
                    logger.warning("周限额账号重新启用失败: name=%s detail=%s", name, error)
            else:
                stats["kept_disabled"] += 1
                logger.info(
                    "周限额恢复检查: 账号仍处于冷却期，保持停用 | name=%s email=%s reset_at=%s",
                    name,
                    item.get("account") or item.get("email") or "",
                    info.get("weekly_reset_at_text") or reset_at or "-",
                )
                if info.get("weekly_limit_reached"):
                    state[name] = build_weekly_limit_state_entry(item, info)
            continue

        if (not disabled_before) and info.get("weekly_limit_reached") and (reset_at <= 0 or reset_at > now_ts):
            logger.info(
                "周限额预检查: 账号已命中周限额，准备停用 | name=%s email=%s reset_at=%s",
                name,
                item.get("account") or item.get("email") or "",
                info.get("weekly_reset_at_text") or reset_at or "-",
            )
            ok, error = patch_auth_file_disabled(
                base_url=base_url,
                token=token,
                name=name,
                disabled=True,
                timeout=max(10, timeout),
            )
            row = {
                "name": name,
                "email": item.get("account") or item.get("email") or "",
                "account_id": extract_chatgpt_account_id(item) or "",
                "auth_index": item.get("auth_index") or "",
                "action": "disable_known_weekly_limit" if ok else "disable_known_weekly_limit_failed",
                "disabled_before": False,
                "disabled_after": True if ok else False,
                "limit_source": info.get("weekly_limit_source") or "",
                "limit_scope": info.get("weekly_limit_scope") or "",
                "plan_type": info.get("weekly_plan_type") or "",
                "used_percent": info.get("weekly_used_percent") or "",
                "limit_window_seconds": info.get("weekly_limit_window_seconds") or "",
                "reset_after_seconds": max(0, reset_at - now_ts) if reset_at > 0 else "",
                "reset_at": reset_at or "",
                "reset_at_text": info.get("weekly_reset_at_text") or "",
                "status": item.get("status") or "",
                "status_message": trace_preview(item.get("status_message") or "", limit=800),
                "error_detail": error,
            }
            append_weekly_limit_report(report_path, row)
            if ok:
                item["disabled"] = True
                state[name] = build_weekly_limit_state_entry(item, info)
                stats["disabled_from_status"] += 1
            else:
                item["_weekly_limit_pending"] = True
                stats["action_failed"] += 1
                logger.warning("周限额账号停用失败: name=%s detail=%s", name, error)

    stale_names = [name for name in state.keys() if name not in seen_names]
    for name in stale_names:
        state.pop(name, None)
    save_weekly_limit_state(state_path, state)
    return files, stats, state


def apply_weekly_limit_status_actions(
    *,
    probe_results: List[Dict[str, Any]],
    conf: Dict[str, Any],
    base_url: str,
    token: str,
    timeout: int,
    logger: logging.Logger,
    weekly_state: Dict[str, Dict[str, Any]],
) -> Dict[str, int]:
    """对本次探测中命中周限额的账号执行停用并写入本地记录。"""
    report_path = resolve_weekly_limit_report_path(conf)
    state_path = resolve_weekly_limit_state_path(conf)
    stats = {
        "weekly_limit_hits": 0,
        "disabled_now": 0,
        "already_pending": 0,
        "disable_failed": 0,
    }
    now_ts = int(time.time())

    for item in probe_results:
        if not item.get("weekly_limit_reached"):
            continue
        stats["weekly_limit_hits"] += 1
        name = str(item.get("name") or "").strip()
        email = str(item.get("account") or item.get("email") or "").strip()
        reset_at = parse_epoch_seconds(item.get("weekly_reset_at"))
        if reset_at > 0 and reset_at <= now_ts:
            logger.info(
                "周限额命中但已过恢复时间，跳过停用 | name=%s email=%s reset_at=%s",
                name,
                email,
                item.get("weekly_reset_at_text") or reset_at,
            )
            continue
        if bool(item.get("disabled")) or item.get("_weekly_limit_pending"):
            stats["already_pending"] += 1
            continue

        logger.info(
            "周限额命中: 准备停用账号 | name=%s email=%s scope=%s reset_at=%s used=%s%%",
            name,
            email,
            item.get("weekly_limit_scope") or "",
            item.get("weekly_reset_at_text") or reset_at or "-",
            item.get("weekly_used_percent") or "",
        )
        ok, error = patch_auth_file_disabled(
            base_url=base_url,
            token=token,
            name=name,
            disabled=True,
            timeout=max(10, timeout),
        )
        row = {
            "name": name,
            "email": email,
            "account_id": extract_chatgpt_account_id(item) or "",
            "auth_index": item.get("auth_index") or "",
            "action": "disable_after_weekly_limit_probe" if ok else "disable_after_weekly_limit_probe_failed",
            "disabled_before": bool(item.get("disabled")),
            "disabled_after": True if ok else False,
            "limit_source": item.get("weekly_limit_source") or "",
            "limit_scope": item.get("weekly_limit_scope") or "",
            "plan_type": item.get("weekly_plan_type") or "",
            "used_percent": item.get("weekly_used_percent") or "",
            "limit_window_seconds": item.get("weekly_limit_window_seconds") or "",
            "reset_after_seconds": item.get("weekly_reset_after_seconds") or max(0, reset_at - now_ts),
            "reset_at": reset_at or "",
            "reset_at_text": item.get("weekly_reset_at_text") or "",
            "status": item.get("status") or "",
            "status_message": trace_preview(item.get("status_message") or "", limit=800),
            "error_detail": error,
        }
        append_weekly_limit_report(report_path, row)
        if ok:
            item["disabled"] = True
            weekly_state[name] = build_weekly_limit_state_entry(item, item)
            stats["disabled_now"] += 1
        else:
            item["_weekly_limit_pending"] = True
            stats["disable_failed"] += 1
            logger.warning("周限额账号停用失败: name=%s email=%s detail=%s", name, email, error)

    save_weekly_limit_state(state_path, weekly_state)
    return stats


def build_local_token_index(account_dir: str) -> Dict[str, Dict[str, str]]:
    """扫描本地 account 目录，建立 email/account_id/name 到文件路径的索引。"""
    by_email: Dict[str, str] = {}
    by_account_id: Dict[str, str] = {}
    by_name: Dict[str, str] = {}
    if not account_dir or not os.path.isdir(account_dir):
        return {"by_email": by_email, "by_account_id": by_account_id, "by_name": by_name}

    for path in Path(account_dir).glob("*.json"):
        by_name[path.name] = str(path)
        try:
            data = json.loads(path.read_text("utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        email = str(data.get("email") or "").strip().lower()
        account_id = str(data.get("account_id") or "").strip()
        if email:
            by_email[email] = str(path)
        if account_id:
            by_account_id[account_id] = str(path)
    return {"by_email": by_email, "by_account_id": by_account_id, "by_name": by_name}


def find_local_token_file(item: Dict[str, Any], account_dir: str, token_index: Dict[str, Dict[str, str]]) -> str:
    """尽量通过 email / account_id / name 找到本地 token 文件。"""
    email = str(item.get("account") or item.get("email") or "").strip().lower()
    if email and token_index["by_email"].get(email):
        return token_index["by_email"][email]

    account_id = str(item.get("chatgpt_account_id") or item.get("account_id") or item.get("accountId") or "").strip()
    if account_id and token_index["by_account_id"].get(account_id):
        return token_index["by_account_id"][account_id]

    name = str(item.get("name") or "").strip()
    if name:
        if token_index["by_name"].get(name):
            return token_index["by_name"][name]
        if not name.endswith(".json") and token_index["by_name"].get(f"{name}.json"):
            return token_index["by_name"][f"{name}.json"]

    if email:
        candidate = os.path.join(account_dir, f"{email}.json")
        if os.path.exists(candidate):
            return candidate
    return ""


def refresh_codex_token(
    *,
    refresh_token: str,
    oauth_issuer: str,
    oauth_client_id: str,
    proxy: str = "",
    timeout: int = 60,
) -> tuple[Optional[int], Optional[Dict[str, Any]], str]:
    """使用 refresh_token 刷新 OpenAI OAuth token。"""
    if not refresh_token:
        return None, None, "missing refresh_token"

    session = create_session(proxy=proxy)
    try:
        resp = session.post(
            f"{oauth_issuer.rstrip('/')}/oauth/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": oauth_client_id,
            },
            verify=False,
            timeout=timeout,
        )
    except Exception as e:
        return None, None, str(e)

    try:
        payload = resp.json()
    except Exception:
        payload = None

    if resp.status_code != 200:
        detail = ""
        if isinstance(payload, dict):
            detail = str(payload.get("error_description") or payload.get("error") or payload.get("message") or "").strip()
        if not detail:
            detail = (resp.text or "")[:300]
        return resp.status_code, None, detail or f"http {resp.status_code}"

    if not isinstance(payload, dict) or not str(payload.get("access_token") or "").strip():
        return resp.status_code, None, "refresh 响应缺少 access_token"
    return resp.status_code, payload, ""


def probe_account_sync(
    *,
    base_url: str,
    token: str,
    item: Dict[str, Any],
    user_agent: str,
    timeout: int,
    retries: int,
) -> Dict[str, Any]:
    """同步探测单个账号，用于 refresh 后二次确认是否恢复。"""
    auth_index = item.get("auth_index")
    name = item.get("name") or item.get("id")
    account = item.get("account") or item.get("email") or ""
    result = {
        "name": name,
        "account": account,
        "auth_index": auth_index,
        "status_code": None,
        "invalid_401": False,
        "error": None,
        "weekly_limit_reached": False,
        "weekly_limit_source": "",
        "weekly_limit_scope": "",
        "weekly_plan_type": "",
        "weekly_used_percent": 0,
        "weekly_limit_window_seconds": 0,
        "weekly_reset_after_seconds": 0,
        "weekly_reset_at": 0,
        "weekly_reset_at_text": "",
    }
    if not auth_index:
        result["error"] = "missing auth_index"
        return result

    payload = build_probe_payload(str(auth_index), user_agent, extract_chatgpt_account_id(item))
    for attempt in range(retries + 1):
        try:
            resp = requests.post(
                f"{base_url}/v0/management/api-call",
                headers={**mgmt_headers(token), "Content-Type": "application/json"},
                json=payload,
                timeout=timeout,
            )
            text = resp.text
            if resp.status_code >= 400:
                raise RuntimeError(f"management api-call http {resp.status_code}: {text[:200]}")
            data = safe_json_text(text)
            sc = data.get("status_code")
            result["status_code"] = sc
            result["invalid_401"] = sc == 401
            result.update(extract_weekly_limit_from_usage_body(decode_management_body(data)))
            if sc is None:
                result["error"] = "missing status_code in api-call response"
            return result
        except Exception as e:
            result["error"] = str(e)
            if attempt >= retries:
                return result
    return result


def upload_refreshed_token_file(base_url: str, token: str, token_path: str, proxy: str = "") -> tuple[bool, str]:
    """将刷新后的 token 文件重新上传到管理端。"""
    if not token_path or not os.path.exists(token_path):
        return False, "token file not found"
    try:
        session = create_session(proxy=proxy)
        with open(token_path, "rb") as f:
            files = {"file": (os.path.basename(token_path), f, "application/json")}
            resp = session.post(
                f"{base_url.rstrip('/')}/v0/management/auth-files",
                files=files,
                headers={"Authorization": f"Bearer {token}"},
                verify=False,
                timeout=30,
            )
        if resp.status_code != 200:
            return False, f"http {resp.status_code}: {(resp.text or '')[:200]}"
        return True, ""
    except Exception as e:
        return False, str(e)


def try_refresh_invalid_accounts(
    *,
    invalid_items: List[Dict[str, Any]],
    conf: Dict[str, Any],
    base_url: str,
    token: str,
    user_agent: str,
    timeout: int,
    retries: int,
    logger: logging.Logger,
) -> tuple[List[str], int, int]:
    """对命中的 401 账号优先尝试 refresh，返回待删除名字列表、恢复数和不确定数。"""
    if not invalid_items:
        return [], 0, 0

    oauth_issuer = str(pick_conf(conf, "oauth", "issuer", default=OPENAI_AUTH_BASE) or OPENAI_AUTH_BASE).rstrip("/")
    oauth_client_id = str(
        pick_conf(conf, "oauth", "client_id", default="app_EMoamEEZ73f0CkXaXp7hrann") or "app_EMoamEEZ73f0CkXaXp7hrann"
    ).strip()
    proxy = str(pick_conf(conf, "run", "proxy", default="") or "")
    output_cfg = conf.get("output")
    if not isinstance(output_cfg, dict):
        output_cfg = {}
    account_dir_value = str(output_cfg.get("account_dir", "account") or "account")
    account_dir = account_dir_value if os.path.isabs(account_dir_value) else os.path.join(os.getcwd(), account_dir_value)
    token_index = build_local_token_index(account_dir)
    report_path = resolve_refresh_report_path(conf)

    names_to_delete: List[str] = []
    recovered = 0
    kept_unknown = 0

    for item in invalid_items:
        email = str(item.get("account") or item.get("email") or "").strip()
        account_id = str(extract_chatgpt_account_id(item) or "").strip()
        result_row = {
            "name": str(item.get("name") or ""),
            "email": email,
            "account_id": account_id,
            "auth_index": str(item.get("auth_index") or ""),
            "local_token_file": "",
            "has_local_token": False,
            "has_refresh_token": False,
            "refresh_http_status": "",
            "reprobe_status": "",
            "action": "",
            "error_detail": "",
        }

        token_path = find_local_token_file(item, account_dir, token_index)
        result_row["local_token_file"] = token_path
        if not token_path:
            result_row["action"] = "delete_missing_local_token"
            result_row["error_detail"] = "未找到本地 token 文件，无法 refresh"
            logger.warning("401账号无法 refresh，缺少本地 token 文件: name=%s email=%s", item.get("name"), email)
            names_to_delete.append(str(item.get("name") or ""))
            append_refresh_report(report_path, result_row)
            continue

        result_row["has_local_token"] = True
        try:
            old_data = json.loads(Path(token_path).read_text("utf-8"))
        except Exception as e:
            result_row["action"] = "delete_invalid_local_token"
            result_row["error_detail"] = f"本地 token 文件解析失败: {e}"
            logger.warning("401账号本地 token 文件解析失败: %s", token_path)
            names_to_delete.append(str(item.get("name") or ""))
            append_refresh_report(report_path, result_row)
            continue

        refresh_token = str((old_data or {}).get("refresh_token") or "").strip()
        result_row["has_refresh_token"] = bool(refresh_token)
        if not refresh_token:
            result_row["action"] = "delete_missing_refresh_token"
            result_row["error_detail"] = "本地 token 文件缺少 refresh_token"
            logger.warning("401账号缺少 refresh_token，只能删除: %s", token_path)
            names_to_delete.append(str(item.get("name") or ""))
            append_refresh_report(report_path, result_row)
            continue

        logger.info("401账号尝试 refresh: name=%s email=%s file=%s", item.get("name"), email, token_path)
        refresh_status, refreshed_tokens, refresh_error = refresh_codex_token(
            refresh_token=refresh_token,
            oauth_issuer=oauth_issuer,
            oauth_client_id=oauth_client_id,
            proxy=proxy,
            timeout=max(30, timeout * 3),
        )
        result_row["refresh_http_status"] = refresh_status if refresh_status is not None else ""
        if not refreshed_tokens:
            result_row["action"] = "delete_refresh_failed"
            result_row["error_detail"] = refresh_error or "refresh 返回空"
            logger.warning("401账号 refresh 失败: name=%s email=%s detail=%s", item.get("name"), email, refresh_error)
            names_to_delete.append(str(item.get("name") or ""))
            append_refresh_report(report_path, result_row)
            continue

        try:
            refreshed_json = build_standard_token_json(email, refreshed_tokens, previous_data=old_data)
            Path(token_path).write_text(refreshed_json, encoding="utf-8")
        except Exception as e:
            result_row["action"] = "delete_refresh_write_failed"
            result_row["error_detail"] = f"refresh 成功但本地写回失败: {e}"
            logger.warning("401账号 refresh 后写回本地 token 失败: %s", token_path)
            names_to_delete.append(str(item.get("name") or ""))
            append_refresh_report(report_path, result_row)
            continue

        upload_ok, upload_error = upload_refreshed_token_file(base_url, token, token_path, proxy=proxy)
        if not upload_ok:
            result_row["action"] = "delete_upload_failed"
            result_row["error_detail"] = f"refresh 成功但上传失败: {upload_error}"
            logger.warning("401账号 refresh 后上传失败: name=%s email=%s detail=%s", item.get("name"), email, upload_error)
            names_to_delete.append(str(item.get("name") or ""))
            append_refresh_report(report_path, result_row)
            continue

        reprobe = probe_account_sync(
            base_url=base_url,
            token=token,
            item=item,
            user_agent=user_agent,
            timeout=timeout,
            retries=retries,
        )
        result_row["reprobe_status"] = reprobe.get("status_code") if reprobe.get("status_code") is not None else ""
        if reprobe.get("invalid_401"):
            result_row["action"] = "delete_still_401"
            result_row["error_detail"] = reprobe.get("error") or "refresh 后复测仍为 401"
            logger.warning("401账号 refresh 后仍然 401: name=%s email=%s", item.get("name"), email)
            names_to_delete.append(str(item.get("name") or ""))
        elif reprobe.get("status_code") is None and reprobe.get("error"):
            result_row["action"] = "kept_refresh_reprobe_unknown"
            result_row["error_detail"] = reprobe.get("error") or ""
            kept_unknown += 1
            logger.warning("401账号 refresh 后复测不确定，暂不删除: name=%s email=%s detail=%s", item.get("name"), email, reprobe.get("error"))
        else:
            result_row["action"] = "kept_after_refresh"
            recovered += 1
            logger.info("401账号 refresh 恢复成功: name=%s email=%s reprobe_status=%s", item.get("name"), email, reprobe.get("status_code"))

        append_refresh_report(report_path, result_row)

    return names_to_delete, recovered, kept_unknown


def fetch_auth_files(base_url: str, token: str, timeout: int) -> List[Dict[str, Any]]:
    """拉取管理端账号文件列表。"""
    resp = requests.get(f"{base_url}/v0/management/auth-files", headers=mgmt_headers(token), timeout=timeout)
    resp.raise_for_status()
    raw = resp.json()
    data = raw if isinstance(raw, dict) else {}
    files = data.get("files", [])
    return files if isinstance(files, list) else []


def build_probe_payload(auth_index: str, user_agent: str, chatgpt_account_id: Optional[str] = None) -> Dict[str, Any]:
    """构造管理端透传调用 payload，用于探测账号是否已 401。"""
    call_header = {
        "Authorization": "Bearer your-management-token",
        "Content-Type": "application/json",
        "User-Agent": user_agent or DEFAULT_MGMT_UA,
    }
    if chatgpt_account_id:
        call_header["Chatgpt-Account-Id"] = chatgpt_account_id
    return {
        "authIndex": auth_index,
        "method": "GET",
        "url": "https://chatgpt.com/backend-api/wham/usage",
        "header": call_header,
    }


async def probe_account_async(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    base_url: str,
    token: str,
    item: Dict[str, Any],
    user_agent: str,
    timeout: int,
    retries: int,
) -> Dict[str, Any]:
    """并发探测单个账号，重点关注是否返回 401。"""
    auth_index = item.get("auth_index")
    name = item.get("name") or item.get("id")
    account = item.get("account") or item.get("email") or ""
    result = {
        "name": name,
        "account": account,
        "auth_index": auth_index,
        "type": get_item_type(item),
        "provider": item.get("provider"),
        "chatgpt_account_id": extract_chatgpt_account_id(item),
        "disabled": bool(item.get("disabled")),
        "status": item.get("status"),
        "status_message": item.get("status_message"),
        "status_code": None,
        "invalid_401": False,
        "error": None,
        "weekly_limit_reached": False,
        "weekly_limit_source": "",
        "weekly_limit_scope": "",
        "weekly_plan_type": "",
        "weekly_used_percent": 0,
        "weekly_limit_window_seconds": 0,
        "weekly_reset_after_seconds": 0,
        "weekly_reset_at": 0,
        "weekly_reset_at_text": "",
    }
    if not auth_index:
        result["error"] = "missing auth_index"
        return result

    payload = build_probe_payload(str(auth_index), user_agent, result.get("chatgpt_account_id"))

    for attempt in range(retries + 1):
        try:
            async with semaphore:
                async with session.post(
                    f"{base_url}/v0/management/api-call",
                    headers={**mgmt_headers(token), "Content-Type": "application/json"},
                    json=payload,
                    timeout=timeout,
                ) as resp:
                    text = await resp.text()
                    if resp.status >= 400:
                        raise RuntimeError(f"management api-call http {resp.status}: {text[:200]}")
                    data = safe_json_text(text)
                    sc = data.get("status_code")
                    result["status_code"] = sc
                    result["invalid_401"] = sc == 401
                    result.update(extract_weekly_limit_from_usage_body(decode_management_body(data)))
                    if sc is None:
                        result["error"] = "missing status_code in api-call response"
                    return result
        except Exception as e:
            result["error"] = str(e)
            if attempt >= retries:
                return result
    return result


async def delete_account_async(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    base_url: str,
    token: str,
    name: str,
    timeout: int,
) -> Dict[str, Any]:
    """删除单个失效账号文件，删除失败时返回错误文本便于追踪。"""
    if not name:
        return {"name": None, "deleted": False, "error": "missing name"}
    encoded_name = quote(name, safe="")
    url = f"{base_url}/v0/management/auth-files?name={encoded_name}"
    try:
        async with semaphore:
            async with session.delete(url, headers=mgmt_headers(token), timeout=timeout) as resp:
                text = await resp.text()
                data = safe_json_text(text)
                ok = resp.status == 200 and data.get("status") == "ok"
                return {
                    "name": name,
                    "deleted": ok,
                    "status_code": resp.status,
                    "error": None if ok else f"delete failed, response={text[:200]}",
                }
    except Exception as e:
        return {"name": name, "deleted": False, "error": str(e)}


async def run_probe_async(
    base_url: str,
    token: str,
    target_type: str,
    workers: int,
    timeout: int,
    retries: int,
    user_agent: str,
    logger: Optional[logging.Logger] = None,
    files: Optional[List[Dict[str, Any]]] = None,
) -> tuple[List[Dict[str, Any]], int, int]:
    """并发探测所有可用目标账号，返回探测结果与统计信息。"""
    if files is None:
        files = fetch_auth_files(base_url, token, timeout)
    candidates: List[Dict[str, Any]] = []
    for f in files:
        if str(get_item_type(f)).lower() != target_type.lower():
            continue
        if bool(f.get("disabled")):
            continue
        if not is_auth_file_candidate_available(f):
            continue
        if f.get("_weekly_limit_pending"):
            continue
        candidates.append(f)

    if not candidates:
        return [], len(files), 0

    connector = aiohttp.TCPConnector(limit=max(1, workers), limit_per_host=max(1, workers))
    client_timeout = aiohttp.ClientTimeout(total=max(1, timeout))
    semaphore = asyncio.Semaphore(max(1, workers))

    probe_results = []
    total_candidates = len(candidates)
    checked = 0
    invalid_count = 0

    async with aiohttp.ClientSession(connector=connector, timeout=client_timeout, trust_env=True) as session:
        tasks = [
            asyncio.create_task(
                probe_account_async(
                    session=session,
                    semaphore=semaphore,
                    base_url=base_url,
                    token=token,
                    item=item,
                    user_agent=user_agent,
                    timeout=timeout,
                    retries=retries,
                )
            )
            for item in candidates
        ]
        try:
            for task in asyncio.as_completed(tasks):
                result = await task
                probe_results.append(result)
                checked += 1
                if result.get("invalid_401"):
                    invalid_count += 1

                if logger and (checked % 50 == 0 or checked == total_candidates):
                    weekly_count = sum(1 for row in probe_results if row.get("weekly_limit_reached"))
                    logger.info(
                        "账号探测进度: 已检查=%s/%s, 命中401=%s, 命中周限额=%s",
                        checked,
                        total_candidates,
                        invalid_count,
                        weekly_count,
                    )
        except asyncio.CancelledError:
            if logger:
                logger.warning("账号探测收到中断信号，正在停止未完成的异步任务...")
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    return probe_results, len(files), len(candidates)


async def run_delete_async(
    base_url: str,
    token: str,
    names_to_delete: List[str],
    delete_workers: int,
    timeout: int,
) -> tuple[int, int]:
    """并发删除一批失效账号文件。"""
    if not names_to_delete:
        return 0, 0

    connector = aiohttp.TCPConnector(limit=max(1, delete_workers), limit_per_host=max(1, delete_workers))
    client_timeout = aiohttp.ClientTimeout(total=max(1, timeout))
    semaphore = asyncio.Semaphore(max(1, delete_workers))

    delete_results = []
    async with aiohttp.ClientSession(connector=connector, timeout=client_timeout, trust_env=True) as session:
        tasks = [
            asyncio.create_task(
                delete_account_async(
                    session=session,
                    semaphore=semaphore,
                    base_url=base_url,
                    token=token,
                    name=name,
                    timeout=timeout,
                )
            )
            for name in names_to_delete
        ]
        try:
            for task in asyncio.as_completed(tasks):
                delete_results.append(await task)
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    success = [r for r in delete_results if r.get("deleted")]
    failed = [r for r in delete_results if not r.get("deleted")]
    return len(success), len(failed)


async def run_clean_401_async(
    *,
    conf: Dict[str, Any],
    base_url: str,
    token: str,
    target_type: str,
    workers: int,
    delete_workers: int,
    timeout: int,
    retries: int,
    user_agent: str,
    logger: logging.Logger,
) -> tuple[int, int, int]:
    """先处理周限额停用/恢复，再探测 401，仅删除不可恢复账号。"""
    logger.info("周限额步骤1/4: 拉取账号列表并检查已停用账号的恢复时间")
    files = fetch_auth_files(base_url, token, timeout)
    files, weekly_recover_stats, weekly_state = recover_weekly_limit_accounts(
        files=files,
        conf=conf,
        base_url=base_url,
        token=token,
        target_type=target_type,
        timeout=timeout,
        logger=logger,
    )
    logger.info(
        "周限额恢复汇总: 重新启用=%s, 保持停用=%s, 预停用=%s, 操作失败=%s",
        weekly_recover_stats.get("reenabled", 0),
        weekly_recover_stats.get("kept_disabled", 0),
        weekly_recover_stats.get("disabled_from_status", 0),
        weekly_recover_stats.get("action_failed", 0),
    )

    logger.info("周限额步骤2/4: 探测当前可用账号的 401 与周限额状态")
    probe_results, total_files, codex_files = await run_probe_async(
        base_url=base_url,
        token=token,
        target_type=target_type,
        workers=workers,
        timeout=timeout,
        retries=retries,
        user_agent=user_agent,
        logger=logger,
        files=files,
    )
    invalid_401 = [r for r in probe_results if r.get("invalid_401")]
    logger.info("探测完成: 总账号=%s, 可用codex账号=%s, 401失效=%s", total_files, codex_files, len(invalid_401))

    logger.info("周限额步骤3/4: 处理本轮新命中的周限额账号")
    weekly_limit_stats = apply_weekly_limit_status_actions(
        probe_results=probe_results,
        conf=conf,
        base_url=base_url,
        token=token,
        timeout=timeout,
        logger=logger,
        weekly_state=weekly_state,
    )
    logger.info(
        "周限额探测汇总: 命中=%s, 新停用=%s, 已在等待=%s, 停用失败=%s",
        weekly_limit_stats.get("weekly_limit_hits", 0),
        weekly_limit_stats.get("disabled_now", 0),
        weekly_limit_stats.get("already_pending", 0),
        weekly_limit_stats.get("disable_failed", 0),
    )

    logger.info("周限额步骤4/4: 对真实 401 账号执行 refresh/删除流程")
    names, recovered_count, kept_unknown = try_refresh_invalid_accounts(
        invalid_items=invalid_401,
        conf=conf,
        base_url=base_url,
        token=token,
        user_agent=user_agent,
        timeout=timeout,
        retries=retries,
        logger=logger,
    )
    logger.info(
        "401刷新汇总: 命中=%s, 刷新恢复=%s, 复测不确定保留=%s, 待删除=%s",
        len(invalid_401),
        recovered_count,
        kept_unknown,
        len(names),
    )

    deleted_ok, deleted_fail = await run_delete_async(
        base_url=base_url,
        token=token,
        names_to_delete=names,
        delete_workers=delete_workers,
        timeout=timeout,
    )
    logger.info("删除完成: 成功=%s, 失败=%s", deleted_ok, deleted_fail)
    return len(names), deleted_ok, deleted_fail


def run_clean_401(conf: Dict[str, Any], logger: logging.Logger) -> tuple[int, int, int]:
    """读取配置并执行 401 清理阶段。"""
    if aiohttp is None:
        raise RuntimeError("未安装 aiohttp，请先安装: pip install aiohttp")

    base_url = str(pick_conf(conf, "clean", "base_url", default="") or "").rstrip("/")
    token = str(pick_conf(conf, "clean", "token", "cpa_password", default="") or "").strip()
    target_type = str(pick_conf(conf, "clean", "target_type", default="codex") or "codex")
    workers = int(pick_conf(conf, "clean", "workers", default=20) or 20)
    delete_workers = int(pick_conf(conf, "clean", "delete_workers", default=40) or 40)
    timeout = int(pick_conf(conf, "clean", "timeout", default=10) or 10)
    retries = int(pick_conf(conf, "clean", "retries", default=1) or 1)
    user_agent = str(pick_conf(conf, "clean", "user_agent", default=DEFAULT_MGMT_UA) or DEFAULT_MGMT_UA)

    if not base_url or not token:
        raise RuntimeError("clean 配置缺少 base_url 或 token/cpa_password")

    logger.info("开始清理账号状态: base_url=%s target_type=%s（包含401与周限额检查）", base_url, target_type)
    logger.info(
        "周限额本地文件: report=%s, state=%s",
        resolve_weekly_limit_report_path(conf),
        resolve_weekly_limit_state_path(conf),
    )
    try:
        return asyncio.run(
            run_clean_401_async(
                conf=conf,
                base_url=base_url,
                token=token,
                target_type=target_type,
                workers=workers,
                delete_workers=delete_workers,
                timeout=timeout,
                retries=retries,
                user_agent=user_agent,
                logger=logger,
            )
        )
    except KeyboardInterrupt:
        logger.warning("收到 Ctrl+C，正在中断清理阶段...")
        raise


def parse_args() -> argparse.Namespace:
    """解析脚本命令行参数。"""
    script_dir = resolve_program_dir(__file__)
    default_cfg = script_dir / "config.json"
    default_log_dir = script_dir / "logs"

    parser = argparse.ArgumentParser(description="账号池自动维护（三合一：清理+补号+收敛）")
    parser.add_argument("--config", default=str(default_cfg), help="统一配置文件路径")
    parser.add_argument(
        "--min-candidates",
        type=int,
        default=None,
        help="候选账号最小阈值（默认读取 maintainer.min_candidates / 顶层 min_candidates，最终默认 100）",
    )
    parser.add_argument("--timeout", type=int, default=15, help="统计 candidates 时接口超时秒数")
    parser.add_argument("--log-dir", default=str(default_log_dir), help="日志目录")
    return parser.parse_args()


def main() -> int:
    """主入口：先清理失效账号，再按缺口补号，最后输出汇总结果。"""
    requests.packages.urllib3.disable_warnings()  # type: ignore[attr-defined]

    args = parse_args()
    config_path = Path(args.config).resolve()

    if not config_path.exists():
        print(f"错误：配置文件不存在，请把 config.json 放到程序同目录后重试：{config_path}", file=sys.stderr)
        return 2

    logger, log_path = setup_logger(Path(args.log_dir).resolve())
    logger.info("=== 账号池自动维护开始（二合一）===")
    logger.info("配置文件: %s", config_path)
    logger.info("日志文件: %s", log_path)

    conf = load_json(config_path)
    try:
        normalize_email_provider(pick_conf(conf, "email", "provider", default=DEFAULT_EMAIL_PROVIDER))
    except Exception as e:
        logger.error("%s", e)
        return 2

    base_url = str(pick_conf(conf, "clean", "base_url", default="") or "").rstrip("/")
    token = str(pick_conf(conf, "clean", "token", "cpa_password", default="") or "").strip()
    target_type = str(pick_conf(conf, "clean", "target_type", default="codex") or "codex")

    cfg_min_candidates = pick_conf(conf, "maintainer", "min_candidates", default=None)
    if cfg_min_candidates is None:
        cfg_min_candidates = conf.get("min_candidates")

    if args.min_candidates is not None:
        min_candidates = int(args.min_candidates)
    elif cfg_min_candidates is not None:
        min_candidates = int(cfg_min_candidates)
    else:
        min_candidates = 100

    if min_candidates < 0:
        logger.error("min_candidates 不能小于 0（当前值=%s）", min_candidates)
        return 2
    if not base_url or not token:
        logger.error("缺少 clean.base_url 或 clean.token/cpa_password")
        return 2

    try:
        try:
            probed_401, deleted_ok, deleted_fail = run_clean_401(conf, logger)
            logger.info("清理阶段汇总: 401命中=%s, 删除成功=%s, 删除失败=%s", probed_401, deleted_ok, deleted_fail)
        except Exception as e:
            logger.error("清理 401 失败: %s", e)
            logger.info("=== 账号池自动维护结束（失败）===")
            return 3

        try:
            total_after_clean, candidates_after_clean = get_candidates_count(
                base_url=base_url,
                token=token,
                target_type=target_type,
                timeout=args.timeout,
            )
        except Exception as e:
            logger.error("删除后统计失败: %s", e)
            logger.info("=== 账号池自动维护结束（失败）===")
            return 4

        logger.info(
            "删除401并同步周限额后统计: 总账号=%s, 可用candidates=%s, 阈值=%s",
            total_after_clean,
            candidates_after_clean,
            min_candidates,
        )

        if candidates_after_clean >= min_candidates:
            logger.info("当前 candidates 已达标，无需补号。")
            logger.info("=== 账号池自动维护结束（成功）===")
            return 0

        gap = min_candidates - candidates_after_clean
        logger.info("当前 candidates 未达标，缺口=%s，开始补号。", gap)

        try:
            filled, failed, synced = run_batch_register(conf=conf, target_tokens=gap, logger=logger)
            logger.info("补号阶段汇总: 成功token=%s, 失败=%s, 收敛账号=%s", filled, failed, synced)
        except Exception as e:
            logger.error("补号阶段失败: %s", e)
            logger.info("=== 账号池自动维护结束（失败）===")
            return 5

        try:
            total_final, candidates_final = get_candidates_count(
                base_url=base_url,
                token=token,
                target_type=target_type,
                timeout=args.timeout,
            )
        except Exception as e:
            logger.error("补号后统计失败: %s", e)
            logger.info("=== 账号池自动维护结束（失败）===")
            return 6

        logger.info(
            "补号后统计: 总账号=%s, 可用codex账号=%s, codex目标=%s",
            total_final,
            candidates_final,
            min_candidates,
        )
        if candidates_final < min_candidates:
            logger.warning("最终 codex账号数 仍低于阈值，请检查邮箱/OAuth/上传链路。")
        logger.info("=== 账号池自动维护结束（成功）===")
        return 0
    except KeyboardInterrupt:
        logger.warning("收到 Ctrl+C，已停止当前任务。")
        logger.info("=== 账号池自动维护结束（手动中断）===")
        return 130


def run_main_with_interrupt_guard() -> int:
    """统一收敛 Ctrl+C，避免 PyInstaller 打印未处理异常栈。"""
    try:
        return main()
    except KeyboardInterrupt:
        print("已收到 Ctrl+C，程序已手动中断。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(run_main_with_interrupt_guard())
