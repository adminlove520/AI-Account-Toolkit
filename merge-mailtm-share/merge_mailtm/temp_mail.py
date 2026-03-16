from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import secrets
import time
from dataclasses import dataclass
from email import policy
from email.parser import Parser
from email.utils import parseaddr
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlencode, urljoin, urlparse

import requests

from merge_mailtm.shared import safe_response_json


MAILTM_BASE = "https://api.mail.tm"
DUCKMAIL_BASE = "https://api.duckmail.sbs"
DEFAULT_EMAIL_PROVIDER = "mailtm"
SUPPORTED_EMAIL_PROVIDERS = {"mailtm", "duckmail", "cfmail"}
EMAIL_PROVIDER_ALIASES = {
    "mail.tm": "mailtm",
    "duck": "duckmail",
    "cf_mail": "cfmail",
    "cloudflare": "cfmail",
    "cloudflare_temp_email": "cfmail",
    "cloudflare-mail": "cfmail",
}
EMAIL_PROVIDER_LABELS = {
    "mailtm": "Mail.tm",
    "duckmail": "DuckMail",
    "cfmail": "CFMail",
}
_CFMAIL_API_BASE_CACHE: Dict[str, str] = {}
_CFMAIL_API_SUFFIXES = (
    "/open_api/settings",
    "/api/new_address",
    "/api/address_login",
    "/api/settings",
    "/api/mails",
    "/user_api/mails",
    "/admin/new_address",
    "/admin/mails",
    "/open_api",
    "/user_api",
    "/admin",
    "/api",
)


@dataclass(frozen=True)
class TempMailConfig:
    """临时邮箱服务配置，统一封装 provider、基础地址与可选 provider 密钥。"""

    provider: str
    base_url: str
    api_key: str = ""


@dataclass(frozen=True)
class TempMailAccount:
    """单个临时邮箱账号的关键信息，用于后续注册与本地落盘。"""

    email: str
    password: str
    token: str
    provider: str


def get_email_provider_label(provider: str) -> str:
    """将内部 provider 标识转成人类可读名称，便于日志定位。"""
    return EMAIL_PROVIDER_LABELS.get(normalize_email_provider(provider), "Mail.tm")


def normalize_email_provider(provider: Any) -> str:
    """标准化 provider 配置，并在非法取值时给出明确错误。"""
    raw = str(provider or DEFAULT_EMAIL_PROVIDER).strip().lower()
    raw = EMAIL_PROVIDER_ALIASES.get(raw, raw)
    if not raw:
        raw = DEFAULT_EMAIL_PROVIDER
    if raw not in SUPPORTED_EMAIL_PROVIDERS:
        supported = ", ".join(sorted(SUPPORTED_EMAIL_PROVIDERS))
        raise RuntimeError(f"email.provider 配置非法: {provider!r}，仅支持: {supported}")
    return raw


def default_email_base(provider: str) -> str:
    """根据 provider 返回默认 API 基础地址。"""
    normalized_provider = normalize_email_provider(provider)
    if normalized_provider == "duckmail":
        return DUCKMAIL_BASE
    if normalized_provider == "cfmail":
        return ""
    return MAILTM_BASE


def normalize_email_base(worker_domain: str, provider: str = DEFAULT_EMAIL_PROVIDER) -> str:
    """兼容旧的 worker_domain 配置，统一得到 provider 对应的基础地址。"""
    normalized_provider = normalize_email_provider(provider)
    raw = str(worker_domain or "").strip()
    if not raw:
        fallback = default_email_base(normalized_provider)
        if fallback:
            return fallback
        raise RuntimeError(f"email.worker_domain 未配置，provider={normalized_provider}")
    if raw.startswith("http://") or raw.startswith("https://"):
        normalized = raw.rstrip("/")
    else:
        normalized = f"https://{raw}".rstrip("/")
    if normalized_provider == "cfmail":
        return normalize_cfmail_base_url(normalized)
    return normalized


def normalize_cfmail_base_url(base_url: str) -> str:
    """将 CFMail 的站点地址规范化到站点根路径，避免误填 `/api` 等接口前缀。"""
    raw = str(base_url or "").strip().rstrip("/")
    if not raw:
        return raw
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        return raw
    path = parsed.path.rstrip("/")
    changed = True
    while changed and path:
        changed = False
        for suffix in _CFMAIL_API_SUFFIXES:
            if path == suffix or path.endswith(suffix):
                path = path[: -len(suffix)].rstrip("/")
                changed = True
                break
    rebuilt = f"{parsed.scheme}://{parsed.netloc}"
    if path:
        rebuilt += path
    return rebuilt.rstrip("/")


def normalize_mailtm_base(worker_domain: str, provider: str = DEFAULT_EMAIL_PROVIDER) -> str:
    """兼容旧函数名，内部仍转到通用的 provider 基础地址解析。"""
    return normalize_email_base(worker_domain, provider=provider)


def make_temp_mail_config(
    *,
    provider: Any = DEFAULT_EMAIL_PROVIDER,
    worker_domain: str = "",
    api_key: str = "",
) -> TempMailConfig:
    """构造统一的临时邮箱配置，便于 Mail.tm / DuckMail / CFMail 共用下游流程。"""
    normalized_provider = normalize_email_provider(provider)
    return TempMailConfig(
        provider=normalized_provider,
        base_url=normalize_email_base(worker_domain, provider=normalized_provider),
        api_key=str(api_key or "").strip(),
    )


def build_temp_mail_headers(
    *,
    provider: str,
    token: str = "",
    api_key: str = "",
    use_json: bool = False,
) -> Dict[str, str]:
    """构造临时邮箱 API 请求头。"""
    normalized_provider = normalize_email_provider(provider)
    headers = {
        "Accept": "application/json",
    }
    if use_json:
        headers["Content-Type"] = "application/json"
    auth_token = str(token or "").strip()
    provider_secret = str(api_key or "").strip()
    if normalized_provider == "cfmail":
        headers["x-lang"] = "zh"
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        if provider_secret:
            headers["x-custom-auth"] = provider_secret
        return headers
    if auth_token or provider_secret:
        headers["Authorization"] = f"Bearer {auth_token or provider_secret}"
    return headers


def _looks_like_cfmail_settings_payload(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    return any(key in payload for key in ("domains", "defaultDomains", "needAuth", "enableUserCreateEmail"))


def _extract_script_sources_from_html(base_url: str, html: str) -> List[str]:
    sources: List[str] = []
    for raw_src in re.findall(r"""<script[^>]+src=["']([^"']+)["']""", html or "", flags=re.IGNORECASE):
        src = str(raw_src or "").strip()
        if not src:
            continue
        if src.startswith("http://") or src.startswith("https://"):
            sources.append(src)
        else:
            sources.append(urljoin(base_url.rstrip("/") + "/", src.lstrip("/")))
    return sources


def _extract_backend_candidates_from_js(js_text: str) -> List[str]:
    candidates: List[str] = []
    for raw_url in re.findall(r"""https://[^"'`\s<>)]+""", js_text or ""):
        url = str(raw_url or "").rstrip("/")
        if not url or url in candidates:
            continue
        host = urlparse(url).netloc.lower()
        if not host:
            continue
        if any(
            host.endswith(suffix)
            for suffix in (
                "github.com",
                "vuejs.org",
                "naiveui.com",
                "fingerprint.com",
                "openfpcdn.io",
                "googlesyndication.com",
                "cloudflareinsights.com",
                "esm.sh",
                "msgpusher.com",
                "day.app",
                "ntfy.sh",
                "discord.gg",
                "t.me",
                "cloudflare.com",
            )
        ):
            continue
        candidates.append(url)
    return candidates


def resolve_cfmail_api_base(base_url: str, api_key: str = "", timeout: int = 15) -> str:
    """解析 CFMail 的真实后端 API 地址。

    支持两类部署：
    1. `worker_domain` 直接填写 Worker 后端地址；
    2. `worker_domain` 误填为 Pages 前端地址时，尝试从前端打包产物中的 `VITE_API_BASE` 反推真实 Worker 地址。
    """
    normalized_base = str(base_url or "").strip().rstrip("/")
    if not normalized_base:
        return normalized_base

    cache_key = f"{normalized_base}|{str(api_key or '').strip()}"
    cached = _CFMAIL_API_BASE_CACHE.get(cache_key)
    if cached:
        return cached

    headers = build_temp_mail_headers(provider="cfmail", api_key=api_key)
    settings_url = normalized_base + "/open_api/settings"
    html_text = ""
    try:
        resp = requests.get(settings_url, headers=headers, verify=False, timeout=timeout)
        payload = safe_response_json(resp)
        if resp.status_code == 200 and _looks_like_cfmail_settings_payload(payload):
            _CFMAIL_API_BASE_CACHE[cache_key] = normalized_base
            _CFMAIL_API_BASE_CACHE[normalized_base] = normalized_base
            return normalized_base
        if "text/html" in str(resp.headers.get("content-type") or "").lower():
            html_text = str(resp.text or "")
    except Exception:
        pass

    if not html_text:
        try:
            root_resp = requests.get(normalized_base + "/", verify=False, timeout=timeout)
            if "text/html" in str(root_resp.headers.get("content-type") or "").lower():
                html_text = str(root_resp.text or "")
        except Exception:
            return normalized_base

    if not html_text:
        return normalized_base

    script_sources = _extract_script_sources_from_html(normalized_base, html_text)
    for script_url in script_sources[:8]:
        try:
            js_resp = requests.get(script_url, verify=False, timeout=timeout)
            if js_resp.status_code != 200:
                continue
            candidates = _extract_backend_candidates_from_js(str(js_resp.text or ""))
        except Exception:
            continue

        for candidate in candidates[:12]:
            candidate_key = f"{candidate}|{str(api_key or '').strip()}"
            try:
                candidate_resp = requests.get(
                    candidate.rstrip("/") + "/open_api/settings",
                    headers=headers,
                    verify=False,
                    timeout=timeout,
                )
            except Exception:
                continue
            candidate_payload = safe_response_json(candidate_resp)
            if candidate_resp.status_code == 200 and _looks_like_cfmail_settings_payload(candidate_payload):
                resolved = candidate.rstrip("/")
                _CFMAIL_API_BASE_CACHE[cache_key] = resolved
                _CFMAIL_API_BASE_CACHE[candidate_key] = resolved
                _CFMAIL_API_BASE_CACHE[normalized_base] = resolved
                return resolved

    return normalized_base


def resolve_temp_mail_config(config: TempMailConfig, timeout: int = 15) -> TempMailConfig:
    """按 provider 对配置做运行时补全；当前主要用于 CFMail 前后端分离场景。"""
    if config.provider != "cfmail":
        return config
    resolved_base = resolve_cfmail_api_base(config.base_url, api_key=config.api_key, timeout=timeout)
    if resolved_base == config.base_url:
        return config
    return TempMailConfig(provider=config.provider, base_url=resolved_base, api_key=config.api_key)


def mailtm_headers(*, token: str = "", use_json: bool = False) -> Dict[str, str]:
    """兼容旧接口名，默认按 Mail.tm 的请求头规范生成。"""
    return build_temp_mail_headers(provider="mailtm", token=token, use_json=use_json)


def get_temp_mail_domain_path(provider: str) -> str:
    """返回当前 provider 的域名列表路径。"""
    normalized_provider = normalize_email_provider(provider)
    if normalized_provider == "cfmail":
        return "/open_api/settings"
    return "/domains"


def get_temp_mail_account_create_path(provider: str) -> str:
    """返回当前 provider 的邮箱创建路径。"""
    normalized_provider = normalize_email_provider(provider)
    if normalized_provider == "cfmail":
        return "/api/new_address"
    return "/accounts"


def get_temp_mail_token_path(provider: str) -> Optional[str]:
    """返回当前 provider 的登录取 token 路径。"""
    normalized_provider = normalize_email_provider(provider)
    if normalized_provider == "cfmail":
        return "/api/address_login"
    return "/token"


def get_temp_mail_messages_path(provider: str) -> str:
    """返回当前 provider 的收件箱列表路径。"""
    normalized_provider = normalize_email_provider(provider)
    if normalized_provider == "cfmail":
        return f"/api/mails?{urlencode({'limit': 100, 'offset': 0})}"
    return "/messages"


def get_temp_mail_message_detail_path(provider: str, msg_id: str) -> str:
    """返回当前 provider 的邮件详情路径。"""
    normalized_provider = normalize_email_provider(provider)
    if normalized_provider == "cfmail":
        return f"/api/mail/{quote(msg_id, safe='')}"
    return f"/messages/{quote(msg_id, safe='')}"


def _unwrap_payload(payload: Any) -> Any:
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        return payload.get("data") or payload
    return payload


def _parse_metadata(metadata: Any) -> Dict[str, Any]:
    if isinstance(metadata, dict):
        return metadata
    if isinstance(metadata, str):
        try:
            data = json.loads(metadata)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def _extract_intro(text: str, metadata: Any) -> str:
    parsed_metadata = _parse_metadata(metadata)
    ai_extract = parsed_metadata.get("ai_extract") if isinstance(parsed_metadata, dict) else None
    if isinstance(ai_extract, dict):
        result_text = str(ai_extract.get("result_text") or ai_extract.get("result") or "").strip()
        if result_text:
            return result_text
    content = str(text or "").strip()
    if not content:
        return ""
    first_line = next((line.strip() for line in content.splitlines() if line.strip()), "")
    if first_line:
        return first_line[:180]
    return content[:180]


def _decode_part_content(part: Any) -> str:
    try:
        content = part.get_content()
        if isinstance(content, bytes):
            charset = part.get_content_charset() or "utf-8"
            return content.decode(charset, errors="replace")
        return str(content or "")
    except Exception:
        payload = part.get_payload(decode=True)
        if isinstance(payload, bytes):
            charset = part.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
        return str(part.get_payload() or "")


def parse_raw_email_content(raw: Any) -> Dict[str, str]:
    """尽量从 RFC822 / EML 原文中解析主题、发件人与正文。"""
    text = str(raw or "")
    if not text.strip():
        return {
            "subject": "",
            "from_name": "",
            "from_address": "",
            "text": "",
            "html": "",
        }

    try:
        message = Parser(policy=policy.default).parsestr(text)
    except Exception:
        return {
            "subject": "",
            "from_name": "",
            "from_address": "",
            "text": text,
            "html": "",
        }

    name, address = parseaddr(str(message.get("From") or ""))
    text_parts: List[str] = []
    html_parts: List[str] = []

    try:
        if message.is_multipart():
            for part in message.walk():
                if part.is_multipart():
                    continue
                if str(part.get_content_disposition() or "").lower() == "attachment":
                    continue
                content = _decode_part_content(part)
                ctype = str(part.get_content_type() or "").lower()
                if ctype == "text/html":
                    html_parts.append(content)
                elif ctype == "text/plain":
                    text_parts.append(content)
        else:
            content = _decode_part_content(message)
            ctype = str(message.get_content_type() or "").lower()
            if ctype == "text/html":
                html_parts.append(content)
            else:
                text_parts.append(content)
    except Exception:
        text_parts.append(text)

    plain_text = "\n".join(part for part in text_parts if part).strip()
    html_text = "\n".join(part for part in html_parts if part).strip()
    return {
        "subject": str(message.get("Subject") or "").strip(),
        "from_name": str(name or "").strip(),
        "from_address": str(address or "").strip(),
        "text": plain_text,
        "html": html_text,
    }


def normalize_temp_mail_domains(payload: Any, provider: str = DEFAULT_EMAIL_PROVIDER) -> List[str]:
    """统一解析域名列表，过滤停用或私有域名后返回可选域名。"""
    normalized_provider = normalize_email_provider(provider)
    if normalized_provider == "cfmail":
        if not isinstance(payload, dict):
            return []
        payload_root = payload
        for key in ("data", "settings", "result"):
            nested = payload_root.get(key) if isinstance(payload_root, dict) else None
            if isinstance(nested, dict):
                payload_root = nested
                break
        raw_domains = payload_root.get("defaultDomains") or payload_root.get("domains") or []
        if not isinstance(raw_domains, list):
            return []
        return [str(item).strip() for item in raw_domains if str(item).strip()]

    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("hydra:member") or payload.get("items") or payload.get("domains") or payload.get("data") or []
    else:
        rows = []

    domains: List[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        domain = str(row.get("domain") or "").strip()
        is_active = bool(row.get("isActive", True))
        is_private = bool(row.get("isPrivate", False))
        if domain and is_active and not is_private:
            domains.append(domain)
    return domains


def extract_temp_mail_message_rows(payload: Any, provider: str = DEFAULT_EMAIL_PROVIDER) -> List[Any]:
    """从列表接口响应中抽取邮件列表原始行。"""
    normalized_provider = normalize_email_provider(provider)
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    if normalized_provider == "cfmail":
        rows = payload.get("results") or payload.get("data") or []
        return rows if isinstance(rows, list) else []
    rows = payload.get("hydra:member") or payload.get("messages") or payload.get("items") or payload.get("inbox") or payload.get("data") or []
    return rows if isinstance(rows, list) else []


def normalize_temp_mail_message(item: Any, provider: str = DEFAULT_EMAIL_PROVIDER) -> Dict[str, Any]:
    """统一消息列表/详情结构，让下游继续按 Mail.tm 风格读取字段。"""
    normalized_provider = normalize_email_provider(provider)
    if not isinstance(item, dict):
        return {}

    if normalized_provider == "cfmail":
        parsed = parse_raw_email_content(item.get("raw"))
        sender_address = str(item.get("source") or parsed.get("from_address") or "").strip()
        sender_name = str(parsed.get("from_name") or "").strip()
        subject = str(parsed.get("subject") or "").strip()
        text = str(parsed.get("text") or item.get("raw") or "")
        html = str(parsed.get("html") or "")
        intro = _extract_intro(text or html or subject, item.get("metadata"))
        created_at = item.get("created_at") or item.get("createdAt") or ""
        return {
            "id": str(item.get("id") or item.get("message_id") or "").strip(),
            "from": {
                "address": sender_address,
                "name": sender_name,
            },
            "subject": subject,
            "intro": intro,
            "text": text,
            "html": html,
            "createdAt": created_at,
        }

    sender = item.get("from") or item.get("sender")
    if not isinstance(sender, dict):
        sender = {}

    msg_id = str(item.get("id") or item.get("_id") or item.get("messageId") or item.get("message_id") or "").strip()
    subject = str(item.get("subject") or item.get("title") or "").strip()
    intro = str(item.get("intro") or item.get("snippet") or "").strip()
    text = item.get("text")
    if isinstance(text, list):
        text = "\n".join(str(part) for part in text)
    text = str(text or item.get("textContent") or item.get("body") or "")
    html = item.get("html")
    if html is None:
        html = item.get("htmlContent") or item.get("bodyHtml") or ""
    if isinstance(html, list):
        html = "\n".join(str(part) for part in html)
    html = str(html or "")
    raw = item.get("raw")
    if raw and not (subject or text or html):
        parsed = parse_raw_email_content(raw)
        subject = subject or str(parsed.get("subject") or "").strip()
        text = text or str(parsed.get("text") or "")
        html = html or str(parsed.get("html") or "")
        intro = intro or _extract_intro(text or html or subject, item.get("metadata"))
        if not sender.get("address"):
            sender["address"] = parsed.get("from_address")
        if not sender.get("name"):
            sender["name"] = parsed.get("from_name")
    created_at = item.get("createdAt") or item.get("created_at") or item.get("date") or ""

    return {
        "id": msg_id,
        "from": {
            "address": str(sender.get("address") or sender.get("email") or "").strip(),
            "name": str(sender.get("name") or "").strip(),
        },
        "subject": subject,
        "intro": intro,
        "text": text,
        "html": html,
        "createdAt": created_at,
    }


def extract_temp_mail_error(resp: Any) -> str:
    """尽量从第三方邮箱接口响应中提取可读错误，便于日志排障。"""
    payload = safe_response_json(resp)
    if isinstance(payload, dict):
        for key in ("message", "error", "detail", "description", "msg"):
            value = payload.get(key)
            if value:
                return str(value)
    text = str(getattr(resp, "text", "") or "").strip()
    return text[:300]


def build_temp_mail_account_create_payload(provider: str, email: str, password: str) -> Dict[str, Any]:
    """构造创建邮箱时的 JSON 请求体。"""
    normalized_provider = normalize_email_provider(provider)
    if normalized_provider == "cfmail":
        local_part, _, domain = str(email or "").partition("@")
        payload: Dict[str, Any] = {
            "cf_token": "",
        }
        if local_part:
            payload["name"] = local_part
        if domain:
            payload["domain"] = domain
        return payload
    return {"address": email, "password": password}


def build_temp_mail_token_payload(provider: str, email: str, password: str) -> Dict[str, Any]:
    """构造重新登录取 token 的 JSON 请求体。"""
    normalized_provider = normalize_email_provider(provider)
    if normalized_provider == "cfmail":
        return {
            "email": str(email or "").strip(),
            "password": hashlib.sha256(str(password or "").encode("utf-8")).hexdigest(),
            "cf_token": "",
        }
    return {"address": email, "password": password}


def extract_temp_mail_token(payload: Any) -> str:
    """从创建/登录响应中抽取 token 或 jwt。"""
    data = _unwrap_payload(payload)
    if not isinstance(data, dict):
        return ""
    return str(data.get("token") or data.get("authToken") or data.get("auth_token") or data.get("jwt") or "").strip()


def extract_temp_mail_account_email(payload: Any, fallback_email: str) -> str:
    """从创建响应中抽取最终邮箱地址。"""
    data = _unwrap_payload(payload)
    if not isinstance(data, dict):
        return str(fallback_email or "").strip()
    return str(data.get("address") or data.get("email") or fallback_email or "").strip()


def extract_temp_mail_account_password(payload: Any, fallback_password: str) -> str:
    """从创建响应中抽取地址密码。"""
    data = _unwrap_payload(payload)
    if not isinstance(data, dict):
        return str(fallback_password or "").strip()
    return str(data.get("password") or fallback_password or "").strip()


def temp_mail_request(
    session: requests.Session,
    method: str,
    config: TempMailConfig,
    path: str,
    *,
    token: str = "",
    json_body: Optional[Dict[str, Any]] = None,
    timeout: int = 20,
    retries: int = 2,
) -> Optional[requests.Response]:
    """统一的临时邮箱请求封装，兼容 provider 鉴权头和简单重试。"""
    url = urljoin(config.base_url.rstrip("/") + "/", path.lstrip("/"))
    last_resp: Optional[requests.Response] = None
    for attempt in range(retries + 1):
        try:
            resp = session.request(
                method.upper(),
                url,
                headers=build_temp_mail_headers(
                    provider=config.provider,
                    token=token,
                    api_key=config.api_key,
                    use_json=json_body is not None,
                ),
                json=json_body,
                timeout=timeout,
                verify=False,
            )
            last_resp = resp
            if resp.status_code in (408, 429) or resp.status_code >= 500:
                if attempt < retries:
                    time.sleep(1 + attempt)
                    continue
            return resp
        except Exception:
            if attempt >= retries:
                break
            time.sleep(1 + attempt)
    return last_resp


def create_temp_email(
    session: requests.Session,
    worker_domain: str,
    email_domains: List[str],
    admin_password: str,
    logger: logging.Logger,
    provider: str = DEFAULT_EMAIL_PROVIDER,
    api_key: str = "",
) -> tuple[Optional[str], Optional[str]]:
    """创建临时邮箱并返回邮箱地址与 token。"""
    effective_api_key = str(api_key or "").strip()
    if normalize_email_provider(provider) == "duckmail" and not effective_api_key:
        effective_api_key = str(admin_password or "").strip()
    mail_config = resolve_temp_mail_config(
        make_temp_mail_config(provider=provider, worker_domain=worker_domain, api_key=effective_api_key)
    )
    provider_label = get_email_provider_label(mail_config.provider)
    preferred_domains = [str(item).strip().lower() for item in email_domains if str(item).strip()]

    try:
        domains = get_mailtm_domains(session, mail_config.base_url, provider=mail_config.provider, api_key=mail_config.api_key)
        if not domains:
            logger.warning("%s 无可用域名: %s", provider_label, mail_config.base_url)
            return None, None

        if preferred_domains:
            filtered = [domain for domain in domains if domain.lower() in preferred_domains]
            if filtered:
                domains = filtered

        for _ in range(5):
            chosen_domain = random.choice(domains)
            requested_email = f"oc{secrets.token_hex(5)}@{chosen_domain}"
            requested_password = secrets.token_urlsafe(18)
            create_res = temp_mail_request(
                session,
                "post",
                mail_config,
                get_temp_mail_account_create_path(mail_config.provider),
                json_body=build_temp_mail_account_create_payload(mail_config.provider, requested_email, requested_password),
                timeout=15,
            )
            if create_res is None or create_res.status_code not in (200, 201):
                continue

            create_data = _unwrap_payload(safe_response_json(create_res))
            final_email = extract_temp_mail_account_email(create_data, requested_email)
            final_password = extract_temp_mail_account_password(create_data, requested_password)
            token = extract_temp_mail_token(create_data)
            if not token:
                token_path = get_temp_mail_token_path(mail_config.provider)
                if token_path and final_password:
                    token_res = temp_mail_request(
                        session,
                        "post",
                        mail_config,
                        token_path,
                        json_body=build_temp_mail_token_payload(mail_config.provider, final_email, final_password),
                        timeout=15,
                    )
                    if token_res is not None and token_res.status_code == 200:
                        token = extract_temp_mail_token(safe_response_json(token_res))
            if token:
                logger.info("创建 %s 邮箱成功: %s", provider_label, final_email)
                return final_email, token

        logger.warning("%s 创建邮箱成功但取 token 失败", provider_label)
    except Exception as exc:
        logger.warning("创建 %s 邮箱异常: %s", provider_label, exc)
    return None, None


def get_mailtm_domains(
    session: requests.Session,
    mailtm_base: str,
    *,
    provider: str = DEFAULT_EMAIL_PROVIDER,
    api_key: str = "",
) -> List[str]:
    """获取当前 provider 的可用域名，并统一解析响应结构。"""
    mail_config = resolve_temp_mail_config(
        make_temp_mail_config(provider=provider, worker_domain=mailtm_base, api_key=api_key)
    )
    resp = temp_mail_request(
        session,
        "get",
        mail_config,
        get_temp_mail_domain_path(mail_config.provider),
        timeout=15,
    )
    if resp is None or resp.status_code != 200:
        return []

    return normalize_temp_mail_domains(safe_response_json(resp), mail_config.provider)


def fetch_emails(
    session: requests.Session,
    worker_domain: str,
    cf_token: str,
    *,
    provider: str = DEFAULT_EMAIL_PROVIDER,
    api_key: str = "",
) -> List[Dict[str, Any]]:
    """拉取收件箱列表，并统一返回 Mail.tm 风格的消息对象。"""
    mail_config = resolve_temp_mail_config(
        make_temp_mail_config(provider=provider, worker_domain=worker_domain, api_key=api_key)
    )
    try:
        resp = temp_mail_request(
            session,
            "get",
            mail_config,
            get_temp_mail_messages_path(mail_config.provider),
            token=cf_token,
            timeout=30,
        )
        if resp is not None and resp.status_code == 200:
            rows = extract_temp_mail_message_rows(safe_response_json(resp), mail_config.provider)
            return [msg for msg in (normalize_temp_mail_message(row, mail_config.provider) for row in rows) if msg.get("id")]
    except Exception:
        pass
    return []


def fetch_email_detail(
    session: requests.Session,
    worker_domain: str,
    cf_token: str,
    msg_id: str,
    *,
    provider: str = DEFAULT_EMAIL_PROVIDER,
    api_key: str = "",
) -> Dict[str, Any]:
    """读取单封邮件详情，邮件不存在或字段缺失时返回空对象。"""
    if not msg_id:
        return {}
    mail_config = resolve_temp_mail_config(
        make_temp_mail_config(provider=provider, worker_domain=worker_domain, api_key=api_key)
    )
    try:
        resp = temp_mail_request(
            session,
            "get",
            mail_config,
            get_temp_mail_message_detail_path(mail_config.provider, msg_id),
            token=cf_token,
            timeout=30,
        )
        if resp is None or resp.status_code != 200:
            return {}
        raw_detail = _unwrap_payload(safe_response_json(resp))
        if raw_detail is None:
            return {}
        detail = normalize_temp_mail_message(raw_detail, mail_config.provider)
        if not detail.get("id"):
            detail["id"] = msg_id
        return detail
    except Exception:
        return {}


def extract_verification_code(content: str) -> Optional[str]:
    """从邮件正文中提取 6 位验证码，并跳过已知无效示例码。"""
    if not content:
        return None
    match = re.search(r"background-color:\s*#F3F3F3[^>]*>[\s\S]*?(\d{6})[\s\S]*?</p>", content)
    if match:
        return match.group(1)
    match = re.search(r"Subject:.*?(\d{6})", content)
    if match and match.group(1) != "177010":
        return match.group(1)
    for pattern in [r">\s*(\d{6})\s*<", r"(?<![#&])\b(\d{6})\b"]:
        for code in re.findall(pattern, content):
            if code != "177010":
                return code
    return None


def wait_for_verification_code(
    session: requests.Session,
    worker_domain: str,
    cf_token: str,
    timeout: int = 120,
    *,
    provider: str = DEFAULT_EMAIL_PROVIDER,
    api_key: str = "",
) -> Optional[str]:
    """轮询收件箱并读取新邮件详情，直到拿到 OpenAI 验证码或超时。"""
    old_ids = set()
    old = fetch_emails(session, worker_domain, cf_token, provider=provider, api_key=api_key)
    if old:
        old_ids = {
            str(item.get("id") or "").strip()
            for item in old
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        }

    start = time.time()
    while time.time() - start < timeout:
        emails = fetch_emails(session, worker_domain, cf_token, provider=provider, api_key=api_key)
        if emails:
            for item in emails:
                if not isinstance(item, dict):
                    continue
                msg_id = str(item.get("id") or "").strip()
                if not msg_id or msg_id in old_ids:
                    continue
                old_ids.add(msg_id)

                detail = fetch_email_detail(
                    session,
                    worker_domain,
                    cf_token,
                    msg_id,
                    provider=provider,
                    api_key=api_key,
                )
                sender = str(((detail.get("from") or {}).get("address") or "")).lower()
                subject = str(detail.get("subject") or "")
                intro = str(detail.get("intro") or "")
                text = str(detail.get("text") or "")
                html = detail.get("html") or ""
                if isinstance(html, list):
                    html = "\n".join(str(part) for part in html)
                content = "\n".join([subject, intro, text, str(html)])

                if "openai" not in sender and "openai" not in content.lower():
                    continue
                code = extract_verification_code(content)
                if code:
                    return code
        time.sleep(3)
    return None
