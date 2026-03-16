"""
账号池维护模块
支持 CPA 平台和 Sub2Api 平台的探测、清理、计数和补号
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests as _requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    import aiohttp
except ImportError:
    aiohttp = None

logger = logging.getLogger(__name__)

DEFAULT_MGMT_UA = "codex_cli_rs/0.76.0 (Debian 13.0.0; x86_64) WindowsTerminal"


def _mgmt_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _build_session(proxy: str = "") -> _requests.Session:
    s = _requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    return s


def _get_item_type(item: Dict[str, Any]) -> str:
    return str(item.get("type") or item.get("typo") or "")


def _safe_json(text: str) -> Dict[str, Any]:
    try:
        return json.loads(text)
    except Exception:
        return {}


def _extract_account_id(item: Dict[str, Any]) -> Optional[str]:
    for key in ("chatgpt_account_id", "chatgptAccountId", "account_id", "accountId"):
        val = item.get(key)
        if val:
            return str(val)
    return None


class PoolMaintainer:
    def __init__(
        self,
        cpa_base_url: str,
        cpa_token: str,
        target_type: str = "codex",
        min_candidates: int = 800,
        used_percent_threshold: int = 95,
        user_agent: str = DEFAULT_MGMT_UA,
    ):
        self.base_url = cpa_base_url.rstrip("/")
        self.token = cpa_token
        self.target_type = target_type
        self.min_candidates = min_candidates
        self.used_percent_threshold = used_percent_threshold
        self.user_agent = user_agent

    def fetch_auth_files(self, timeout: int = 15) -> List[Dict[str, Any]]:
        resp = _requests.get(
            f"{self.base_url}/v0/management/auth-files",
            headers=_mgmt_headers(self.token),
            timeout=timeout,
        )
        resp.raise_for_status()
        raw = resp.json()
        data = raw if isinstance(raw, dict) else {}
        files = data.get("files", [])
        return files if isinstance(files, list) else []

    def get_pool_status(self, timeout: int = 15) -> Dict[str, Any]:
        try:
            files = self.fetch_auth_files(timeout)
            candidates = [f for f in files if _get_item_type(f).lower() == self.target_type.lower()]
            total = len(files)
            cand_count = len(candidates)
            return {
                "total": total,
                "candidates": cand_count,
                "error_count": max(0, total - cand_count),
                "threshold": self.min_candidates,
                "healthy": cand_count >= self.min_candidates,
                "percent": round(cand_count / self.min_candidates * 100, 1) if self.min_candidates > 0 else 100,
                "last_checked": time.strftime("%Y-%m-%d %H:%M:%S"),
                "error": None,
            }
        except Exception as e:
            return {
                "total": 0,
                "candidates": 0,
                "error_count": 0,
                "threshold": self.min_candidates,
                "healthy": False,
                "percent": 0,
                "last_checked": time.strftime("%Y-%m-%d %H:%M:%S"),
                "error": str(e),
            }

    def test_connection(self, timeout: int = 10) -> Dict[str, Any]:
        try:
            files = self.fetch_auth_files(timeout)
            candidates = [f for f in files if _get_item_type(f).lower() == self.target_type.lower()]
            return {
                "ok": True,
                "total": len(files),
                "candidates": len(candidates),
                "message": f"连接成功，共 {len(files)} 个账号，{len(candidates)} 个 {self.target_type} 账号",
            }
        except Exception as e:
            return {"ok": False, "total": 0, "candidates": 0, "message": f"连接失败: {e}"}

    async def probe_accounts_async(
        self, workers: int = 20, timeout: int = 10, retries: int = 1,
    ) -> Dict[str, Any]:
        if aiohttp is None:
            raise RuntimeError("需要安装 aiohttp: pip install aiohttp")

        files = self.fetch_auth_files(timeout)
        candidates = [f for f in files if _get_item_type(f).lower() == self.target_type.lower()]

        if not candidates:
            return {"total": len(files), "candidates": 0, "invalid": [], "files": files}

        semaphore = asyncio.Semaphore(max(1, workers))
        connector = aiohttp.TCPConnector(limit=max(1, workers))
        client_timeout = aiohttp.ClientTimeout(total=max(1, timeout))

        async def probe_one(session: aiohttp.ClientSession, item: Dict[str, Any]) -> Dict[str, Any]:
            auth_index = item.get("auth_index")
            name = item.get("name") or item.get("id")
            result = {
                "name": name,
                "auth_index": auth_index,
                "invalid_401": False,
                "invalid_used_percent": False,
                "used_percent": None,
                "error": None,
            }
            if not auth_index:
                result["error"] = "missing auth_index"
                return result

            account_id = _extract_account_id(item)
            call_header = {
                "Authorization": "Bearer $TOKEN$",
                "Content-Type": "application/json",
                "User-Agent": self.user_agent,
            }
            if account_id:
                call_header["Chatgpt-Account-Id"] = account_id

            payload = {
                "authIndex": auth_index,
                "method": "GET",
                "url": "https://chatgpt.com/backend-api/wham/usage",
                "header": call_header,
            }

            for attempt in range(retries + 1):
                try:
                    async with semaphore:
                        async with session.post(
                            f"{self.base_url}/v0/management/api-call",
                            headers={**_mgmt_headers(self.token), "Content-Type": "application/json"},
                            json=payload,
                            timeout=timeout,
                        ) as resp:
                            text = await resp.text()
                            if resp.status >= 400:
                                raise RuntimeError(f"HTTP {resp.status}: {text[:200]}")
                            data = _safe_json(text)
                            sc = data.get("status_code")
                            result["invalid_401"] = sc == 401
                            if sc == 200:
                                try:
                                    body_data = _safe_json(data.get("body", ""))
                                    used_pct = (body_data.get("rate_limit", {}).get("primary_window", {}).get("used_percent"))
                                    if used_pct is not None:
                                        result["used_percent"] = used_pct
                                        result["invalid_used_percent"] = used_pct >= self.used_percent_threshold
                                except Exception:
                                    pass
                            return result
                except Exception as e:
                    result["error"] = str(e)
                    if attempt >= retries:
                        return result
            return result

        async def delete_one(session: aiohttp.ClientSession, name: str) -> Dict[str, Any]:
            encoded = quote(name, safe="")
            try:
                async with semaphore:
                    async with session.delete(
                        f"{self.base_url}/v0/management/auth-files?name={encoded}",
                        headers=_mgmt_headers(self.token),
                        timeout=timeout,
                    ) as resp:
                        text = await resp.text()
                        data = _safe_json(text)
                        ok = resp.status == 200 and data.get("status") == "ok"
                        return {"name": name, "deleted": ok}
            except Exception:
                return {"name": name, "deleted": False}

        invalid_list = []
        async with aiohttp.ClientSession(connector=connector, timeout=client_timeout, trust_env=True) as session:
            tasks = [asyncio.create_task(probe_one(session, item)) for item in candidates]
            for task in asyncio.as_completed(tasks):
                result = await task
                if result.get("invalid_401") or result.get("invalid_used_percent"):
                    invalid_list.append(result)

        return {
            "total": len(files),
            "candidates": len(candidates),
            "invalid": invalid_list,
            "files": files,
        }

    async def clean_invalid_async(self, workers: int = 20, timeout: int = 10, retries: int = 1) -> Dict[str, Any]:
        if aiohttp is None:
            raise RuntimeError("需要安装 aiohttp: pip install aiohttp")

        probe_result = await self.probe_accounts_async(workers, timeout, retries)
        invalid = probe_result["invalid"]
        names = [str(r["name"]) for r in invalid if r.get("name")]

        deleted_ok = 0
        deleted_fail = 0

        if names:
            semaphore = asyncio.Semaphore(max(1, workers))
            connector = aiohttp.TCPConnector(limit=max(1, workers))
            client_timeout = aiohttp.ClientTimeout(total=max(1, timeout))

            async with aiohttp.ClientSession(connector=connector, timeout=client_timeout, trust_env=True) as session:
                async def do_delete(name: str) -> bool:
                    encoded = quote(name, safe="")
                    try:
                        async with semaphore:
                            async with session.delete(
                                f"{self.base_url}/v0/management/auth-files?name={encoded}",
                                headers=_mgmt_headers(self.token),
                                timeout=timeout,
                            ) as resp:
                                text = await resp.text()
                                data = _safe_json(text)
                                return resp.status == 200 and data.get("status") == "ok"
                    except Exception:
                        return False

                tasks = [asyncio.create_task(do_delete(n)) for n in names]
                for task in asyncio.as_completed(tasks):
                    if await task:
                        deleted_ok += 1
                    else:
                        deleted_fail += 1

        return {
            "total": probe_result["total"],
            "candidates": probe_result["candidates"],
            "invalid_count": len(invalid),
            "deleted_ok": deleted_ok,
            "deleted_fail": deleted_fail,
        }

    def probe_and_clean_sync(self, workers: int = 20, timeout: int = 10, retries: int = 1) -> Dict[str, Any]:
        return asyncio.run(self.clean_invalid_async(workers, timeout, retries))

    def calculate_gap(self, current_candidates: Optional[int] = None) -> int:
        if current_candidates is None:
            status = self.get_pool_status()
            if status.get("error"):
                raise RuntimeError(f"CPA 池状态查询失败: {status['error']}")
            current_candidates = status["candidates"]
        gap = self.min_candidates - current_candidates
        return max(0, gap)

    def upload_token(self, filename: str, token_data: Dict[str, Any], proxy: str = "") -> bool:
        if not self.base_url or not self.token:
            return False
        session = _build_session(proxy)
        content = json.dumps(token_data, ensure_ascii=False).encode("utf-8")
        files = {"file": (filename, content, "application/json")}
        headers = {"Authorization": f"Bearer {self.token}"}
        for attempt in range(3):
            try:
                resp = session.post(
                    f"{self.base_url}/v0/management/auth-files",
                    files=files, headers=headers, verify=False, timeout=30,
                )
                if resp.status_code in (200, 201, 204):
                    return True
            except Exception:
                pass
            if attempt < 2:
                time.sleep(2 ** attempt)
        return False


class Sub2ApiMaintainer:
    """Sub2Api 平台池维护 — 通过 Admin API 管理账号池"""

    def __init__(
        self,
        base_url: str,
        bearer_token: str,
        min_candidates: int = 200,
        email: str = "",
        password: str = "",
    ):
        self.base_url = base_url.rstrip("/")
        self.bearer_token = bearer_token
        self.min_candidates = min_candidates
        self.email = email
        self.password = password
        self._session = _build_session()

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.bearer_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _login(self) -> str:
        resp = self._session.post(
            f"{self.base_url}/api/v1/auth/login",
            json={"email": self.email, "password": self.password},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        token = (
            data.get("token")
            or data.get("access_token")
            or (data.get("data") or {}).get("token")
            or (data.get("data") or {}).get("access_token")
            or ""
        )
        if token:
            self.bearer_token = token
        return token

    def _request(self, method: str, path: str, **kwargs) -> _requests.Response:
        kwargs.setdefault("timeout", 15)
        url = f"{self.base_url}{path}"
        resp = self._session.request(method, url, headers=self._headers(), **kwargs)
        if resp.status_code == 401 and self.email and self.password:
            self._login()
            resp = self._session.request(method, url, headers=self._headers(), **kwargs)
        return resp

    def get_dashboard_stats(self, timeout: int = 15) -> Dict[str, Any]:
        resp = self._request(
            "GET", "/api/v1/admin/dashboard/stats",
            params={"timezone": "Asia/Shanghai"}, timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("data") if isinstance(data.get("data"), dict) else data

    def list_accounts(
        self, page: int = 1, page_size: int = 100, timeout: int = 15,
    ) -> Dict[str, Any]:
        params = {
            "page": page, "page_size": page_size,
            "platform": "openai", "type": "oauth",
        }
        resp = self._request(
            "GET", "/api/v1/admin/accounts",
            params=params, timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("data") if isinstance(data.get("data"), dict) else data

    def refresh_account(self, account_id: int, timeout: int = 30) -> bool:
        try:
            resp = self._request(
                "POST", f"/api/v1/admin/accounts/{account_id}/refresh",
                timeout=timeout,
            )
            return resp.status_code in (200, 201)
        except Exception:
            return False

    def delete_account(self, account_id: int, timeout: int = 15) -> bool:
        try:
            resp = self._request(
                "DELETE", f"/api/v1/admin/accounts/{account_id}",
                timeout=timeout,
            )
            return resp.status_code in (200, 204)
        except Exception:
            return False

    def get_pool_status(self, timeout: int = 15) -> Dict[str, Any]:
        try:
            stats = self.get_dashboard_stats(timeout)
            total = stats.get("total_accounts", 0)
            normal = stats.get("normal_accounts", 0)
            error = stats.get("error_accounts", 0)
            return {
                "total": total,
                "candidates": normal,
                "error_count": error,
                "threshold": self.min_candidates,
                "healthy": normal >= self.min_candidates,
                "percent": round(normal / self.min_candidates * 100, 1) if self.min_candidates > 0 else 100,
                "last_checked": time.strftime("%Y-%m-%d %H:%M:%S"),
                "error": None,
            }
        except Exception as e:
            return {
                "total": 0, "candidates": 0, "error_count": 0,
                "threshold": self.min_candidates, "healthy": False,
                "percent": 0, "last_checked": time.strftime("%Y-%m-%d %H:%M:%S"),
                "error": str(e),
            }

    def test_connection(self, timeout: int = 10) -> Dict[str, Any]:
        try:
            stats = self.get_dashboard_stats(timeout)
            total = stats.get("total_accounts", 0)
            normal = stats.get("normal_accounts", 0)
            error = stats.get("error_accounts", 0)
            return {
                "ok": True,
                "total": total,
                "normal": normal,
                "error": error,
                "message": f"连接成功，共 {total} 个账号，{normal} 正常，{error} 异常",
            }
        except Exception as e:
            return {"ok": False, "total": 0, "normal": 0, "error": 0,
                    "message": f"连接失败: {e}"}

    def _list_accounts_by_ids(
        self, ids: List[int], timeout: int = 15,
    ) -> Dict[int, str]:
        """查询指定 ID 的账号当前状态，返回 {id: status}"""
        result: Dict[int, str] = {}
        id_set = set(ids)
        page = 1
        while id_set:
            data = self.list_accounts(page=page, page_size=100, timeout=timeout)
            items = data.get("items") or []
            if not items:
                break
            for item in items:
                aid = item.get("id")
                if aid in id_set:
                    result[aid] = str(item.get("status", ""))
                    id_set.discard(aid)
            total = data.get("total", 0)
            if page * 100 >= total or len(items) < 100:
                break
            page += 1
        return result

    def probe_and_clean_sync(self, timeout: int = 15) -> Dict[str, Any]:
        all_accounts: List[Dict[str, Any]] = []
        page = 1
        page_size = 100
        while True:
            data = self.list_accounts(page=page, page_size=page_size, timeout=timeout)
            items = data.get("items") or []
            all_accounts.extend(items)
            if not items or len(items) < page_size:
                break
            total = data.get("total")
            if isinstance(total, int) and total > 0 and len(all_accounts) >= total:
                break
            page += 1

        error_accounts = [
            a for a in all_accounts
            if str(a.get("status", "")).lower() in ("error", "disabled")
        ]
        normal_count = len(all_accounts) - len(error_accounts)

        if not error_accounts:
            return {
                "total": len(all_accounts), "normal": normal_count,
                "error_count": 0, "refreshed": 0,
                "deleted_ok": 0, "deleted_fail": 0,
            }

        # 尝试刷新所有异常账号
        refreshed_ids = []
        refresh_failed: List[Dict[str, Any]] = []
        for acc in error_accounts:
            acc_id = acc.get("id")
            if not acc_id:
                refresh_failed.append(acc)
                continue
            if self.refresh_account(acc_id, timeout=30):
                refreshed_ids.append(acc_id)
            else:
                refresh_failed.append(acc)

        # 等待刷新生效后重新查询状态，确认是否真正恢复
        still_error: List[Dict[str, Any]] = list(refresh_failed)
        if refreshed_ids:
            time.sleep(2)
            recheck = self._list_accounts_by_ids(refreshed_ids, timeout=timeout)
            recovered = 0
            for acc_id in refreshed_ids:
                acc_status = recheck.get(acc_id, "error")
                if str(acc_status).lower() in ("error", "disabled"):
                    still_error.append({"id": acc_id})
                else:
                    recovered += 1
        else:
            recovered = 0

        # 删除不可恢复的账号
        deleted_ok = 0
        deleted_fail = 0
        for acc in still_error:
            acc_id = acc.get("id")
            if not acc_id:
                deleted_fail += 1
                continue
            if self.delete_account(acc_id, timeout=timeout):
                deleted_ok += 1
            else:
                deleted_fail += 1

        return {
            "total": len(all_accounts), "normal": normal_count,
            "error_count": len(error_accounts), "refreshed": recovered,
            "deleted_ok": deleted_ok, "deleted_fail": deleted_fail,
        }

    def calculate_gap(self, current_candidates: Optional[int] = None) -> int:
        if current_candidates is None:
            status = self.get_pool_status()
            if status.get("error"):
                raise RuntimeError(f"Sub2Api 池状态查询失败: {status['error']}")
            current_candidates = status["candidates"]
        return max(0, self.min_candidates - current_candidates)
