#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
get_tokens.py
=============
简化版：只做注册 + 获取 access_token
输出格式（results.txt）：email|email_jwt|password|access_token
配置文件：config.yaml（与 gpt-team-new.py 共用）
"""

from __future__ import annotations

import base64
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
import uuid
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlencode, urlparse

import requests
import urllib3
import yaml
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ============================================================
# 配置加载
# ============================================================
_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")


def _load_config() -> Dict[str, Any]:
    if not os.path.exists(_CONFIG_FILE):
        raise FileNotFoundError(f"找不到配置文件: {_CONFIG_FILE}")
    with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


_cfg = _load_config()

TOTAL_ACCOUNTS: int = int(_cfg.get("total_accounts", 1))
TEMP_MAIL_WORKER_DOMAIN: str = _cfg["temp_mail"]["worker_domain"]
TEMP_MAIL_EMAIL_DOMAINS: List[str] = _cfg["temp_mail"]["email_domains"]
TEMP_MAIL_ADMIN_PASSWORD: str = _cfg["temp_mail"]["admin_password"]
RESULTS_FILE: str = _cfg.get("output", {}).get("results_file", "results.txt")

# OAuth 常量
OPENAI_AUTH_BASE = "https://auth.openai.com"
OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OAUTH_REDIRECT_URI = "http://localhost:1455/auth/callback"

print(f"配置已加载 | 目标数量: {TOTAL_ACCOUNTS} | 邮箱域名: {TEMP_MAIL_EMAIL_DOMAINS}")

# ============================================================
# 日志
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("get-tokens")

# ============================================================
# HTTP 工具
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
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    return s


http_session = create_session()

# ============================================================
# PKCE
# ============================================================

def generate_pkce() -> Tuple[str, str]:
    code_verifier = (
        base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
    )
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


# ============================================================
# Datadog Trace 头（模拟浏览器）
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
# Sentinel Token（反机器人）
# ============================================================

class SentinelTokenGenerator:
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
            "vendorSub-undefined", "location", "Object",
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
    }
    try:
        resp = session.post(
            "https://sentinel.openai.com/backend-api/sentinel/req",
            data=json.dumps(body),
            headers=headers,
            timeout=15,
            verify=False,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data if isinstance(data, dict) else None
    except Exception:
        pass
    return None


def build_sentinel_token(
    session: requests.Session, device_id: str, flow: str = "authorize_continue"
) -> Optional[str]:
    challenge = fetch_sentinel_challenge(session, device_id, flow)
    if not challenge:
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
# 临时邮箱 API
# ============================================================

def create_temp_email() -> Tuple[Optional[str], Optional[str]]:
    """创建临时邮箱，返回 (email, jwt_token)"""
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


def _extract_otp(content: str) -> Optional[str]:
    """从邮件原始内容提取6位验证码"""
    if not content:
        return None
    m = re.search(r"background-color:\s*#F3F3F3[^>]*>[\s\S]*?(\d{6})[\s\S]*?</p>", content)
    if m:
        return m.group(1)
    for pat in [r">\s*(\d{6})\s*<", r"(?<![#&])\b(\d{6})\b"]:
        for code in re.findall(pat, content):
            if code != "177010":
                return code
    return None


def wait_for_otp(jwt_token: str, timeout: int = 120) -> Optional[str]:
    """轮询等待6位验证码"""
    seen_ids: set = set()
    start = time.time()
    while time.time() - start < timeout:
        for item in fetch_emails_list(jwt_token):
            if not isinstance(item, dict):
                continue
            eid = item.get("id")
            if eid in seen_ids:
                continue
            seen_ids.add(eid)
            code = _extract_otp(str(item.get("raw") or ""))
            if code:
                logger.info("收到验证码: %s", code)
                return code
        time.sleep(3)
    logger.warning("等待验证码超时 (%ds)", timeout)
    return None


# ============================================================
# 随机用户信息
# ============================================================

def generate_random_name() -> Tuple[str, str]:
    first = ["James", "Robert", "John", "Michael", "David", "Mary", "Jennifer", "Linda", "Emma", "Olivia"]
    last = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller"]
    return random.choice(first), random.choice(last)


def generate_random_birthday() -> str:
    return f"{random.randint(1992, 2003):04d}-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}"


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
# 注册器（五步 HTTP 流程）
# ============================================================

class Registrar:
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

    def step0_init_oauth(self, email: str) -> bool:
        """初始化 OAuth 会话"""
        self.session.cookies.set("oai-did", self.device_id, domain=".auth.openai.com")
        self.session.cookies.set("oai-did", self.device_id, domain="auth.openai.com")
        code_verifier, code_challenge = generate_pkce()
        self.code_verifier = code_verifier
        self.state = secrets.token_urlsafe(32)
        params = {
            "response_type": "code",
            "client_id": OAUTH_CLIENT_ID,
            "redirect_uri": OAUTH_REDIRECT_URI,
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
            logger.warning("step0a 失败: %s", e)
            return False
        if resp.status_code not in (200, 302):
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
                headers=h, verify=False, timeout=30,
            )
            return r2.status_code == 200
        except Exception as e:
            logger.warning("step0b 异常: %s", e)
            return False

    def step2_register_user(self, email: str, password: str) -> bool:
        h = self._headers(f"{OPENAI_AUTH_BASE}/create-account/password", with_sentinel=True)
        try:
            resp = self.session.post(
                f"{OPENAI_AUTH_BASE}/api/accounts/user/register",
                json={"username": email, "password": password},
                headers=h, verify=False, timeout=30,
            )
            if resp.status_code == 200:
                return True
            if resp.status_code in (301, 302):
                loc = resp.headers.get("Location", "")
                return "email-otp" in loc or "email-verification" in loc
            logger.warning("step2 失败: %s | %s", resp.status_code, resp.text[:200])
            return False
        except Exception as e:
            logger.warning("step2 异常: %s", e)
            return False

    def step3_send_otp(self) -> bool:
        try:
            h = dict(NAVIGATE_HEADERS)
            h["referer"] = f"{OPENAI_AUTH_BASE}/create-account/password"
            self.session.get(f"{OPENAI_AUTH_BASE}/api/accounts/email-otp/send",
                             headers=h, verify=False, timeout=30, allow_redirects=True)
            self.session.get(f"{OPENAI_AUTH_BASE}/email-verification",
                             headers=h, verify=False, timeout=30, allow_redirects=True)
            return True
        except Exception as e:
            logger.warning("step3 异常: %s", e)
            return False

    def step4_validate_otp(self, code: str) -> bool:
        h = self._headers(f"{OPENAI_AUTH_BASE}/email-verification")
        try:
            r = self.session.post(
                f"{OPENAI_AUTH_BASE}/api/accounts/email-otp/validate",
                json={"code": code}, headers=h, verify=False, timeout=30,
            )
            return r.status_code == 200
        except Exception as e:
            logger.warning("step4 异常: %s", e)
            return False

    def step5_create_account(self, first_name: str, last_name: str, birthdate: str) -> bool:
        h = self._headers(f"{OPENAI_AUTH_BASE}/about-you")
        body = {"name": f"{first_name} {last_name}", "birthdate": birthdate}
        try:
            r = self.session.post(
                f"{OPENAI_AUTH_BASE}/api/accounts/create_account",
                json=body, headers=h, verify=False, timeout=30,
            )
            if r.status_code == 200:
                return True
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

    def register(self, email: str, jwt_token: str, password: str) -> bool:
        """执行完整五步注册，成功返回 True"""
        first_name, last_name = generate_random_name()
        birthdate = generate_random_birthday()

        logger.info("[注册] step0 初始化 OAuth")
        if not self.step0_init_oauth(email):
            logger.warning("[注册] step0 失败")
            return False
        time.sleep(1)

        logger.info("[注册] step2 提交注册表单")
        if not self.step2_register_user(email, password):
            logger.warning("[注册] step2 失败")
            return False
        time.sleep(1)

        logger.info("[注册] step3 发送 OTP")
        if not self.step3_send_otp():
            logger.warning("[注册] step3 失败")
            return False

        logger.info("[注册] 等待验证码...")
        code = wait_for_otp(jwt_token, timeout=120)
        if not code:
            logger.warning("[注册] 未收到验证码")
            return False

        logger.info("[注册] step4 验证 OTP: %s", code)
        if not self.step4_validate_otp(code):
            logger.warning("[注册] step4 失败")
            return False
        time.sleep(1)

        logger.info("[注册] step5 创建账号")
        ok = self.step5_create_account(first_name, last_name, birthdate)
        if not ok:
            logger.warning("[注册] step5 失败")
        return ok


# ============================================================
# OAuth 登录获取 access_token
# ============================================================

def _extract_code_from_url(url: str) -> Optional[str]:
    if not url or "code=" not in url:
        return None
    try:
        return parse_qs(urlparse(url).query).get("code", [None])[0]
    except Exception:
        return None


def _follow_redirects_for_code(
    session: requests.Session, url: str, max_depth: int = 10
) -> Optional[str]:
    """跟随重定向提取 OAuth code"""
    if max_depth <= 0:
        return None
    try:
        r = session.get(url, headers=NAVIGATE_HEADERS, verify=False, timeout=15, allow_redirects=False)
        if r.status_code in (301, 302, 303, 307, 308):
            loc = r.headers.get("Location", "")
            code = _extract_code_from_url(loc)
            if code:
                return code
            if loc.startswith("/"):
                loc = f"{OPENAI_AUTH_BASE}{loc}"
            return _follow_redirects_for_code(session, loc, max_depth - 1)
        if r.status_code == 200:
            return _extract_code_from_url(str(r.url))
    except requests.exceptions.ConnectionError as e:
        m = re.search(r"(https?://localhost[^\s'\"]+)", str(e))
        if m:
            return _extract_code_from_url(m.group(1))
    except Exception:
        pass
    return None


def oauth_login(email: str, password: str, jwt_token: str, proxy: str = "") -> Optional[str]:
    """
    HTTP OAuth 登录，返回 access_token 字符串，失败返回 None。
    流程：A.获取login_session → B.提交邮箱 → C.提交密码 → D.OTP(可选) → E.获取code → F.换token
    """
    session = create_session(proxy=proxy)
    device_id = str(uuid.uuid4())
    session.cookies.set("oai-did", device_id, domain=".auth.openai.com")
    session.cookies.set("oai-did", device_id, domain="auth.openai.com")

    code_verifier, code_challenge = generate_pkce()
    state = secrets.token_urlsafe(32)

    # Step A: 获取 login_session
    logger.info("[登录] Step A: authorize")
    auth_params = {
        "response_type": "code",
        "client_id": OAUTH_CLIENT_ID,
        "redirect_uri": OAUTH_REDIRECT_URI,
        "scope": "openid profile email offline_access",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    try:
        session.get(
            f"{OPENAI_AUTH_BASE}/oauth/authorize?{urlencode(auth_params)}",
            headers=NAVIGATE_HEADERS, allow_redirects=True, verify=False, timeout=30,
        )
    except Exception as e:
        logger.warning("[登录] Step A 失败: %s", e)
        return None

    # Step B: 提交邮箱
    logger.info("[登录] Step B: 提交邮箱")
    h = dict(COMMON_HEADERS)
    h["referer"] = f"{OPENAI_AUTH_BASE}/log-in"
    h["oai-device-id"] = device_id
    h.update(generate_datadog_trace())
    sentinel = build_sentinel_token(session, device_id, flow="authorize_continue")
    if sentinel:
        h["openai-sentinel-token"] = sentinel
    try:
        resp = session.post(
            f"{OPENAI_AUTH_BASE}/api/accounts/authorize/continue",
            json={"username": {"kind": "email", "value": email}},
            headers=h, verify=False, timeout=30,
        )
    except Exception as e:
        logger.warning("[登录] Step B 异常: %s", e)
        return None
    if resp.status_code != 200:
        logger.warning("[登录] Step B 失败: HTTP %s", resp.status_code)
        return None

    try:
        b_data = resp.json()
        continue_url = str(b_data.get("continue_url") or "")
        page_type = str(((b_data.get("page") or {}).get("type")) or "")
    except Exception:
        continue_url, page_type = "", ""

    # Step C: 提交密码
    logger.info("[登录] Step C: 提交密码")
    h["referer"] = f"{OPENAI_AUTH_BASE}/log-in/password"
    h.update(generate_datadog_trace())
    sentinel2 = build_sentinel_token(session, device_id, flow="password_verify")
    if sentinel2:
        h["openai-sentinel-token"] = sentinel2
    try:
        resp = session.post(
            f"{OPENAI_AUTH_BASE}/api/accounts/password/verify",
            json={"password": password},
            headers=h, verify=False, timeout=30, allow_redirects=False,
        )
    except Exception as e:
        logger.warning("[登录] Step C 异常: %s", e)
        return None
    if resp.status_code != 200:
        logger.warning("[登录] Step C 失败: HTTP %s", resp.status_code)
        return None
    try:
        c_data = resp.json()
        continue_url = str(c_data.get("continue_url") or continue_url)
        page_type = str(((c_data.get("page") or {}).get("type")) or page_type)
    except Exception:
        pass

    # Step D（可选）：OTP 验证
    if page_type == "email_otp_verification" or "email-verification" in continue_url:
        logger.info("[登录] Step D: 需要 OTP 验证")
        if not jwt_token:
            logger.warning("[登录] 无 jwt_token，无法获取 OTP")
            return None
        h_v = dict(COMMON_HEADERS)
        h_v["referer"] = f"{OPENAI_AUTH_BASE}/email-verification"
        h_v["oai-device-id"] = device_id
        h_v.update(generate_datadog_trace())
        # 触发 OTP 发送
        sentinel_otp = build_sentinel_token(session, device_id, flow="email_otp")
        if sentinel_otp:
            h_v["openai-sentinel-token"] = sentinel_otp
        try:
            session.post(f"{OPENAI_AUTH_BASE}/api/accounts/email-otp/init",
                         json={}, headers=h_v, verify=False, timeout=30)
        except Exception:
            pass
        # 等待并提交验证码
        tried: set = set()
        start = time.time()
        got = False
        while time.time() - start < 120:
            for item in fetch_emails_list(jwt_token):
                if not isinstance(item, dict):
                    continue
                c = _extract_otp(str(item.get("raw") or ""))
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
                        got = True
                        logger.info("[登录] OTP 验证成功: %s", c)
                        break
            if got:
                break
            time.sleep(2)
        if not got:
            logger.warning("[登录] OTP 超时")
            return None

        # OTP 后处理：about-you 页面（新账号首次登录需要填写资料）
        if "about-you" in continue_url:
            full_about = continue_url if continue_url.startswith("http") else f"{OPENAI_AUTH_BASE}{continue_url}"
            try:
                resp_about = session.get(full_about, headers=NAVIGATE_HEADERS, verify=False, timeout=30, allow_redirects=True)
                if "consent" in str(resp_about.url) or "organization" in str(resp_about.url):
                    continue_url = str(resp_about.url)
                else:
                    fn, ln = generate_random_name()
                    bd = generate_random_birthday()
                    h_create = dict(COMMON_HEADERS)
                    h_create["referer"] = full_about
                    h_create["oai-device-id"] = device_id
                    h_create.update(generate_datadog_trace())
                    r_create = session.post(
                        f"{OPENAI_AUTH_BASE}/api/accounts/create_account",
                        json={"name": f"{fn} {ln}", "birthdate": bd},
                        headers=h_create, verify=False, timeout=30,
                    )
                    if r_create.status_code == 200:
                        try:
                            continue_url = str(r_create.json().get("continue_url") or continue_url)
                        except Exception:
                            pass
                    elif r_create.status_code == 400 and "already_exists" in r_create.text:
                        continue_url = f"{OPENAI_AUTH_BASE}/sign-in-with-chatgpt/codex/consent"
            except Exception as e:
                logger.warning("[登录] about-you 处理异常: %s", e)

        # consent 页面类型
        if page_type == "consent":
            continue_url = f"{OPENAI_AUTH_BASE}/sign-in-with-chatgpt/codex/consent"

    # Step E: 跟随 consent 重定向获取 auth code
    if not continue_url:
        logger.warning("[登录] 无 continue_url")
        return None
    consent_url = continue_url if continue_url.startswith("http") else f"{OPENAI_AUTH_BASE}{continue_url}"
    logger.info("[登录] Step E: 获取 auth code | consent_url=%s", consent_url[:80])

    # 解析 oai-client-auth-session cookie（用于 workspace/select 备用流程）
    def _decode_auth_session(sess: requests.Session) -> Optional[Dict[str, Any]]:
        for ck in sess.cookies:
            if ck.name == "oai-client-auth-session":
                part = ck.value.split(".")[0] if "." in ck.value else ck.value
                pad = 4 - len(part) % 4
                try:
                    raw = base64.urlsafe_b64decode(part + ("=" * (pad if pad != 4 else 0)))
                    d = json.loads(raw)
                    return d if isinstance(d, dict) else None
                except Exception:
                    pass
        return None

    auth_code: Optional[str] = None
    try:
        resp_c = session.get(
            consent_url, headers=NAVIGATE_HEADERS, verify=False, timeout=30, allow_redirects=False,
        )
        logger.info("[登录] consent GET: HTTP %s | Location=%s", resp_c.status_code, resp_c.headers.get("Location", "")[:100])
        if resp_c.status_code in (301, 302, 303, 307, 308):
            loc = resp_c.headers.get("Location", "")
            auth_code = _extract_code_from_url(loc)
            if not auth_code:
                auth_code = _follow_redirects_for_code(session, loc if loc.startswith("http") else f"{OPENAI_AUTH_BASE}{loc}")
        elif resp_c.status_code == 200:
            html = resp_c.text
            consent_h = {
                "accept": "application/json, text/plain, */*",
                "content-type": "application/json",
                "origin": OPENAI_AUTH_BASE,
                "referer": consent_url,
                "user-agent": USER_AGENT,
                "oai-device-id": device_id,
            }
            # 提取 state/nonce
            consent_payload: Dict[str, str] = {"action": "allow"}
            state_m = re.search(r'["\']state["\']:\s*["\']([^"\'\\s]+)["\']', html)
            nonce_m = re.search(r'["\']nonce["\']:\s*["\']([^"\'\\s]+)["\']', html)
            if state_m:
                consent_payload["state"] = state_m.group(1)
            if nonce_m:
                consent_payload["nonce"] = nonce_m.group(1)
            logger.info("[登录] consent POST to consent_url | payload=%s", consent_payload)
            try:
                r_post = session.post(
                    consent_url, json=consent_payload, headers=consent_h,
                    verify=False, timeout=30, allow_redirects=False,
                )
                logger.info("[登录] consent POST: HTTP %s | Location=%s | body=%s",
                            r_post.status_code,
                            r_post.headers.get("Location", "")[:100],
                            r_post.text[:300])
                if r_post.status_code in (301, 302, 303, 307, 308):
                    loc2 = r_post.headers.get("Location", "")
                    auth_code = _extract_code_from_url(loc2)
                    if not auth_code:
                        next_url = loc2 if loc2.startswith("http") else f"{OPENAI_AUTH_BASE}{loc2}"
                        auth_code = _follow_redirects_for_code(session, next_url)
                        if not auth_code:
                            consent_url = next_url
                elif r_post.status_code == 200:
                    try:
                        rd = r_post.json()
                        redir = str(rd.get("redirectTo") or rd.get("redirect_url") or rd.get("continue_url") or "")
                        logger.info("[登录] consent POST 200 redir=%s", redir[:100])
                        if redir:
                            auth_code = _extract_code_from_url(redir) or _follow_redirects_for_code(
                                session, redir if redir.startswith("http") else f"{OPENAI_AUTH_BASE}{redir}"
                            )
                    except Exception:
                        pass
            except requests.exceptions.ConnectionError as e:
                m = re.search(r"(https?://localhost[^\s'\"&]+)", str(e))
                if m:
                    auth_code = _extract_code_from_url(m.group(1))
        else:
            auth_code = _extract_code_from_url(str(resp_c.url))
            if not auth_code:
                auth_code = _follow_redirects_for_code(session, str(resp_c.url))
    except requests.exceptions.ConnectionError as e:
        m = re.search(r"(https?://localhost[^\s'\"]+)", str(e))
        if m:
            auth_code = _extract_code_from_url(m.group(1))
    except Exception:
        pass

    # workspace/select 备用流程（含 organization/select）
    if not auth_code:
        sess_data = _decode_auth_session(session)
        ws_id = None
        if sess_data:
            ws_list = sess_data.get("workspaces", [])
            if isinstance(ws_list, list) and ws_list:
                ws_id = (ws_list[0] or {}).get("id")
        if ws_id:
            h_ws: Dict[str, str] = dict(COMMON_HEADERS)
            h_ws["referer"] = consent_url
            h_ws["oai-device-id"] = device_id
            h_ws.update(generate_datadog_trace())
            try:
                r_ws = session.post(
                    f"{OPENAI_AUTH_BASE}/api/accounts/workspace/select",
                    json={"workspace_id": ws_id},
                    headers=h_ws, verify=False, timeout=30, allow_redirects=False,
                )
                logger.info("[登录] workspace/select: HTTP %s", r_ws.status_code)
                if r_ws.status_code in (301, 302, 303, 307, 308):
                    loc = r_ws.headers.get("Location", "")
                    auth_code = _extract_code_from_url(loc)
                    if not auth_code:
                        auth_code = _follow_redirects_for_code(
                            session, loc if loc.startswith("http") else f"{OPENAI_AUTH_BASE}{loc}"
                        )
                elif r_ws.status_code == 200:
                    ws_data = r_ws.json() or {}
                    ws_next = str(ws_data.get("continue_url") or "")
                    ws_page = str(((ws_data.get("page") or {}).get("type")) or "")
                    logger.info("[登录] workspace/select 200: ws_next=%s ws_page=%s", ws_next[:80], ws_page)
                    if "organization" in ws_next or "organization" in ws_page:
                        org_url = ws_next if ws_next.startswith("http") else f"{OPENAI_AUTH_BASE}{ws_next}"
                        org_id = None
                        project_id = None
                        ws_orgs = (ws_data.get("data") or {}).get("orgs", [])
                        if ws_orgs:
                            org_id = (ws_orgs[0] or {}).get("id")
                            projects = (ws_orgs[0] or {}).get("projects", [])
                            if projects:
                                project_id = (projects[0] or {}).get("id")
                        if org_id:
                            body_org: Dict[str, str] = {"org_id": org_id}
                            if project_id:
                                body_org["project_id"] = project_id
                            h_org: Dict[str, str] = dict(COMMON_HEADERS)
                            h_org["referer"] = org_url
                            h_org["oai-device-id"] = device_id
                            h_org.update(generate_datadog_trace())
                            resp_org = session.post(
                                f"{OPENAI_AUTH_BASE}/api/accounts/organization/select",
                                json=body_org, headers=h_org, verify=False,
                                timeout=30, allow_redirects=False,
                            )
                            logger.info("[登录] organization/select: HTTP %s", resp_org.status_code)
                            if resp_org.status_code in (301, 302, 303, 307, 308):
                                loc = resp_org.headers.get("Location", "")
                                auth_code = _extract_code_from_url(loc)
                                if not auth_code:
                                    auth_code = _follow_redirects_for_code(
                                        session, loc if loc.startswith("http") else f"{OPENAI_AUTH_BASE}{loc}"
                                    )
                            elif resp_org.status_code == 200:
                                org_next = str((resp_org.json() or {}).get("continue_url") or "")
                                if org_next:
                                    full_next = org_next if org_next.startswith("http") else f"{OPENAI_AUTH_BASE}{org_next}"
                                    auth_code = _follow_redirects_for_code(session, full_next)
                        else:
                            auth_code = _follow_redirects_for_code(session, org_url)
                    elif ws_next:
                        full = ws_next if ws_next.startswith("http") else f"{OPENAI_AUTH_BASE}{ws_next}"
                        auth_code = _follow_redirects_for_code(session, full)
            except Exception as e:
                logger.info("[登录] workspace/select 异常: %s", e)

    # fallback: 带重定向跟一次
    if not auth_code:
        try:
            r_fb = session.get(consent_url, headers=NAVIGATE_HEADERS, verify=False, timeout=30, allow_redirects=True)
            auth_code = _extract_code_from_url(str(r_fb.url))
            if not auth_code:
                for hist in r_fb.history:
                    auth_code = _extract_code_from_url(hist.headers.get("Location", ""))
                    if auth_code:
                        break
        except requests.exceptions.ConnectionError as e:
            m = re.search(r"(https?://localhost[^\s'\"]+)", str(e))
            if m:
                auth_code = _extract_code_from_url(m.group(1))
        except Exception:
            pass

    if not auth_code:
        logger.warning("[登录] 未能获取 auth_code")
        return None

    # Step F: code 换 token
    logger.info("[登录] Step F: code 换 token")
    try:
        resp_tok = create_session(proxy=proxy).post(
            f"{OPENAI_AUTH_BASE}/oauth/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": OAUTH_REDIRECT_URI,
                "client_id": OAUTH_CLIENT_ID,
                "code_verifier": code_verifier,
            },
            verify=False, timeout=60,
        )
        if resp_tok.status_code == 200:
            data = resp_tok.json()
            access_token = str(data.get("access_token") or "")
            if access_token:
                logger.info("[登录] access_token 获取成功")
                return access_token
        logger.warning("[登录] token 交换失败: HTTP %s", resp_tok.status_code)
    except Exception as e:
        logger.warning("[登录] token 交换异常: %s", e)
    return None


# ============================================================
# 结果保存
# ============================================================

_save_lock = __import__("threading").Lock()


def save_result(email: str, email_jwt: str, password: str, access_token: str) -> None:
    """追加保存一行到 results.txt"""
    with _save_lock:
        with open(RESULTS_FILE, "a", encoding="utf-8") as f:
            f.write(f"{email}|{email_jwt}|{password}|{access_token}\n")
    logger.info("已保存: %s → %s", email, RESULTS_FILE)


# ============================================================
# 单账号完整流程
# ============================================================

def process_one(proxy: str = "") -> bool:
    """
    完整流程：
    1. 创建临时邮箱
    2. 注册账号（五步 HTTP）
    3. OAuth 登录获取 access_token
    4. 保存结果
    """
    # 1. 创建临时邮箱
    email, email_jwt = create_temp_email()
    if not email:
        logger.error("创建临时邮箱失败，跳过")
        return False

    password = generate_random_password()
    logger.info("=" * 50)
    logger.info("邮箱: %s", email)

    # 2. 注册
    reg = Registrar(proxy=proxy)
    if not reg.register(email=email, jwt_token=email_jwt, password=password):
        logger.error("注册失败: %s", email)
        return False
    logger.info("注册成功: %s", email)
    time.sleep(3)

    # 3. 登录获取 access_token（最多重试 3 次）
    access_token: Optional[str] = None
    for attempt in range(1, 4):
        access_token = oauth_login(
            email=email, password=password, jwt_token=email_jwt, proxy=proxy
        )
        if access_token:
            break
        if attempt < 3:
            logger.warning("登录第 %d 次失败，15s 后重试...", attempt)
            time.sleep(15)

    if not access_token:
        logger.warning("获取 access_token 失败（注册已成功）: %s", email)
        # 仍保存邮箱和 jwt，access_token 留空
        save_result(email, email_jwt or "", password, "")
        return False

    # 4. 保存
    save_result(email, email_jwt or "", password, access_token)
    logger.info("完成: %s", email)
    return True


# ============================================================
# 批量入口
# ============================================================

def run() -> None:
    logger.info("=" * 50)
    logger.info("开始批量处理，目标数量: %d", TOTAL_ACCOUNTS)
    logger.info("结果将保存到: %s", RESULTS_FILE)
    logger.info("=" * 50)

    success = 0
    fail = 0
    for i in range(TOTAL_ACCOUNTS):
        logger.info("\n[%d/%d] 开始处理", i + 1, TOTAL_ACCOUNTS)
        ok = process_one()
        if ok:
            success += 1
        else:
            fail += 1
        logger.info("进度: %d/%d | 成功: %d | 失败: %d", i + 1, TOTAL_ACCOUNTS, success, fail)
        if i < TOTAL_ACCOUNTS - 1:
            wait = random.randint(5, 15)
            logger.info("等待 %ds...", wait)
            time.sleep(wait)

    logger.info("=" * 50)
    logger.info("完成 | 总计: %d | 成功: %d | 失败: %d", TOTAL_ACCOUNTS, success, fail)
    logger.info("=" * 50)


if __name__ == "__main__":
    run()
