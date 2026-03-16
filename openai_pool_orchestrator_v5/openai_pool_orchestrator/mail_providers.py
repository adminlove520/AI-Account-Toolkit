"""
MailProvider 抽象层
支持 Mail.tm / MoeMail / DuckMail / 自定义兼容 API
"""

from __future__ import annotations

import itertools
import logging
import random
import re
import secrets
import string
import time
import threading
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple, Callable

import requests as _requests
from requests.adapters import HTTPAdapter
import urllib3
from urllib3.exceptions import InsecureRequestWarning
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)
urllib3.disable_warnings(InsecureRequestWarning)


def _normalize_proxy_url(proxy: str) -> str:
    value = str(proxy or "").strip()
    if not value:
        return ""
    if "://" in value:
        return value
    if ":" in value:
        return f"http://{value}"
    return ""


class _ProxyAwareSession(_requests.Session):
    def __init__(
        self,
        proxy: str = "",
        proxy_selector: Optional[Callable[[], str]] = None,
    ):
        super().__init__()
        self._default_proxy = _normalize_proxy_url(proxy)
        self._proxy_selector = proxy_selector

    def request(self, method, url, **kwargs):
        selected_proxy = ""
        if self._proxy_selector:
            try:
                selected_proxy = _normalize_proxy_url(self._proxy_selector() or "")
            except Exception:
                selected_proxy = ""
        if not selected_proxy:
            selected_proxy = self._default_proxy
        base_kwargs = dict(kwargs)
        if selected_proxy and "proxies" not in base_kwargs:
            base_kwargs["proxies"] = {"http": selected_proxy, "https": selected_proxy}
        try:
            return super().request(method, url, **base_kwargs)
        except Exception:
            # 动态代理失败时，自动回退固定代理（若有）
            if (
                selected_proxy
                and self._default_proxy
                and selected_proxy != self._default_proxy
                and "proxies" not in kwargs
            ):
                fallback_kwargs = dict(kwargs)
                fallback_kwargs["proxies"] = {"http": self._default_proxy, "https": self._default_proxy}
                return super().request(method, url, **fallback_kwargs)
            raise


def _build_session(proxy: str = "", proxy_selector: Optional[Callable[[], str]] = None) -> _requests.Session:
    s = _ProxyAwareSession(proxy, proxy_selector)
    retry_total = 0 if proxy_selector else 2
    retry = Retry(
        total=retry_total,
        connect=retry_total,
        read=retry_total,
        status=retry_total,
        backoff_factor=0.2,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    fixed_proxy = _normalize_proxy_url(proxy)
    if fixed_proxy and not proxy_selector:
        s.proxies = {"http": fixed_proxy, "https": fixed_proxy}
    return s


def _extract_code(content: str) -> Optional[str]:
    if not content:
        return None
    m = re.search(r"background-color:\s*#F3F3F3[^>]*>[\s\S]*?(\d{6})[\s\S]*?</p>", content)
    if m:
        return m.group(1)
    for pat in [
        r"Verification code:?\s*(\d{6})",
        r"code is\s*(\d{6})",
        r"Subject:.*?(\d{6})",
        r">\s*(\d{6})\s*<",
        r"(?<![#&])\b(\d{6})\b",
    ]:
        for code in re.findall(pat, content, re.IGNORECASE):
            if code != "177010":
                return code
    return None


# ==================== 抽象基类 ====================

class MailProvider(ABC):
    @abstractmethod
    def create_mailbox(
        self,
        proxy: str = "",
        proxy_selector: Optional[Callable[[], str]] = None,
    ) -> Tuple[str, str]:
        """返回 (email, auth_credential)，auth_credential 是 bearer token 或 email_id"""

    @abstractmethod
    def wait_for_otp(
        self,
        auth_credential: str,
        email: str,
        proxy: str = "",
        proxy_selector: Optional[Callable[[], str]] = None,
        timeout: int = 120,
        stop_event: Optional[threading.Event] = None,
    ) -> str:
        """轮询获取6位验证码，超时返回空字符串"""

    def test_connection(self, proxy: str = "") -> Tuple[bool, str]:
        """测试 API 连通性，返回 (success, message)"""
        try:
            email, cred = self.create_mailbox(proxy)
            if email and cred:
                return True, f"成功创建测试邮箱: {email}"
            return False, "创建邮箱失败，请检查配置"
        except Exception as e:
            return False, f"连接失败: {e}"

    def close(self):
        pass


# ==================== Mail.tm ====================

class MailTmProvider(MailProvider):
    def __init__(self, api_base: str = "https://api.mail.tm"):
        self.api_base = api_base.rstrip("/")

    def _headers(self, token: str = "", use_json: bool = False) -> Dict[str, str]:
        h: Dict[str, str] = {"Accept": "application/json"}
        if use_json:
            h["Content-Type"] = "application/json"
        if token:
            h["Authorization"] = f"Bearer {token}"
        return h

    def _get_domains(self, session: _requests.Session) -> List[str]:
        resp = session.get(f"{self.api_base}/domains", headers=self._headers(), timeout=15, verify=False)
        if resp.status_code != 200:
            return []
        data = resp.json()
        items = data if isinstance(data, list) else (data.get("hydra:member") or data.get("items") or [])
        domains = []
        for item in items:
            if not isinstance(item, dict):
                continue
            domain = str(item.get("domain") or "").strip()
            if domain and item.get("isActive", True) and not item.get("isPrivate", False):
                domains.append(domain)
        return domains

    def create_mailbox(
        self,
        proxy: str = "",
        proxy_selector: Optional[Callable[[], str]] = None,
    ) -> Tuple[str, str]:
        session = _build_session(proxy, proxy_selector)
        domains = self._get_domains(session)
        if not domains:
            return "", ""
        domain = random.choice(domains)

        for _ in range(5):
            local = f"oc{secrets.token_hex(5)}"
            email = f"{local}@{domain}"
            password = secrets.token_urlsafe(18)

            resp = session.post(
                f"{self.api_base}/accounts",
                headers=self._headers(use_json=True),
                json={"address": email, "password": password},
                timeout=15, verify=False,
            )
            if resp.status_code not in (200, 201):
                continue

            token_resp = session.post(
                f"{self.api_base}/token",
                headers=self._headers(use_json=True),
                json={"address": email, "password": password},
                timeout=15, verify=False,
            )
            if token_resp.status_code == 200:
                token = str(token_resp.json().get("token") or "").strip()
                if token:
                    return email, token
        return "", ""

    def wait_for_otp(
        self,
        auth_credential: str,
        email: str,
        proxy: str = "",
        proxy_selector: Optional[Callable[[], str]] = None,
        timeout: int = 120,
        stop_event: Optional[threading.Event] = None,
    ) -> str:
        session = _build_session(proxy, proxy_selector)
        seen_ids: set = set()
        start = time.time()

        while time.time() - start < timeout:
            if stop_event and stop_event.is_set():
                return ""
            try:
                resp = session.get(
                    f"{self.api_base}/messages",
                    headers=self._headers(token=auth_credential),
                    timeout=15, verify=False,
                )
                if resp.status_code != 200:
                    time.sleep(3)
                    continue

                data = resp.json()
                messages = data if isinstance(data, list) else (
                    data.get("hydra:member") or data.get("messages") or []
                )

                for msg in messages:
                    if not isinstance(msg, dict):
                        continue
                    msg_id = str(msg.get("id") or msg.get("@id") or "").strip()
                    if not msg_id or msg_id in seen_ids:
                        continue
                    seen_ids.add(msg_id)

                    if msg_id.startswith("/messages/"):
                        msg_id = msg_id.split("/")[-1]

                    detail_resp = session.get(
                        f"{self.api_base}/messages/{msg_id}",
                        headers=self._headers(token=auth_credential),
                        timeout=15, verify=False,
                    )
                    if detail_resp.status_code != 200:
                        continue

                    mail_data = detail_resp.json()
                    sender = str(((mail_data.get("from") or {}).get("address") or "")).lower()
                    subject = str(mail_data.get("subject") or "")
                    intro = str(mail_data.get("intro") or "")
                    text = str(mail_data.get("text") or "")
                    html = mail_data.get("html") or ""
                    if isinstance(html, list):
                        html = "\n".join(str(x) for x in html)
                    content = "\n".join([subject, intro, text, str(html)])

                    if "openai" not in sender and "openai" not in content.lower():
                        continue

                    code = _extract_code(content)
                    if code:
                        return code
            except Exception:
                pass
            time.sleep(3)
        return ""


# ==================== MoeMail ====================

class MoeMailProvider(MailProvider):
    def __init__(self, api_base: str, api_key: str):
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key

    def _headers(self) -> Dict[str, str]:
        return {"X-API-Key": self.api_key}

    def _get_domain(self, session: _requests.Session) -> Optional[str]:
        try:
            resp = session.get(
                f"{self.api_base}/api/config",
                headers=self._headers(), timeout=10, verify=False,
            )
            if resp.status_code == 200:
                data = resp.json()
                domains_str = data.get("emailDomains", "")
                if domains_str:
                    domains = [d.strip() for d in domains_str.split(",") if d.strip()]
                    if domains:
                        return random.choice(domains)
        except Exception:
            pass
        return None

    def create_mailbox(
        self,
        proxy: str = "",
        proxy_selector: Optional[Callable[[], str]] = None,
    ) -> Tuple[str, str]:
        session = _build_session(proxy, proxy_selector)
        domain = self._get_domain(session)
        if not domain:
            return "", ""

        chars = string.ascii_lowercase + string.digits
        prefix = "".join(random.choice(chars) for _ in range(random.randint(8, 13)))

        try:
            resp = session.post(
                f"{self.api_base}/api/emails/generate",
                json={"name": prefix, "domain": domain, "expiryTime": 0},
                headers=self._headers(), timeout=15, verify=False,
            )
            if resp.status_code not in (200, 201):
                return "", ""
            data = resp.json()
            email_id = data.get("id")
            email = data.get("email")
            if email_id and email:
                return email, str(email_id)
        except Exception:
            pass
        return "", ""

    def wait_for_otp(
        self,
        auth_credential: str,
        email: str,
        proxy: str = "",
        proxy_selector: Optional[Callable[[], str]] = None,
        timeout: int = 120,
        stop_event: Optional[threading.Event] = None,
    ) -> str:
        session = _build_session(proxy, proxy_selector)
        email_id = auth_credential
        start = time.time()

        while time.time() - start < timeout:
            if stop_event and stop_event.is_set():
                return ""
            try:
                resp = session.get(
                    f"{self.api_base}/api/emails/{email_id}",
                    headers=self._headers(), timeout=15, verify=False,
                )
                if resp.status_code == 200:
                    messages = resp.json().get("messages") or []
                    for msg in messages:
                        if not isinstance(msg, dict):
                            continue
                        msg_id = msg.get("id")
                        if not msg_id:
                            continue
                        detail_resp = session.get(
                            f"{self.api_base}/api/emails/{email_id}/{msg_id}",
                            headers=self._headers(), timeout=15, verify=False,
                        )
                        if detail_resp.status_code == 200:
                            detail = detail_resp.json()
                            msg_obj = detail.get("message") or {}
                            content = msg_obj.get("content") or msg_obj.get("html") or ""
                            if not content:
                                content = detail.get("text") or detail.get("html") or ""
                            code = _extract_code(content)
                            if code:
                                return code
            except Exception:
                pass
            time.sleep(3)
        return ""


# ==================== DuckMail ====================

class DuckMailProvider(MailProvider):
    def __init__(self, api_base: str = "https://api.duckmail.sbs", bearer_token: str = ""):
        self.api_base = api_base.rstrip("/")
        self.bearer_token = bearer_token

    def _auth_headers(self, token: str = "") -> Dict[str, str]:
        h: Dict[str, str] = {"Accept": "application/json"}
        if token:
            h["Authorization"] = f"Bearer {token}"
        return h

    def create_mailbox(
        self,
        proxy: str = "",
        proxy_selector: Optional[Callable[[], str]] = None,
    ) -> Tuple[str, str]:
        session = _build_session(proxy, proxy_selector)
        headers: Dict[str, str] = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"

        try:
            domains_resp = session.get(f"{self.api_base}/domains", headers={"Accept": "application/json"}, timeout=15, verify=False)
            if domains_resp.status_code != 200:
                return "", ""
            data = domains_resp.json()
            items = data if isinstance(data, list) else (data.get("hydra:member") or [])
            domains = [str(i.get("domain") or "") for i in items if isinstance(i, dict) and i.get("domain") and i.get("isActive", True)]
            if not domains:
                return "", ""
            domain = random.choice(domains)

            local = f"oc{secrets.token_hex(5)}"
            email = f"{local}@{domain}"
            password = secrets.token_urlsafe(18)

            resp = session.post(
                f"{self.api_base}/accounts",
                json={"address": email, "password": password},
                headers=headers, timeout=30, verify=False,
            )
            if resp.status_code not in (200, 201):
                return "", ""

            time.sleep(0.5)
            token_resp = session.post(
                f"{self.api_base}/token",
                json={"address": email, "password": password},
                headers=headers, timeout=30, verify=False,
            )
            if token_resp.status_code == 200:
                mail_token = token_resp.json().get("token")
                if mail_token:
                    return email, str(mail_token)
        except Exception:
            pass
        return "", ""

    def wait_for_otp(
        self,
        auth_credential: str,
        email: str,
        proxy: str = "",
        proxy_selector: Optional[Callable[[], str]] = None,
        timeout: int = 120,
        stop_event: Optional[threading.Event] = None,
    ) -> str:
        session = _build_session(proxy, proxy_selector)
        seen_ids: set = set()
        start = time.time()

        while time.time() - start < timeout:
            if stop_event and stop_event.is_set():
                return ""
            try:
                resp = session.get(
                    f"{self.api_base}/messages",
                    headers=self._auth_headers(auth_credential),
                    timeout=30, verify=False,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    messages = data.get("hydra:member") or data.get("member") or data.get("data") or []
                    for msg in (messages if isinstance(messages, list) else []):
                        if not isinstance(msg, dict):
                            continue
                        msg_id = msg.get("id") or msg.get("@id")
                        if not msg_id or msg_id in seen_ids:
                            continue
                        seen_ids.add(msg_id)
                        raw_id = str(msg_id).split("/")[-1] if str(msg_id).startswith("/") else str(msg_id)

                        detail_resp = session.get(
                            f"{self.api_base}/messages/{raw_id}",
                            headers=self._auth_headers(auth_credential),
                            timeout=30, verify=False,
                        )
                        if detail_resp.status_code == 200:
                            detail = detail_resp.json()
                            content = detail.get("text") or detail.get("html") or ""
                            code = _extract_code(content)
                            if code:
                                return code
            except Exception:
                pass
            time.sleep(3)
        return ""


# ==================== 多提供商路由 ====================


class MultiMailRouter:
    """线程安全的多邮箱提供商路由器，支持轮询/随机/容错策略"""

    def __init__(self, config: Dict[str, Any]):
        providers_list: List[str] = config.get("mail_providers") or []
        provider_configs: Dict[str, Dict] = config.get("mail_provider_configs") or {}
        self.strategy: str = config.get("mail_strategy", "round_robin")

        if not providers_list:
            legacy = config.get("mail_provider", "mailtm")
            providers_list = [legacy]
            provider_configs = {legacy: config.get("mail_config") or {}}

        self._provider_names: List[str] = []
        self._providers: Dict[str, MailProvider] = {}
        self._failures: Dict[str, int] = {}
        self._lock = threading.RLock()
        self._counter = itertools.count()

        for name in providers_list:
            try:
                p = create_provider_by_name(name, provider_configs.get(name, {}))
                self._provider_names.append(name)
                self._providers[name] = p
                self._failures[name] = 0
            except Exception as e:
                logger.warning("创建邮箱提供商 %s 失败: %s", name, e)

        if not self._providers:
            fallback = create_provider_by_name("mailtm", {})
            self._provider_names = ["mailtm"]
            self._providers = {"mailtm": fallback}
            self._failures = {"mailtm": 0}

    def next_provider(self) -> Tuple[str, MailProvider]:
        with self._lock:
            names = self._provider_names
            if not names:
                raise RuntimeError("无可用邮箱提供商")

            if self.strategy == "random":
                name = random.choice(names)
            elif self.strategy == "failover":
                name = min(names, key=lambda n: self._failures.get(n, 0))
            else:
                idx = next(self._counter) % len(names)
                name = names[idx]
            return name, self._providers[name]

    def providers(self) -> List[Tuple[str, MailProvider]]:
        with self._lock:
            return [(n, self._providers[n]) for n in self._provider_names]

    def report_success(self, provider_name: str) -> None:
        with self._lock:
            self._failures[provider_name] = max(0, self._failures.get(provider_name, 0) - 1)

    def report_failure(self, provider_name: str) -> None:
        with self._lock:
            self._failures[provider_name] = self._failures.get(provider_name, 0) + 1


# ==================== 工厂函数 ====================


def create_provider_by_name(provider_type: str, mail_cfg: Dict[str, Any]) -> MailProvider:
    """根据提供商名称和单独配置创建实例"""
    provider_type = provider_type.lower().strip()
    api_base = str(mail_cfg.get("api_base", "")).strip()

    if provider_type == "moemail":
        return MoeMailProvider(
            api_base=api_base or "https://your-moemail-api.example.com",
            api_key=str(mail_cfg.get("api_key", "")).strip(),
        )
    elif provider_type == "duckmail":
        return DuckMailProvider(
            api_base=api_base or "https://api.duckmail.sbs",
            bearer_token=str(mail_cfg.get("bearer_token", "")).strip(),
        )
    else:
        return MailTmProvider(api_base=api_base or "https://api.mail.tm")


def create_provider(config: Dict[str, Any]) -> MailProvider:
    """兼容旧配置格式的工厂函数"""
    provider_type = str(config.get("mail_provider", "mailtm")).lower()
    mail_cfg = config.get("mail_config") or {}
    return create_provider_by_name(provider_type, mail_cfg)
