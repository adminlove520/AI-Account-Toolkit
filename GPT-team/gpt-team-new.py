#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gpt-team-new.py
================
全新纯 HTTP 协议版本（无 Selenium / 无浏览器）
- 注册：使用 ProtocolRegistrar 五步 HTTP 流程 + Sentinel Token
- 母号登录：HTTP OAuth + PKCE，自动拉取 account_id / auth_token
- Codex 授权：HTTP 交换 code → token → 上传到 CPA
- 子号邀请：注册成功后自动发送团队邀请
配置文件: config.yaml（兼容原格式）
"""

from __future__ import annotations

import base64
import csv
import datetime as dt
import hashlib
import json
import logging
import os
import random
import re
import secrets
import string
import sys
import time
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlencode, urlparse

import requests
import urllib3
import yaml
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 消除 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ============================================================
# ① 读取 config.yaml
# ============================================================
_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")


def _load_config() -> Dict[str, Any]:
    if not os.path.exists(_CONFIG_FILE):
        raise FileNotFoundError(f"找不到配置文件: {_CONFIG_FILE}\n请先创建 config.yaml")
    with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


_cfg = _load_config()

# 账号总数
TOTAL_ACCOUNTS: int = int(_cfg.get("total_accounts", 2))

# 临时邮箱配置
TEMP_MAIL_WORKER_DOMAIN: str = _cfg["temp_mail"]["worker_domain"]
TEMP_MAIL_EMAIL_DOMAINS: List[str] = _cfg["temp_mail"]["email_domains"]
TEMP_MAIL_ADMIN_PASSWORD: str = _cfg["temp_mail"]["admin_password"]

# CLI Proxy API（CPA）配置
CLI_PROXY_API_BASE: str = _cfg["cli_proxy"]["api_base"].rstrip("/")
CLI_PROXY_PASSWORD: str = _cfg["cli_proxy"]["password"]

# 输出文件
ACCOUNTS_FILE: str = _cfg["output"].get("accounts_file", "accounts.txt")
INVITE_TRACKER_FILE: str = _cfg["output"]["invite_tracker_file"]

# CPA 上传开关
CPA_UPLOAD_ENABLED: bool = bool(_cfg.get("cli_proxy", {}).get("upload_enabled", True))

# 车头（Teams）列表
TEAMS: List[Dict[str, Any]] = _cfg.get("teams", [])

# OAuth 常量（Codex CLI 客户端）
OPENAI_AUTH_BASE = "https://auth.openai.com"
OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OAUTH_REDIRECT_URI = "http://localhost:1455/auth/callback"
OAUTH_SCOPE = "openid profile email offline_access"

print(f"✅ 配置已加载: {_CONFIG_FILE}")
print(f"   注册数量: {TOTAL_ACCOUNTS} | 车头数量: {len(TEAMS)} | 邮箱域名: {TEMP_MAIL_EMAIL_DOMAINS}")

# ============================================================
# ② 日志（仅控制台输出，不写文件）
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("gpt-team")

# ============================================================
# ③ HTTP Session 工厂（带 Retry + Verify=False）
# ============================================================
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.0.0 Safari/537.36"
)

COMMON_HEADERS: Dict[str, str] = {
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

NAVIGATE_HEADERS: Dict[str, str] = {
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


def create_session(proxy: str = "") -> requests.Session:
    """创建带重试的 requests.Session"""
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    return s


# 全局 HTTP Session（供临时邮箱 / CPA API 等非 OpenAI 请求使用）
http_session = create_session()

# ============================================================
# ④ PKCE 工具
# ============================================================

def generate_pkce() -> Tuple[str, str]:
    """返回 (code_verifier, code_challenge)"""
    code_verifier = (
        base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
    )
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


# ============================================================
# ⑤ Datadog Trace（模拟浏览器追踪头）
# ============================================================

def generate_datadog_trace() -> Dict[str, str]:
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


# ============================================================
# ⑥ SentinelTokenGenerator（OpenAI 反机器人令牌）
# ============================================================

class SentinelTokenGenerator:
    """生成 openai-sentinel-token，绕过注册/登录反机器人检测"""
    MAX_ATTEMPTS = 500_000

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
        h ^= h >> 16
        h = (h * 2246822507) & 0xFFFFFFFF
        h ^= h >> 13
        h = (h * 3266489909) & 0xFFFFFFFF
        h ^= h >> 16
        h &= 0xFFFFFFFF
        return format(h, "08x")

    @staticmethod
    def _b64(data: Any) -> str:
        js = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
        return base64.b64encode(js.encode("utf-8")).decode("ascii")

    def _get_config(self) -> List[Any]:
        now = dt.datetime.now(dt.timezone.utc).strftime(
            "%a %b %d %Y %H:%M:%S GMT+0000 (Coordinated Universal Time)"
        )
        perf_now = random.uniform(1000, 50000)
        time_origin = time.time() * 1000 - perf_now
        return [
            "1920x1080", now, 4294705152, random.random(), USER_AGENT,
            "https://sentinel.openai.com/sentinel/20260124ceb8/sdk.js",
            None, None, "en-US", "en-US,en", random.random(),
            "vendorSub−undefined", "location", "Object",
            perf_now, self.sid, "", random.choice([4, 8, 12, 16]), time_origin,
        ]

    def generate_requirements_token(self) -> str:
        cfg = self._get_config()
        cfg[3] = 1
        cfg[9] = round(random.uniform(5, 50))
        return "gAAAAAC" + self._b64(cfg)

    def generate_token(self, seed: Optional[str] = None, difficulty: Optional[str] = None) -> str:
        if seed is None:
            seed = self.requirements_seed
            difficulty = difficulty or "0"
        cfg = self._get_config()
        start = time.time()
        for i in range(self.MAX_ATTEMPTS):
            cfg[3] = i
            cfg[9] = round((time.time() - start) * 1000)
            data = self._b64(cfg)
            hash_hex = self._fnv1a_32(seed + data)
            if hash_hex[: len(difficulty or "0")] <= (difficulty or "0"):
                return "gAAAAAB" + data + "~S"
        return "gAAAAAB" + self._b64(str(None))


# ============================================================
# ⑦ 从 Sentinel 服务器拉取挑战并构建完整 Token
# ============================================================

def fetch_sentinel_challenge(
    session: requests.Session, device_id: str, flow: str = "authorize_continue"
) -> Optional[Dict[str, Any]]:
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


def build_sentinel_token(
    session: requests.Session, device_id: str, flow: str = "authorize_continue"
) -> Optional[str]:
    """获取 Sentinel 挑战并求解，返回 openai-sentinel-token 字符串"""
    challenge = fetch_sentinel_challenge(session, device_id, flow)
    if not challenge:
        # 无法获取挑战时降级：只用本地生成 token
        gen = SentinelTokenGenerator(device_id=device_id)
        return json.dumps({
            "p": gen.generate_requirements_token(), "t": "", "c": "",
            "id": device_id, "flow": flow,
        })
    c_value = challenge.get("token", "")
    pow_data = challenge.get("proofofwork", {})
    gen = SentinelTokenGenerator(device_id=device_id)
    if isinstance(pow_data, dict) and pow_data.get("required") and pow_data.get("seed"):
        p_value = gen.generate_token(
            seed=pow_data.get("seed"), difficulty=pow_data.get("difficulty", "0")
        )
    else:
        p_value = gen.generate_requirements_token()
    return json.dumps({"p": p_value, "t": "", "c": c_value, "id": device_id, "flow": flow})


# ============================================================
# ⑧ 临时邮箱 API（自建 Cloudflare Worker 版）
# ============================================================

def create_temp_email() -> Tuple[Optional[str], Optional[str]]:
    """
    调用自建邮箱 API 创建新地址。
    返回 (email_address, jwt_token)，失败返回 (None, None)
    """
    name_len = random.randint(10, 14)
    name_chars = list(random.choices(string.ascii_lowercase, k=name_len))
    for _ in range(random.choice([1, 2])):
        pos = random.randint(2, len(name_chars) - 1)
        name_chars.insert(pos, random.choice(string.digits))
    name = "".join(name_chars)
    chosen_domain = random.choice(TEMP_MAIL_EMAIL_DOMAINS)

    try:
        resp = http_session.post(
            f"https://{TEMP_MAIL_WORKER_DOMAIN}/admin/new_address",
            json={"enablePrefix": True, "name": name, "domain": chosen_domain},
            headers={"x-admin-auth": TEMP_MAIL_ADMIN_PASSWORD, "Content-Type": "application/json"},
            timeout=15,
            verify=False,
        )
        if resp.status_code == 200:
            data = resp.json()
            email = data.get("address")
            token = data.get("jwt")
            if email:
                logger.info("创建临时邮箱成功: %s", email)
                return str(email), str(token or "")
        logger.warning("创建临时邮箱失败: HTTP %s | %s", resp.status_code, resp.text[:200])
    except Exception as e:
        logger.warning("创建临时邮箱异常: %s", e)
    return None, None


def _get_jwt_for_address(email_address: str) -> str:
    """
    通过 admin API 为已有邮箱地址获取 JWT（母号用）。
    地址已在 Worker 中存在，尝试多种方式获取 JWT。
    """
    if not email_address or "@" not in email_address:
        return ""
    try:
        name, domain_part = email_address.split("@", 1)
        admin_h = {"x-admin-auth": TEMP_MAIL_ADMIN_PASSWORD, "Content-Type": "application/json"}

        # 方法1：new_address enablePrefix=False（地址不存在时创建并返回JWT）
        resp = http_session.post(
            f"https://{TEMP_MAIL_WORKER_DOMAIN}/admin/new_address",
            headers=admin_h,
            json={"enablePrefix": False, "name": name, "domain": domain_part},
            timeout=15, verify=False,
        )
        logger.info("_get_jwt 方法1: HTTP %s | body=%s", resp.status_code, resp.text[:120])
        if resp.status_code in (200, 201):
            data = resp.json()
            jwt = data.get("jwt") or data.get("token")
            if jwt:
                return str(jwt)

        # 地址已存在时（方法1返回400），通过列表API找到ID，然后请求生成 JWT
        # cloudflare_temp_email 项目：name=完整邮箱, id=数字ID, 无JWT字段
        found_id = None
        search_done = False

        # 方法A：先尝试带 search/q 参数的精确搜索（减少翻页量）
        for search_key in ["q", "search", "query", "keyword", "name"]:
            r_search = http_session.get(
                f"https://{TEMP_MAIL_WORKER_DOMAIN}/admin/address",
                params={"limit": 20, "offset": 0, search_key: email_address},
                headers={"x-admin-auth": TEMP_MAIL_ADMIN_PASSWORD},
                timeout=15, verify=False,
            )
            if r_search.status_code == 200:
                s_items = r_search.json()
                if isinstance(s_items, dict):
                    s_items = s_items.get("results") or s_items.get("data") or []
                if isinstance(s_items, list):
                    for it in s_items:
                        if str(it.get("name") or "").lower() == email_address.lower():
                            found_id = it.get("id")
                            logger.info("_get_jwt 搜索参数(%s)找到 | id=%s | email=%s",
                                        search_key, found_id, email_address)
                            search_done = True
                            break
            if search_done:
                break

        # 方法B：全量翻页搜索（遍历所有地址直到找到）
        if not search_done:
            page_size = 100
            offset = 0
            max_pages = 200  # 最多查 20000 个地址
            first_page_logged = False
            while not search_done and offset < page_size * max_pages:
                resp_list = http_session.get(
                    f"https://{TEMP_MAIL_WORKER_DOMAIN}/admin/address",
                    params={"limit": page_size, "offset": offset},
                    headers={"x-admin-auth": TEMP_MAIL_ADMIN_PASSWORD},
                    timeout=15, verify=False,
                )
                if resp_list.status_code != 200:
                    logger.info("_get_jwt 列表API: HTTP %s | body=%s",
                                resp_list.status_code, resp_list.text[:80])
                    break
                raw_json = resp_list.json()
                if not first_page_logged:
                    first_page_logged = True
                    logger.info("_get_jwt 全量搜索开始 | total首页: %s",
                                str(raw_json.get("results", [])[:1])[:100] if isinstance(raw_json, dict) else "")
                items = raw_json
                if isinstance(items, dict):
                    items = (items.get("results") or items.get("data")
                             or items.get("addresses") or items.get("items") or [])
                if not isinstance(items, list) or not items:
                    logger.info("_get_jwt 全量搜索结束 | offset=%s (无更多条目)", offset)
                    break
                for it in items:
                    if str(it.get("name") or "").lower() == email_address.lower():
                        found_id = it.get("id")
                        logger.info("_get_jwt 全量搜索找到 | id=%s | offset=%s | email=%s",
                                    found_id, offset, email_address)
                        search_done = True
                        break
                if search_done or len(items) < page_size:
                    break
                offset += page_size

        if not search_done:
            logger.warning("_get_jwt 未找到地址 | email=%s (已搜索至 offset=%s)", email_address, offset if 'offset' in dir() else 0)


        if found_id:
            # 使用 ID 调用 new_token 端点生成 JWT
            for token_path in [
                f"/admin/address/{found_id}/new_token",
                f"/admin/new_address_token/{found_id}",
                f"/admin/address/{found_id}/token",
            ]:
                resp_tok = http_session.post(
                    f"https://{TEMP_MAIL_WORKER_DOMAIN}{token_path}",
                    headers={"x-admin-auth": TEMP_MAIL_ADMIN_PASSWORD, "Content-Type": "application/json"},
                    json={},
                    timeout=15, verify=False,
                )
                logger.info("_get_jwt token端点 %s: HTTP %s | body=%s",
                            token_path, resp_tok.status_code, resp_tok.text[:120])
                if resp_tok.status_code in (200, 201):
                    d = resp_tok.json()
                    jwt = d.get("jwt") or d.get("token") or d.get("address_token")
                    if jwt:
                        logger.info("_get_jwt 通过ID端点成功 | email=%s", email_address)
                        return str(jwt)

        logger.warning("_get_jwt_for_address 全部方法失败 | email=%s", email_address)
    except Exception as e:
        logger.warning("_get_jwt_for_address 异常: %s | email=%s", e, email_address)
    return ""


def fetch_emails_list(jwt_token: str) -> List[Dict[str, Any]]:
    """拉取收件箱邮件列表"""
    try:
        resp = http_session.get(
            f"https://{TEMP_MAIL_WORKER_DOMAIN}/api/mails",
            params={"limit": 10, "offset": 0},
            headers={"Authorization": f"Bearer {jwt_token}"},
            verify=False,
            timeout=30,
        )
        if resp.status_code == 200:
            rows = resp.json().get("results", [])
            return rows if isinstance(rows, list) else []
    except Exception:
        pass
    return []


def _extract_otp_from_raw(content: str) -> Optional[str]:
    """从邮件原始内容中提取6位数字验证码"""
    if not content:
        return None
    # 优先提取 HTML 标签内的数字
    m = re.search(r"background-color:\s*#F3F3F3[^>]*>[\s\S]*?(\d{6})[\s\S]*?</p>", content)
    if m:
        return m.group(1)
    for pat in [r">\s*(\d{6})\s*<", r"(?<![#&])\b(\d{6})\b"]:
        for code in re.findall(pat, content):
            if code != "177010":
                return code
    return None


def wait_for_otp(jwt_token: str, timeout: int = 120) -> Optional[str]:
    """轮询等待并提取6位验证码"""
    seen_ids: set = set()
    start = time.time()
    while time.time() - start < timeout:
        emails = fetch_emails_list(jwt_token)
        for item in emails:
            if not isinstance(item, dict):
                continue
            eid = item.get("id")
            if eid in seen_ids:
                continue
            seen_ids.add(eid)
            raw = str(item.get("raw") or "")
            code = _extract_otp_from_raw(raw)
            if code:
                logger.info("收到验证码: %s", code)
                return code
        time.sleep(3)
    logger.warning("等待验证码超时（%ds）", timeout)
    return None


# ============================================================
# ⑨ 随机用户信息生成
# ============================================================

def generate_random_name() -> Tuple[str, str]:
    first = ["James", "Robert", "John", "Michael", "David", "Mary", "Jennifer", "Linda", "Emma", "Olivia"]
    last = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller"]
    return random.choice(first), random.choice(last)


def generate_random_birthday() -> str:
    year = random.randint(1992, 2003)
    month = random.randint(1, 12)
    day = random.randint(1, 28)
    return f"{year:04d}-{month:02d}-{day:02d}"


def generate_random_password(length: int = 16) -> str:
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


# ============================================================
# ⑩ ProtocolRegistrar：纯 HTTP 五步注册流程
# ============================================================

class ProtocolRegistrar:
    """
    纯 HTTP 注册器（来自 对比/gptzidong），无需 Selenium。
    步骤：
      step0: OAuth 会话初始化 + authorize/continue
      step2: 提交 email + password 注册
      step3: 触发 OTP 发送
      step4: 验证 OTP
      step5: 创建账号（填写姓名/生日）
    """

    def __init__(self, proxy: str = ""):
        self.session = create_session(proxy=proxy)
        self.device_id = str(uuid.uuid4())
        self.sentinel_gen = SentinelTokenGenerator(device_id=self.device_id)
        self.code_verifier: Optional[str] = None
        self.state: Optional[str] = None

    def _headers(self, referer: str, with_sentinel: bool = False) -> Dict[str, str]:
        h = dict(COMMON_HEADERS)
        h["referer"] = referer
        h["oai-device-id"] = self.device_id
        h.update(generate_datadog_trace())
        if with_sentinel:
            h["openai-sentinel-token"] = self.sentinel_gen.generate_token()
        return h

    def step0_init_oauth(self, email: str, client_id: str, redirect_uri: str) -> bool:
        """初始化 OAuth 会话并发送 authorize/continue 请求"""
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
            resp = self.session.get(
                url, headers=NAVIGATE_HEADERS, allow_redirects=True, verify=False, timeout=30
            )
        except Exception as e:
            logger.warning("step0a 失败: %s", e)
            return False
        if resp.status_code not in (200, 302):
            logger.warning("step0a 状态异常: %s", resp.status_code)
            return False
        if not any(c.name == "login_session" for c in self.session.cookies):
            logger.warning("step0a 未获取 login_session cookie")
            return False

        h = self._headers(f"{OPENAI_AUTH_BASE}/create-account")
        sentinel = build_sentinel_token(self.session, self.device_id, flow="authorize_continue")
        if sentinel:
            h["openai-sentinel-token"] = sentinel
        try:
            r2 = self.session.post(
                f"{OPENAI_AUTH_BASE}/api/accounts/authorize/continue",
                json={"username": {"kind": "email", "value": email}, "screen_hint": "signup"},
                headers=h,
                verify=False,
                timeout=30,
            )
            if r2.status_code != 200:
                logger.warning("step0b 失败: %s | %s", r2.status_code, r2.text[:200])
            return r2.status_code == 200
        except Exception as e:
            logger.warning("step0b 异常: %s", e)
            return False

    def step2_register_user(self, email: str, password: str) -> bool:
        """提交注册表单（email + password）"""
        h = self._headers(f"{OPENAI_AUTH_BASE}/create-account/password", with_sentinel=True)
        try:
            resp = self.session.post(
                f"{OPENAI_AUTH_BASE}/api/accounts/user/register",
                json={"username": email, "password": password},
                headers=h,
                verify=False,
                timeout=30,
            )
            if resp.status_code == 200:
                return True
            if resp.status_code in (301, 302):
                loc = resp.headers.get("Location", "")
                return "email-otp" in loc or "email-verification" in loc
            logger.warning("step2 失败: %s | %s", resp.status_code, resp.text[:200])
            return False
        except Exception as e:
            logger.warning("step2 异常: %s | email=%s", e, email)
            return False

    def step3_send_otp(self) -> bool:
        """触发验证邮件发送"""
        try:
            h = dict(NAVIGATE_HEADERS)
            h["referer"] = f"{OPENAI_AUTH_BASE}/create-account/password"
            self.session.get(
                f"{OPENAI_AUTH_BASE}/api/accounts/email-otp/send",
                headers=h, verify=False, timeout=30, allow_redirects=True,
            )
            self.session.get(
                f"{OPENAI_AUTH_BASE}/email-verification",
                headers=h, verify=False, timeout=30, allow_redirects=True,
            )
            return True
        except Exception as e:
            logger.warning("step3 异常: %s", e)
            return False

    def step4_validate_otp(self, code: str) -> bool:
        """提交6位验证码"""
        h = self._headers(f"{OPENAI_AUTH_BASE}/email-verification")
        try:
            r = self.session.post(
                f"{OPENAI_AUTH_BASE}/api/accounts/email-otp/validate",
                json={"code": code},
                headers=h,
                verify=False,
                timeout=30,
            )
            if r.status_code != 200:
                logger.warning("step4 失败: %s | code=%s", r.status_code, code)
            return r.status_code == 200
        except Exception as e:
            logger.warning("step4 异常: %s", e)
            return False

    def step5_create_account(self, first_name: str, last_name: str, birthdate: str) -> bool:
        """填写姓名和生日，完成账号创建"""
        h = self._headers(f"{OPENAI_AUTH_BASE}/about-you")
        body = {"name": f"{first_name} {last_name}", "birthdate": birthdate}
        try:
            r = self.session.post(
                f"{OPENAI_AUTH_BASE}/api/accounts/create_account",
                json=body, headers=h, verify=False, timeout=30,
            )
            if r.status_code == 200:
                return True
            # 命中 sentinel 风控时重试
            if r.status_code == 403 and "sentinel" in r.text.lower():
                h["openai-sentinel-token"] = SentinelTokenGenerator(self.device_id).generate_token()
                rr = self.session.post(
                    f"{OPENAI_AUTH_BASE}/api/accounts/create_account",
                    json=body, headers=h, verify=False, timeout=30,
                )
                return rr.status_code in (200, 301, 302)
            return r.status_code in (301, 302)
        except Exception as e:
            logger.warning("step5 异常: %s", e)
            return False

    def register(
        self,
        email: str,
        jwt_token: str,
        password: str,
        client_id: str = OAUTH_CLIENT_ID,
        redirect_uri: str = OAUTH_REDIRECT_URI,
    ) -> bool:
        """执行完整注册五步，成功返回 True"""
        first_name, last_name = generate_random_name()
        birthdate = generate_random_birthday()

        logger.info("[注册] step0 初始化 OAuth | email=%s", email)
        if not self.step0_init_oauth(email, client_id, redirect_uri):
            logger.warning("[注册] step0 失败 | email=%s", email)
            return False
        time.sleep(1)

        logger.info("[注册] step2 提交注册表单 | email=%s", email)
        if not self.step2_register_user(email, password):
            logger.warning("[注册] step2 失败 | email=%s", email)
            return False
        time.sleep(1)

        logger.info("[注册] step3 发送OTP | email=%s", email)
        if not self.step3_send_otp():
            logger.warning("[注册] step3 失败 | email=%s", email)
            return False

        logger.info("[注册] 等待验证码 | email=%s", email)
        code = wait_for_otp(jwt_token, timeout=120)
        if not code:
            logger.warning("[注册] 未收到验证码 | email=%s", email)
            return False

        logger.info("[注册] step4 验证OTP: %s | email=%s", code, email)
        if not self.step4_validate_otp(code):
            logger.warning("[注册] step4 失败 | email=%s", email)
            return False
        time.sleep(1)

        logger.info("[注册] step5 创建账号 | email=%s", email)
        ok = self.step5_create_account(first_name, last_name, birthdate)
        if not ok:
            logger.warning("[注册] step5 失败 | email=%s", email)
        return ok


# ============================================================
# ⑪ HTTP OAuth 登录（子号/母号通用）— 来自 对比/gptzidong
# ============================================================

def _extract_code_from_url(url: str) -> Optional[str]:
    if not url or "code=" not in url:
        return None
    try:
        return parse_qs(urlparse(url).query).get("code", [None])[0]
    except Exception:
        return None


def _follow_and_extract_code(
    session: requests.Session,
    url: str,
    oauth_issuer: str,
    max_depth: int = 10,
) -> Optional[str]:
    """跟随重定向，提取 OAuth code 参数"""
    if max_depth <= 0:
        return None
    try:
        r = session.get(
            url, headers=NAVIGATE_HEADERS, verify=False, timeout=15, allow_redirects=False,
        )
        if r.status_code in (301, 302, 303, 307, 308):
            loc = r.headers.get("Location", "")
            code = _extract_code_from_url(loc)
            if code:
                return code
            if loc.startswith("/"):
                loc = f"{oauth_issuer}{loc}"
            return _follow_and_extract_code(session, loc, oauth_issuer, max_depth - 1)
        if r.status_code == 200:
            return _extract_code_from_url(str(r.url))
    except requests.exceptions.ConnectionError as e:
        m = re.search(r"(https?://localhost[^\s'\"]+)", str(e))
        if m:
            return _extract_code_from_url(m.group(1))
    except Exception:
        pass
    return None


def perform_http_oauth_login(
    email: str,
    password: str = "",
    cf_token: str = "",
    worker_domain: str = "",
    oauth_issuer: str = OPENAI_AUTH_BASE,
    oauth_client_id: str = OAUTH_CLIENT_ID,
    oauth_redirect_uri: str = OAUTH_REDIRECT_URI,
    proxy: str = "",
) -> Optional[Dict[str, Any]]:
    """
    纯 HTTP OAuth 登录，返回包含 access_token/refresh_token 的字典。
    参考自 gptzidong/auto_pool_maintainer.py 的 perform_codex_oauth_login_http。
    """
    session = create_session(proxy=proxy)
    device_id = str(uuid.uuid4())

    session.cookies.set("oai-did", device_id, domain=".auth.openai.com")
    session.cookies.set("oai-did", device_id, domain="auth.openai.com")

    code_verifier, code_challenge = generate_pkce()
    state = secrets.token_urlsafe(32)

    # Step A: 获取 login_session cookie
    logger.info("[Codex] Step A: authorize | email=%s", email)
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
            authorize_url, headers=NAVIGATE_HEADERS,
            allow_redirects=True, verify=False, timeout=30,
        )
    except Exception as e:
        logger.warning("[Codex] Step A 失败: %s | email=%s", e, email)
        return None

    # Step B: 提交邮箱
    logger.info("[Codex] Step B: 提交邮箱 | email=%s", email)
    headers = dict(COMMON_HEADERS)
    headers["referer"] = f"{oauth_issuer}/log-in"
    headers["oai-device-id"] = device_id
    headers.update(generate_datadog_trace())

    sentinel_email = build_sentinel_token(session, device_id, flow="authorize_continue")
    if not sentinel_email:
        logger.warning("[Codex] Step B sentinel 失败 | email=%s", email)
        return None
    headers["openai-sentinel-token"] = sentinel_email

    try:
        resp = session.post(
            f"{oauth_issuer}/api/accounts/authorize/continue",
            json={"username": {"kind": "email", "value": email}},
            headers=headers, verify=False, timeout=30,
        )
    except Exception as e:
        logger.warning("[Codex] Step B 异常: %s | email=%s", e, email)
        return None
    if resp.status_code != 200:
        logger.warning("[Codex] Step B 失败: HTTP %s | email=%s", resp.status_code, email)
        return None

    # Step C: 提交密码
    logger.info("[Codex] Step C: 提交密码 | email=%s", email)
    headers["referer"] = f"{oauth_issuer}/log-in/password"
    headers.update(generate_datadog_trace())

    sentinel_pwd = build_sentinel_token(session, device_id, flow="password_verify")
    if not sentinel_pwd:
        logger.warning("[Codex] Step C sentinel 失败 | email=%s", email)
        return None
    headers["openai-sentinel-token"] = sentinel_pwd

    try:
        resp = session.post(
            f"{oauth_issuer}/api/accounts/password/verify",
            json={"password": password},
            headers=headers, verify=False, timeout=30, allow_redirects=False,
        )
    except Exception as e:
        logger.warning("[Codex] Step C 异常: %s | email=%s", e, email)
        return None
    if resp.status_code != 200:
        logger.warning("[Codex] Step C 失败: HTTP %s | email=%s", resp.status_code, email)
        return None

    continue_url = None
    page_type = ""
    try:
        data = resp.json()
        continue_url = str(data.get("continue_url") or "")
        page_type = str(((data.get("page") or {}).get("type")) or "")
    except Exception:
        pass
    logger.info("[Codex] Step C 结果 | continue_url=%s | page_type=%s | email=%s", (continue_url or "")[:80], page_type, email)
    if not continue_url:
        logger.warning("[Codex] Step C 无 continue_url | email=%s", email)
        return None

    # Step D（可选）：若需要邮箱验证码
    if page_type == "email_otp_verification" or "email-verification" in continue_url:
        logger.info("[Codex] Step D: 需要OTP验证 | email=%s", email)
        if not cf_token:
            logger.warning("[Codex] 无 cf_token，跳过OTP | email=%s", email)
            return None

        h_val = dict(COMMON_HEADERS)
        h_val["referer"] = f"{oauth_issuer}/email-verification"
        h_val["oai-device-id"] = device_id
        h_val.update(generate_datadog_trace())

        # 必须先触发发送 OTP 邮件，否则等待循环里永远收不到新验证码
        sentinel_otp = build_sentinel_token(session, device_id, flow="email_otp")
        if sentinel_otp:
            h_val["openai-sentinel-token"] = sentinel_otp
        try:
            r_otp_init = session.post(
                f"{oauth_issuer}/api/accounts/email-otp/init",
                json={}, headers=h_val, verify=False, timeout=30,
            )
            logger.info("[Codex] OTP 触发: HTTP %s | email=%s", r_otp_init.status_code, email)
        except Exception as e:
            logger.warning("[Codex] OTP 触发失败: %s | email=%s", e, email)

        tried_codes: set = set()
        start_time = time.time()
        code = None
        while time.time() - start_time < 120:
            all_emails = fetch_emails_list(cf_token)
            if not all_emails:
                time.sleep(2)
                continue

            all_codes = []
            for e_item in all_emails:
                if isinstance(e_item, dict):
                    c = _extract_otp_from_raw(str(e_item.get("raw") or ""))
                    if c and c not in tried_codes:
                        all_codes.append(c)

            if not all_codes:
                time.sleep(2)
                continue

            for try_code in all_codes:
                tried_codes.add(try_code)
                resp_val = session.post(
                    f"{oauth_issuer}/api/accounts/email-otp/validate",
                    json={"code": try_code}, headers=h_val, verify=False, timeout=30,
                )
                if resp_val.status_code == 200:
                    code = try_code
                    try:
                        d2 = resp_val.json()
                        continue_url = str(d2.get("continue_url") or "")
                        page_type = str(((d2.get("page") or {}).get("type")) or "")
                    except Exception:
                        pass
                    break

            if code:
                break
            time.sleep(2)
        if not code:
            return None

        # 处理 about-you 页面（新注册账号可能需要填写资料）
        if "about-you" in continue_url:
            h_about = dict(NAVIGATE_HEADERS)
            h_about["referer"] = f"{oauth_issuer}/email-verification"
            try:
                resp_about = session.get(
                    f"{oauth_issuer}/about-you",
                    headers=h_about, verify=False, timeout=30, allow_redirects=True,
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
                    headers=h_create, verify=False, timeout=30,
                )
                if resp_create.status_code == 200:
                    try:
                        data = resp_create.json()
                        continue_url = str(data.get("continue_url") or "")
                    except Exception:
                        pass
                elif resp_create.status_code == 400 and "already_exists" in resp_create.text:
                    continue_url = f"{oauth_issuer}/sign-in-with-chatgpt/codex/consent"

        # 处理 consent 页面类型
        if "consent" in page_type:
            continue_url = f"{oauth_issuer}/sign-in-with-chatgpt/codex/consent"

        if not continue_url or "email-verification" in continue_url:
            return None

    # Step E: 跟随 consent/workspace 重定向获取 auth code
    if continue_url.startswith("/"):
        consent_url = f"{oauth_issuer}{continue_url}"
    else:
        consent_url = continue_url

    def _decode_auth_session_cookie(sess: requests.Session) -> Optional[Dict[str, Any]]:
        for c in sess.cookies:
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

    auth_code = None

    # 主流：GET consent_url，如果是重定向就提取 code
    try:
        resp_consent = session.get(
            consent_url, headers=NAVIGATE_HEADERS,
            verify=False, timeout=30, allow_redirects=False,
        )
        if resp_consent.status_code in (301, 302, 303, 307, 308):
            loc = resp_consent.headers.get("Location", "")
            auth_code = _extract_code_from_url(loc)
            if not auth_code:
                auth_code = _follow_and_extract_code(session, loc, oauth_issuer)
        elif resp_consent.status_code == 200:
            # 需要 POST 同意
            # 提取页面中的 state/nonce 等隐藏字段
            html = resp_consent.text
            state_m = re.search(r'["\']state["\']:\s*["\']([^"\'\ ]+)["\']', html)
            nonce_m = re.search(r'["\']nonce["\']:\s*["\']([^"\'\ ]+)["\']', html)
            # POST 到同一 URL 表示应允 consent
            consent_payload = {"action": "allow"}
            if state_m:
                consent_payload["state"] = state_m.group(1)
            if nonce_m:
                consent_payload["nonce"] = nonce_m.group(1)
            consent_h = {
                "accept": "application/json, text/plain, */*",
                "content-type": "application/json",
                "origin": oauth_issuer,
                "referer": consent_url,
                "user-agent": USER_AGENT,
                "oai-device-id": device_id,
            }
            try:
                r_consent_post = session.post(
                    consent_url, json=consent_payload,
                    headers=consent_h, verify=False, timeout=30,
                    allow_redirects=False,
                )
                if r_consent_post.status_code in (301, 302, 303, 307, 308):
                    loc2 = r_consent_post.headers.get("Location", "")
                    auth_code = _extract_code_from_url(loc2)
                    if not auth_code:
                        consent_url = loc2 if loc2.startswith("http") else f"{oauth_issuer}{loc2}"
                elif r_consent_post.status_code == 200:
                    try:
                        cdata = r_consent_post.json()
                        redirect_to = str(cdata.get("redirectTo") or cdata.get("redirect_url") or "")
                        if redirect_to:
                            auth_code = _extract_code_from_url(redirect_to)
                            if not auth_code:
                                consent_url = redirect_to
                    except Exception:
                        pass
            except requests.exceptions.ConnectionError as e:
                m = re.search(r"(https?://localhost[^\s'\"&]+)", str(e))
                if m:
                    auth_code = _extract_code_from_url(m.group(1))
        else:
            # 可能是其他页面，尝试跟随重定向
            auth_code = _extract_code_from_url(str(resp_consent.url))
            if not auth_code:
                auth_code = _follow_and_extract_code(session, str(resp_consent.url), oauth_issuer)
    except requests.exceptions.ConnectionError as e:
        m = re.search(r"(https?://localhost[^\s'\"]+)", str(e))
        if m:
            auth_code = _extract_code_from_url(m.group(1))
    except Exception:
        pass

    # 如果普通重定向拿不到 code，尝试 workspace/select 流程
    if not auth_code:
        session_data = _decode_auth_session_cookie(session)
        workspace_id = None
        if session_data:
            workspaces = session_data.get("workspaces", [])
            if isinstance(workspaces, list) and workspaces:
                workspace_id = (workspaces[0] or {}).get("id")

        if workspace_id:
            h_ws: Dict[str, str] = dict(COMMON_HEADERS)
            h_ws["referer"] = consent_url
            h_ws["oai-device-id"] = device_id
            h_ws.update(generate_datadog_trace())
            try:
                resp_ws = session.post(
                    f"{oauth_issuer}/api/accounts/workspace/select",
                    json={"workspace_id": workspace_id},
                    headers=h_ws, verify=False, timeout=30, allow_redirects=False,
                )
                if resp_ws.status_code in (301, 302, 303, 307, 308):
                    loc = resp_ws.headers.get("Location", "")
                    auth_code = _extract_code_from_url(loc)
                    if not auth_code:
                        auth_code = _follow_and_extract_code(session, loc, oauth_issuer)
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
                            body: Dict[str, str] = {"org_id": org_id}
                            if project_id:
                                body["project_id"] = project_id
                            h_org: Dict[str, str] = dict(COMMON_HEADERS)
                            h_org["referer"] = org_url
                            h_org["oai-device-id"] = device_id
                            h_org.update(generate_datadog_trace())
                            resp_org = session.post(
                                f"{oauth_issuer}/api/accounts/organization/select",
                                json=body, headers=h_org, verify=False,
                                timeout=30, allow_redirects=False,
                            )
                            if resp_org.status_code in (301, 302, 303, 307, 308):
                                loc = resp_org.headers.get("Location", "")
                                auth_code = _extract_code_from_url(loc)
                                if not auth_code:
                                    auth_code = _follow_and_extract_code(session, loc, oauth_issuer)
                            elif resp_org.status_code == 200:
                                org_data = resp_org.json()
                                org_next = str(org_data.get("continue_url") or "")
                                if org_next:
                                    full_next = org_next if org_next.startswith("http") else f"{oauth_issuer}{org_next}"
                                    auth_code = _follow_and_extract_code(session, full_next, oauth_issuer)
                        else:
                            auth_code = _follow_and_extract_code(session, org_url, oauth_issuer)
                    else:
                        if ws_next:
                            full_next = ws_next if ws_next.startswith("http") else f"{oauth_issuer}{ws_next}"
                            auth_code = _follow_and_extract_code(session, full_next, oauth_issuer)
            except Exception:
                pass

    # 最终 fallback: 带重定向再跟一次
    if not auth_code:
        try:
            resp_fallback = session.get(
                consent_url, headers=NAVIGATE_HEADERS,
                verify=False, timeout=30, allow_redirects=True,
            )
            auth_code = _extract_code_from_url(str(resp_fallback.url))
            if not auth_code and resp_fallback.history:
                for hist in resp_fallback.history:
                    loc = hist.headers.get("Location", "")
                    auth_code = _extract_code_from_url(loc)
                    if auth_code:
                        break
        except requests.exceptions.ConnectionError as e:
            m = re.search(r"(https?://localhost[^\s'\"]+)", str(e))
            if m:
                auth_code = _extract_code_from_url(m.group(1))
        except Exception:
            pass

    if not auth_code:
        logger.warning("[Codex登录] 未能获取 auth_code | email=%s", email)
        return None

    # Step F: 用 code 换取 token
    return _exchange_code_for_token(
        auth_code, code_verifier,
        oauth_issuer=oauth_issuer,
        oauth_client_id=oauth_client_id,
        oauth_redirect_uri=oauth_redirect_uri,
        proxy=proxy,
    )


def _exchange_code_for_token(
    code: str,
    code_verifier: str,
    oauth_issuer: str = OPENAI_AUTH_BASE,
    oauth_client_id: str = OAUTH_CLIENT_ID,
    oauth_redirect_uri: str = OAUTH_REDIRECT_URI,
    proxy: str = "",
) -> Optional[Dict[str, Any]]:
    """用 authorization_code 换取 access_token/refresh_token"""
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
        logger.warning("token 交换失败: HTTP %s | %s", resp.status_code, resp.text[:200])
        return None
    except Exception as e:
        logger.warning("token 交换异常: %s", e)
        return None


# ============================================================
# ⑫ JWT 解码（不验签）
# ============================================================

def decode_jwt_payload(token: str) -> Dict[str, Any]:
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


# ============================================================
# ⑬ CPA 管理 API：上传 token 文件 / 构建请求头
# ============================================================

def _cpa_headers() -> Dict[str, str]:
    key = (CLI_PROXY_PASSWORD or "").strip()
    h = {"Content-Type": "application/json"}
    if key:
        h["Authorization"] = f"Bearer {key}"
        h["X-Management-Key"] = key
    return h


def upload_token_to_cpa(email: str, token_data: Dict[str, Any]) -> bool:
    """
    将 token JSON 文件上传到 CPA 管理 API。
    接口：POST /v0/management/auth-files（multipart/form-data）
    """
    upload_url = f"{CLI_PROXY_API_BASE}/v0/management/auth-files"
    filename = f"{email}.json"
    content = json.dumps(token_data, ensure_ascii=False).encode("utf-8")
    try:
        resp = http_session.post(
            upload_url,
            files={"file": (filename, content, "application/json")},
            headers={"Authorization": f"Bearer {CLI_PROXY_PASSWORD}", "X-Management-Key": CLI_PROXY_PASSWORD},
            timeout=30,
            verify=False,
        )
        if resp.status_code == 200:
            logger.info("✅ token 上传 CPA 成功: %s", email)
            return True
        logger.warning("token 上传 CPA 失败: HTTP %s | %s", resp.status_code, resp.text[:200])
        return False
    except Exception as e:
        logger.warning("token 上传 CPA 异常: %s", e)
        return False


def build_token_dict(email: str, tokens: Dict[str, Any]) -> Dict[str, Any]:
    """构造标准 token JSON（与 gptzidong 格式兼容）"""
    access_token = str(tokens.get("access_token") or "")
    refresh_token = str(tokens.get("refresh_token") or "")
    id_token = str(tokens.get("id_token") or "")

    payload = decode_jwt_payload(access_token)
    auth_info = payload.get("https://api.openai.com/auth", {})
    account_id = auth_info.get("chatgpt_account_id", "") if isinstance(auth_info, dict) else ""

    exp_timestamp = payload.get("exp", 0)
    now = dt.datetime.now(tz=dt.timezone(dt.timedelta(hours=8)))
    expired_str = ""
    if exp_timestamp:
        exp_dt = dt.datetime.fromtimestamp(exp_timestamp, tz=dt.timezone(dt.timedelta(hours=8)))
        expired_str = exp_dt.strftime("%Y-%m-%dT%H:%M:%S+08:00")

    return {
        "type": "codex",
        "email": email,
        "expired": expired_str,
        "id_token": id_token,
        "account_id": account_id,
        "access_token": access_token,
        "last_refresh": now.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "refresh_token": refresh_token,
    }



# ============================================================
# ⑭ 母号 Session 自动拉取（chatgpt.com 专用 HTTP 登录）
# ============================================================
# chatgpt.com 使用 NextAuth 体系，与 Codex OAuth 完全不同：
#   1. 访问 chatgpt.com/auth/login → 跳转 auth.openai.com 获取 login_session
#   2. 提交邮箱（authorize/continue）
#   3. 提交密码（password/verify）或 OTP
#   4. consent redirect → chatgpt.com/api/auth/callback/openai
#   5. 调用 /api/auth/session 获取 accessToken + organizationId

CHATGPT_BASE = "https://chatgpt.com"
CHATGPT_AUTH_CALLBACK = "https://chatgpt.com/api/auth/callback/openai"
CHATGPT_OAUTH_CLIENT_ID = "pdlLIX2Y72MIl2rhLhTE9VV9bN905kBh"
CHATGPT_OAUTH_REDIRECT_URI = "https://chatgpt.com/api/auth/callback/openai"


def chatgpt_http_login(
    email: str,
    password: str = "",
    cf_token: str = "",
    proxy: str = "",
    tag: str = "",         # 日志前缀：显示车头名称（如 "1" / "2"）
) -> Tuple[str, str]:
    """
    chatgpt.com 专用 HTTP 登录（NextAuth 体系）。
    返回 (access_token, org_id)，失败返回 ("", "")

    流程：
      A. GET chatgpt.com 首页 → POST chatgpt.com/api/auth/signin/openai（CSRF）
         → 跟随重定向到 auth.openai.com/oauth/authorize
         → auth.openai.com 设置 login_session Cookie
      B. 提交邮箱（authorize/continue），origin=auth.openai.com
      C. 提交密码 or 触发 OTP
      D. OTP 验证（可选）
      E. 跟随 consent 重定向到 chatgpt.com
      F. 读取 /api/auth/session
    """
    session = create_session(proxy=proxy)
    device_id = str(uuid.uuid4())
    tag = tag or email   # 日志前缀：优先用车头名称，否则用邮箱

    session.cookies.set("oai-did", device_id, domain=".chatgpt.com")
    session.cookies.set("oai-did", device_id, domain="chatgpt.com")
    session.cookies.set("oai-did", device_id, domain=".auth.openai.com")

    # ── Step A: NextAuth CSRF + OAuth authorize ──
    logger.info(f"  [{tag}] 获取 NextAuth CSRF token | email=%s", email)
    try:
        # 1. 访问首页建立 chatgpt.com session
        session.get(
            CHATGPT_BASE,
            headers=NAVIGATE_HEADERS,
            allow_redirects=True,
            verify=False,
            timeout=20,
        )
    except Exception:
        pass

    # 2. 获取 NextAuth CSRF token
    csrf_token = ""
    try:
        resp_csrf = session.get(
            f"{CHATGPT_BASE}/api/auth/csrf",
            headers={
                "accept": "application/json",
                "referer": f"{CHATGPT_BASE}/",
                "user-agent": USER_AGENT,
                "x-requested-with": "XMLHttpRequest",
            },
            verify=False,
            timeout=15,
        )
        if resp_csrf.status_code == 200:
            csrf_token = str(resp_csrf.json().get("csrfToken") or "")
            logger.info(f"  [{tag}] CSRF token 获取成功: %s...", csrf_token[:8])
    except Exception as e:
        logger.warning(f"  [{tag}] CSRF 获取失败: %s（继续尝试）", e)

    # 3. POST signin/openai 触发 OAuth 重定向（携带 CSRF）
    try:
        signin_headers = {
            "accept": "application/json",
            "content-type": "application/x-www-form-urlencoded",
            "origin": CHATGPT_BASE,
            "referer": f"{CHATGPT_BASE}/auth/login",
            "user-agent": USER_AGENT,
        }
        signin_data: Dict[str, str] = {"callbackUrl": CHATGPT_BASE}
        if csrf_token:
            signin_data["csrfToken"] = csrf_token
        # 先用 GET 尝试（有些版本），再 POST
        resp_signin = session.post(
            f"{CHATGPT_BASE}/api/auth/signin/openai",
            data=signin_data,
            headers=signin_headers,
            allow_redirects=True,
            verify=False,
            timeout=30,
        )
        logger.info(f"  [{tag}] signin/openai %s → %s",
                    resp_signin.status_code, str(resp_signin.url)[:60])
    except Exception as e:
        logger.warning(f"  [{tag}] signin/openai 异常: %s（继续）", e)

    # 4. 确认已到 auth.openai.com 并有 login_session Cookie
    has_login_session = any(c.name == "login_session" for c in session.cookies)
    logger.info(f"  [{tag}] login_session Cookie: %s", "✅ 存在" if has_login_session else "❌ 未获取")

    if not has_login_session:
        # fallback：直接构建 authorize URL 访问
        logger.info(f"  [{tag}] 尝试直接访问 authorize URL...")
        try:
            code_verifier_fb, code_challenge_fb = generate_pkce()
            auth_params_fb = {
                "response_type": "code",
                "client_id": CHATGPT_OAUTH_CLIENT_ID,
                "redirect_uri": CHATGPT_OAUTH_REDIRECT_URI,
                "scope": "openid profile email offline_access",
                "code_challenge": code_challenge_fb,
                "code_challenge_method": "S256",
                "state": secrets.token_urlsafe(32),
            }
            auth_url_fb = f"{OPENAI_AUTH_BASE}/oauth/authorize?{urlencode(auth_params_fb)}"
            session.get(
                auth_url_fb, headers=NAVIGATE_HEADERS,
                allow_redirects=True, verify=False, timeout=30,
            )
            has_login_session = any(c.name == "login_session" for c in session.cookies)
            if has_login_session:
                logger.info(f"  [{tag}] fallback 成功获取 login_session ✅")
        except Exception as e:
            logger.warning(f"  [{tag}] fallback 失败: %s", e)

    if not has_login_session:
        logger.warning(f"  [{tag}] 无法获取 login_session Cookie，可能被风控 | email=%s", email)
        return "", ""

    # ── Step B: 提交邮箱（origin 必须是 auth.openai.com）──
    logger.info(f"  [{tag}] 提交邮箱 | email=%s", email)
    h_b: Dict[str, str] = {
        "accept": "application/json",
        "content-type": "application/json",
        "origin": OPENAI_AUTH_BASE,
        "referer": f"{OPENAI_AUTH_BASE}/log-in",
        "user-agent": USER_AGENT,
        "oai-device-id": device_id,
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
    }
    h_b.update(generate_datadog_trace())
    sentinel_b = build_sentinel_token(session, device_id, flow="authorize_continue")
    if sentinel_b:
        h_b["openai-sentinel-token"] = sentinel_b
    try:
        resp_b = session.post(
            f"{OPENAI_AUTH_BASE}/api/accounts/authorize/continue",
            json={"username": {"kind": "email", "value": email}},
            headers=h_b, verify=False, timeout=30,
        )
        if resp_b.status_code != 200:
            logger.warning(f"  [{tag}] 失败: HTTP %s | %s",
                           resp_b.status_code, resp_b.text[:200])
            return "", ""
        # 解析 Step B 返回的 continue_url 并 GET 跟随（推进状态机）
        try:
            b_data = resp_b.json()
            b_continue = str(b_data.get("continue_url") or "")
            b_page_type = str(((b_data.get("page") or {}).get("type")) or "")
        except Exception:
            b_continue = ""
            b_page_type = ""
        # 判断走哪个分支：OTP 还是密码
        if "email-verification" in b_continue or b_page_type == "email_otp_verification":
            # 服务端要求 OTP（该账号启用了邮箱验证登录）
            next_step = "otp"
        else:
            next_step = "password"
        logger.info(f"  [{tag}] 登录方式: %s | continue_url=%s",
                    next_step, b_continue[:50])
        if b_continue:
            if b_continue.startswith("/"):
                b_continue_full = f"{OPENAI_AUTH_BASE}{b_continue}"
            else:
                b_continue_full = b_continue
            try:
                session.get(
                    b_continue_full,
                    headers=NAVIGATE_HEADERS,
                    allow_redirects=True,
                    verify=False,
                    timeout=15,
                )
                logger.info(f"  [{tag}] 跟随 continue_url → %s", b_continue_full[:60])
            except Exception:
                pass
        logger.info(f"  [{tag}] ✅ 邮箱提交 + 页面跳转完成")
    except Exception as e:
        logger.warning(f"  [{tag}] 异常: %s", e)
        return "", ""

    continue_url = b_continue if b_continue else ""
    page_type = b_page_type if b_page_type else ""

    # ── Step C: 密码 OR OTP（根据 Step B 实际跳转目标决定）──
    if next_step == "password" and password:
        logger.info(f"  [{tag}] 提交密码 | email=%s", email)
        h2: Dict[str, str] = {
            "accept": "application/json",
            "content-type": "application/json",
            "origin": OPENAI_AUTH_BASE,
            "referer": f"{OPENAI_AUTH_BASE}/log-in/password",
            "user-agent": USER_AGENT,
            "oai-device-id": device_id,
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
        }
        h2.update(generate_datadog_trace())
        sentinel2 = build_sentinel_token(session, device_id, flow="password_verify")
        if sentinel2:
            h2["openai-sentinel-token"] = sentinel2
        try:
            resp_c = session.post(
                f"{OPENAI_AUTH_BASE}/api/accounts/password/verify",
                json={"password": password},
                headers=h2, verify=False, timeout=30, allow_redirects=False,
            )
            if resp_c.status_code != 200:
                logger.warning(f"  [{tag}] 密码失败: HTTP %s | %s", resp_c.status_code, resp_c.text[:200])
                return "", ""
            data_c = resp_c.json()
            continue_url = str(data_c.get("continue_url") or "")
            page_type = str(((data_c.get("page") or {}).get("type")) or "")
            logger.info(f"  [{tag}] ✅ 密码验证成功")
        except Exception as e:
            logger.warning(f"  [{tag}] 密码异常: %s", e)
            return "", ""
    else:
        # 无密码 OR 服务端要求OTP：触发邮箱 OTP
        if next_step == "otp" and password:
            logger.info(f"  [{tag}] 账号要求OTP登录（忽略密码）| email=%s", email)
        if not cf_token:
            logger.warning(f"  [{tag}] 无密码且无 cf_token，无法获取OTP | email=%s", email)
            return "", ""
        logger.info(f"  [{tag}] OTP模式：触发发送 OTP 邮件 | email=%s", email)
        # 必须主动请求服务端发送 OTP（访问页面不会自动发送）
        h_otp_trigger: Dict[str, str] = {
            "accept": "application/json",
            "content-type": "application/json",
            "origin": OPENAI_AUTH_BASE,
            "referer": f"{OPENAI_AUTH_BASE}/email-verification",
            "user-agent": USER_AGENT,
            "oai-device-id": device_id,
        }
        h_otp_trigger.update(generate_datadog_trace())
        sentinel_trigger = build_sentinel_token(session, device_id, flow="email_otp")
        if sentinel_trigger:
            h_otp_trigger["openai-sentinel-token"] = sentinel_trigger
        try:
            r_trigger = session.post(
                f"{OPENAI_AUTH_BASE}/api/accounts/email-otp/init",
                json={}, headers=h_otp_trigger, verify=False, timeout=30,
            )
            logger.info(f"  [{tag}] OTP 触发响应: HTTP %s", r_trigger.status_code)
        except Exception as e:
            logger.warning(f"  [{tag}] OTP 触发失败（继续等待）: %s", e)
        # page_type、continue_url 不变，Step D 负责等待和提交 OTP
        h_otp: Dict[str, str] = {
            "accept": "application/json",
            "content-type": "application/json",
            "origin": OPENAI_AUTH_BASE,
            "referer": f"{OPENAI_AUTH_BASE}/log-in",
            "user-agent": USER_AGENT,
            "oai-device-id": device_id,
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
        }
        h_otp.update(generate_datadog_trace())
        sentinel_otp = build_sentinel_token(session, device_id, flow="email_otp")
        if sentinel_otp:
            h_otp["openai-sentinel-token"] = sentinel_otp
        try:
            session.post(f"{OPENAI_AUTH_BASE}/api/accounts/email-otp/init",
                         json={}, headers=h_otp, verify=False, timeout=30)
        except Exception:
            try:
                session.get(f"{OPENAI_AUTH_BASE}/api/accounts/email-otp/send",
                            headers=h_otp, verify=False, timeout=30)
            except Exception:
                pass
        page_type = "email_otp_verification"
        continue_url = f"{OPENAI_AUTH_BASE}/email-verification"

    # ── Step D（可选）：邮箱 OTP 验证 ──
    if page_type == "email_otp_verification" or "email-verification" in continue_url:
        if not cf_token:
            logger.warning(f"  [{tag}] 需要OTP但无cf_token | email=%s", email)
            return "", ""
        logger.info(f"  [{tag}] 等待OTP | email=%s", email)
        h_v: Dict[str, str] = {
            "accept": "application/json",
            "content-type": "application/json",
            "origin": OPENAI_AUTH_BASE,
            "referer": f"{OPENAI_AUTH_BASE}/email-verification",
            "user-agent": USER_AGENT,
            "oai-device-id": device_id,
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
        }
        h_v.update(generate_datadog_trace())
        tried: set = set()
        start = time.time()
        got_code = False
        while time.time() - start < 120:
            for item in fetch_emails_list(cf_token):
                if not isinstance(item, dict):
                    continue
                c = _extract_otp_from_raw(str(item.get("raw") or ""))
                if c and c not in tried:
                    tried.add(c)
                    rv = session.post(
                        f"{OPENAI_AUTH_BASE}/api/accounts/email-otp/validate",
                        json={"code": c}, headers=h_v, verify=False, timeout=30,
                    )
                    if rv.status_code == 200:
                        try:
                            d2 = rv.json()
                            continue_url = str(d2.get("continue_url") or continue_url)
                            page_type = str(((d2.get("page") or {}).get("type")) or "")
                        except Exception:
                            pass
                        got_code = True
                        logger.info(f"  [{tag}] OTP验证成功: %s", c)
                        break
            if got_code:
                break
            time.sleep(3)
        if not got_code:
            logger.warning(f"  [{tag}] OTP超时 | email=%s", email)
            return "", ""

    # ── Step E: 跟随 consent 重定向 → chatgpt.com session ──
    if not continue_url:
        logger.warning(f"  [{tag}] 无 continue_url | email=%s", email)
        return "", ""

    if continue_url.startswith("/"):
        consent_url = f"{OPENAI_AUTH_BASE}{continue_url}"
    else:
        consent_url = continue_url

    logger.info(f"  [{tag}] 跟随 consent 重定向 | url=%s", consent_url[:80])
    final_url = ""
    try:
        # 跟随重定向一直到 chatgpt.com，让 NextAuth callback 建立 session
        resp_e = session.get(
            consent_url,
            headers=NAVIGATE_HEADERS,
            allow_redirects=True,
            verify=False,
            timeout=30,
        )
        final_url = str(resp_e.url)
        logger.info(f"  [{tag}] 最终落地: %s", final_url[:80])
    except Exception as e:
        logger.warning(f"  [{tag}] 重定向异常: %s", e)
        pass

    # 如果落地不在 chatgpt.com，需要做 workspace/select 来获取 chatgpt.com callback redirect
    if "chatgpt.com" not in final_url:
        logger.info(f"  [{tag}] 落地非 chatgpt.com，执行 workspace/select 流程...")
        try:
            # 解析 oai-client-auth-session cookie 获取 workspace 信息
            ws_id = None
            for ck in session.cookies:
                if ck.name == "oai-client-auth-session":
                    import base64 as _b64
                    part = ck.value.split(".")[0] if "." in ck.value else ck.value
                    pad = 4 - len(part) % 4
                    try:
                        raw = _b64.urlsafe_b64decode(part + ("=" * (pad if pad != 4 else 0)))
                        cd = json.loads(raw)
                        logger.info(f"  [{tag}] auth-session: %s", str(cd)[:150])
                        ws_list = cd.get("workspaces", [])
                        if ws_list and isinstance(ws_list[0], dict):
                            ws_id = ws_list[0].get("id")
                    except Exception as ex:
                        logger.info(f"  [{tag}] cookie parse: %s", ex)
                    break

            # 如果 cookie 里没有，试着读 workspace 列表 API
            if not ws_id:
                r_ws_list = session.get(
                    f"{OPENAI_AUTH_BASE}/api/accounts/workspace",
                    headers={"accept": "application/json", "user-agent": USER_AGENT, "oai-device-id": device_id},
                    verify=False, timeout=15)
                logger.info(f"  [{tag}] workspace list: HTTP %s | %s",
                            r_ws_list.status_code, r_ws_list.text[:150])
                if r_ws_list.status_code == 200:
                    wl = r_ws_list.json()
                    if isinstance(wl, list) and wl:
                        ws_id = (wl[0] or {}).get("id")
                    elif isinstance(wl, dict):
                        ws_id = wl.get("id") or (wl.get("workspaces") or [{}])[0].get("id")

            # POST /api/accounts/workspace/select
            ws_body = {"workspace_id": ws_id} if ws_id else {}
            logger.info(f"  [{tag}] POST workspace/select | body=%s", ws_body)
            r_ws_sel = session.post(
                f"{OPENAI_AUTH_BASE}/api/accounts/workspace/select",
                json=ws_body,
                headers={"accept": "application/json", "content-type": "application/json",
                         "origin": OPENAI_AUTH_BASE, "user-agent": USER_AGENT,
                         "oai-device-id": device_id},
                allow_redirects=False, verify=False, timeout=30)
            logger.info(f"  [{tag}] workspace/select: HTTP %s | Location=%s | body=%s",
                        r_ws_sel.status_code,
                        r_ws_sel.headers.get("Location", "")[:80],
                        r_ws_sel.text[:200])

            # 跟随 continue_url 或 Location redirect
            ws_next = ""
            try:
                ws_data = r_ws_sel.json()
                ws_next = str(ws_data.get("continue_url") or "")
            except: pass
            loc = r_ws_sel.headers.get("Location", "")
            ws_next = ws_next or loc

            if ws_next:
                if not ws_next.startswith("http"):
                    ws_next = f"{OPENAI_AUTH_BASE}{ws_next}"
                logger.info(f"  [{tag}] 跟随 ws_next: %s", ws_next[:80])
                r_ws_redir = session.get(
                    ws_next, headers={"user-agent": USER_AGENT},
                    allow_redirects=True, verify=False, timeout=30)
                logger.info(f"  [{tag}] ws_next 落地: %s", str(r_ws_redir.url)[:80])
        except Exception as e:
            logger.warning(f"  [{tag}] workspace/select 异常: %s", e)



    # ── Step F: 读取 chatgpt.com session ──
    logger.info(f"  [{tag}] 读取 /api/auth/session | email=%s", email)
    try:
        resp_s = session.get(
            f"{CHATGPT_BASE}/api/auth/session",
            headers={
                "accept": "application/json",
                "referer": f"{CHATGPT_BASE}/",
                "user-agent": USER_AGENT,
            },
            verify=False,
            timeout=20,
        )
        if resp_s.status_code == 200:
            sdata = resp_s.json()
            access_token = str(sdata.get("accessToken") or "")
            acct = sdata.get("account") or {}
            # account.id 是 UUID（邀请 API 路径使用），organizationId 是 org-xxx（Header 使用）
            acct_uuid = str(acct.get("id") or "")
            org_id = str(acct.get("organizationId") or "")
            # 优先用 UUID，其次用 org_id
            primary_id = acct_uuid or org_id
            if access_token and primary_id:
                logger.info(f"  [{tag}] ✅ 获取成功 | uuid=%s | org_id=%s", acct_uuid, org_id)
                # 返回 (access_token, account_uuid) 给 refresh_team_session_http 存储
                return access_token, acct_uuid or org_id
            elif access_token:
                # org_id 从 JWT 中解析
                payload = decode_jwt_payload(access_token)
                auth_info = payload.get("https://api.openai.com/auth", {})
                if isinstance(auth_info, dict):
                    org_id = str(auth_info.get("organization_id") or auth_info.get("chatgpt_account_id") or "")
                if org_id:
                    logger.info(f"  [{tag}] ✅ 从 JWT 获取 org_id=%s", org_id)
                    return access_token, org_id
            logger.warning(f"  [{tag}] session 不完整: %s", str(sdata)[:200])
        else:
            logger.warning(f"  [{tag}] session HTTP %s | %s", resp_s.status_code, resp_s.text[:150])
    except Exception as e:
        logger.warning(f"  [{tag}] session 异常: %s", e)

    return "", ""


def refresh_team_session_http(team):
    """
    通过纯 HTTP OAuth 登录母号（chatgpt.com OAuth），
    获取 account_id 和 auth_token 并写回 team 字典。
    - 有 password：走密码登录流程
    - 无 password：直接走邮箱 OTP 登录流程（母号邮箱必须在自建 Worker 中）
    """
    m_email = team.get("email", "")
    m_password = team.get("password", "")  # 可为空，无密码时走 OTP
    if not m_email:
        logger.error("母号未配置 email，无法登录")
        return False

    mode_str = "密码登录" if m_password else "无密码OTP登录"
    logger.info("🔄 HTTP 刷新母号 session [%s]: %s", mode_str, m_email)

    # 获取母号邮箱 JWT（用于 OTP 拉取）
    # 优先使用 config.yaml 中直接配置的 jwt（最简单稳定）
    mother_jwt = team.get("jwt", "").strip()
    if not mother_jwt:
        mother_jwt = _get_jwt_for_address(m_email)
    if mother_jwt:
        logger.info("母号邮箱 JWT 已获取 (%s) | email=%s",
                    "config配置" if team.get("jwt") else "Worker API", m_email)
    if not m_password and not mother_jwt:
        logger.error("无密码模式下无法获取母号邮箱JWT | email=%s", m_email)
        return False


    # 使用 chatgpt.com 专用登录函数
    access_token, org_id = chatgpt_http_login(
        email=m_email,
        password=m_password,
        cf_token=mother_jwt,
        tag=team.get("name", m_email),
    )

    if access_token and org_id:
        team["auth_token"] = f"Bearer {access_token}"
        team["account_id"] = org_id
        logger.info("✅ 母号 token 刷新成功 | account_id=%s | email=%s", org_id, m_email)
        return True

    logger.warning("母号 session 获取失败 | email=%s", m_email)
    return False


# ============================================================
# ⑮ 团队邀请管理
# ============================================================

_tracker_lock = threading.Lock()


def load_invite_tracker():
    if os.path.exists(INVITE_TRACKER_FILE):
        try:
            with open(INVITE_TRACKER_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning("读取 invite tracker 失败: %s", e)
    return {"teams": {team["email"]: [] for team in TEAMS}}


def save_invite_tracker(tracker):
    try:
        with open(INVITE_TRACKER_FILE, "w", encoding="utf-8") as f:
            json.dump(tracker, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("保存 invite tracker 失败: %s", e)


def get_available_team(tracker):
    for team in TEAMS:
        invited = tracker["teams"].get(team["email"], [])
        if len(invited) < team.get("max_invites", 3):
            return team
    return None


def invite_to_team(email, team):
    """发送团队邀请，token 失效（401）时自动刷新后重试一次"""
    if not team.get("account_id") or not team.get("auth_token"):
        if not refresh_team_session_http(team):
            logger.error("未能获取母号 session，跳过邀请: %s", email)
            return False

    account_id = team["account_id"]  # 应为 UUID 格式
    invite_url = f"https://chatgpt.com/backend-api/accounts/{account_id}/invites"

    for attempt in range(2):
        headers = {
            "accept": "*/*",
            "accept-language": "en-US,en;q=0.9",
            "authorization": team["auth_token"],
            "chatgpt-account-id": account_id,
            "content-type": "application/json",
            "origin": "https://chatgpt.com",
            "referer": "https://chatgpt.com/",
            "user-agent": USER_AGENT,
        }
        payload = {
            "email_addresses": [email],
            "role": "standard-user",
            "resend_emails": True,
        }
        try:
            resp = http_session.post(invite_url, headers=headers, json=payload, timeout=15)
            if resp.status_code == 200:
                result = resp.json()
                if result.get("account_invites"):
                    logger.info("✅ 邀请成功: %s → %s", email, team["name"])
                    return True
                elif result.get("errored_emails"):
                    logger.warning("邀请出错: %s | %s", email, result["errored_emails"])
                    return False
                logger.warning("邀请响应异常: %s", result)
                return False
            elif resp.status_code == 401 and attempt == 0:
                logger.info("Token 已过期，刷新后重试...")
                if not refresh_team_session_http(team):
                    return False
                continue
            logger.warning("邀请失败: HTTP %s | %s", resp.status_code, resp.text[:200])
            return False
        except Exception as e:
            logger.warning("邀请请求异常: %s", e)
            return False
    return False


def auto_invite_to_team(email):
    """线程安全地选择可用车头并发送邀请"""
    with _tracker_lock:
        tracker = load_invite_tracker()
        for team_key, emails in tracker["teams"].items():
            if email in emails:
                logger.info("⚠️ %s 已邀请，跳过", email)
                return False
        team = get_available_team(tracker)
        if not team:
            logger.warning("所有车头已满，无可用名额")
            return False
        team_key = team["email"]
        if team_key not in tracker["teams"]:
            tracker["teams"][team_key] = []
        tracker["teams"][team_key].append(email)
        save_invite_tracker(tracker)

    ok = invite_to_team(email, team)
    if not ok:
        with _tracker_lock:
            tracker = load_invite_tracker()
            lst = tracker["teams"].get(team_key, [])
            if email in lst:
                lst.remove(email)
            save_invite_tracker(tracker)
    else:
        invited_count = len(tracker["teams"].get(team_key, []))
        logger.info("车头状态: %s %d/%d", team.get("name"), invited_count, team.get("max_invites", 3))
    return ok


# ============================================================
# ⑯ CSV 保存
# ============================================================

_csv_lock = threading.Lock()


def save_to_txt(email, password):
    """保存账号到 TXT（一行一个：email|password|时间）"""
    with _csv_lock:
        with open(ACCOUNTS_FILE, "a", encoding="utf-8") as f:
            f.write(f"{email}|{password}|{time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    logger.info("📄 已保存: %s → %s", email, ACCOUNTS_FILE)


# ============================================================
# ⑰ 单账号注册主流程
# ============================================================

def register_one_account(proxy=""):
    """
    完整注册一个新子账号：
    1. 创建临时邮箱
    2. HTTP 五步注册（ProtocolRegistrar）
    3. 发送团队邀请（如有车头）
    4. HTTP OAuth 登录获取 Codex token
    5. 上传 token 到 CPA + 保存本地
    """
    # 1. 创建临时邮箱
    email, jwt_token = create_temp_email()
    if not email:
        logger.error("创建临时邮箱失败，跳过")
        return None, None, False

    password = generate_random_password()
    logger.info("=" * 60)
    logger.info("📧 邮箱: %s", email)

    # 2. HTTP 五步注册
    registrar = ProtocolRegistrar(proxy=proxy)
    reg_ok = registrar.register(email=email, jwt_token=jwt_token, password=password)
    if not reg_ok:
        logger.error("❌ 注册失败 | email=%s", email)
        save_to_txt(email, password)
        return email, password, False

    logger.info("✅ 注册成功 | email=%s", email)
    save_to_txt(email, password)
    time.sleep(3)

    # 3. 团队邀请
    invited = False
    if TEAMS:
        logger.info("📨 发送团队邀请 | email=%s", email)
        invited = auto_invite_to_team(email)
        if not invited:
            logger.warning("⚠️ 邀请失败，继续尝试获取 Codex token | email=%s", email)

    if invited:
        logger.info("⏳ 等待邀请生效 (5s)...")
        time.sleep(5)

    # 4. HTTP 登录获取 Codex token（最多重试 3 次）
    logger.info("🔑 HTTP 登录获取 Codex token | email=%s", email)
    tokens = None
    for attempt in range(1, 4):
        tokens = perform_http_oauth_login(
            email=email,
            password=password,
            cf_token=jwt_token,
            worker_domain=TEMP_MAIL_WORKER_DOMAIN,
            oauth_issuer=OPENAI_AUTH_BASE,
            oauth_client_id=OAUTH_CLIENT_ID,
            oauth_redirect_uri=OAUTH_REDIRECT_URI,
            proxy=proxy,
        )
        if tokens:
            break
        if attempt < 3:
            logger.warning("⚠️ Codex 登录第 %d 次失败，5s 后重试... | email=%s", attempt, email)
            time.sleep(5)

    if not tokens:
        logger.warning("❌ Codex 登录失败（注册已成功）| email=%s", email)
        return email, password, True

    # 5. 上传到 CPA（按 upload_enabled 开关）+ 保存本地
    token_dict = build_token_dict(email, tokens)
    if CPA_UPLOAD_ENABLED:
        upload_token_to_cpa(email, token_dict)
    else:
        logger.info("⏭️ 跳过 CPA 上传（upload_enabled=false）| email=%s", email)

    local_dir = "output_tokens"
    os.makedirs(local_dir, exist_ok=True)
    token_file = os.path.join(local_dir, f"{email}.json")
    try:
        with open(token_file, "w", encoding="utf-8") as f:
            json.dump(token_dict, f, ensure_ascii=False, indent=2)
        logger.info("📁 token 已保存本地: %s", token_file)
    except Exception as e:
        logger.warning("本地保存 token 失败: %s", e)

    logger.info("🎉 完整流程成功: %s", email)
    return email, password, True


# ============================================================
# ⑱ 批量注册入口
# ============================================================

def run_batch():
    logger.info("=" * 60)
    logger.info("🚀 开始批量注册，目标账号数: %d", TOTAL_ACCOUNTS)
    logger.info("=" * 60)

    if TEAMS:
        logger.info("� 已配置 %d 个车头，session 按需获取", len(TEAMS))
    else:
        logger.warning("⚠️ 未配置任何车头，将跳过邀请步骤\n")

    success_count = 0
    fail_count = 0
    registered = []

    for i in range(TOTAL_ACCOUNTS):
        logger.info("#" * 60)
        logger.info("📝 注册账号 %d/%d", i + 1, TOTAL_ACCOUNTS)
        logger.info("#" * 60)

        email, password, success = register_one_account()

        if success:
            success_count += 1
            if email:
                registered.append(email)
        else:
            fail_count += 1

        logger.info("-" * 40)
        logger.info("📊 进度: %d/%d | ✅成功: %d | ❌失败: %d",
                    i + 1, TOTAL_ACCOUNTS, success_count, fail_count)
        logger.info("-" * 40)

        if i < TOTAL_ACCOUNTS - 1:
            wait_time = random.randint(5, 20)
            logger.info("⏳ 等待 %ds 后注册下一个...", wait_time)
            time.sleep(wait_time)

    logger.info("=" * 60)
    logger.info("🏁 批量注册完成")
    logger.info("   总计: %d | ✅成功: %d | ❌失败: %d", TOTAL_ACCOUNTS, success_count, fail_count)
    for e in registered:
        logger.info("     - %s", e)
    logger.info("=" * 60)


# ============================================================
# ⑲ 程序入口
# ============================================================

if __name__ == "__main__":
    run_batch()
