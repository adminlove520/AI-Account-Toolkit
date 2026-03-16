#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import argparse
import asyncio
import json
import os
import sys
import urllib.parse

import requests

try:
    import aiohttp
except Exception:
    aiohttp = None

DEFAULT_BASE_URL = "你的CPA地址"
DEFAULT_UA = "codex_cli_rs/0.76.0 (Debian 13.0.0; x86_64) WindowsTerminal"
DEFAULT_TIMEOUT = 12
DEFAULT_CONFIG_PATH = "config.json"


def safe_json(resp):
    try:
        return resp.json()
    except Exception:
        return {}


def safe_json_text(text):
    try:
        return json.loads(text)
    except Exception:
        return {}


def mgmt_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }


def get_item_type(item):
    # 兼容后端字段命名差异（type / typo）
    return item.get("type") or item.get("typo")


def extract_chatgpt_account_id(item):
    for key in ("chatgpt_account_id", "chatgptAccountId", "account_id", "accountId"):
        val = item.get(key)
        if val:
            return val
    return None


def headers_to_dict(headers):
    result = {}
    for h in headers or []:
        name = str(h.get("name") or "").strip().lower()
        value = h.get("value")
        if name and (value is not None) and name not in result:
            result[name] = value
    return result


def load_context_from_har(har_path):
    with open(har_path, "r", encoding="utf-8") as f:
        har = json.load(f)

    entries = (((har.get("log") or {}).get("entries")) or [])
    token = None
    base_url = None
    chatgpt_account_id = None
    user_agent = None

    for entry in entries:
        req = entry.get("request") or {}
        url = req.get("url") or ""
        method = str(req.get("method") or "").upper()
        headers = headers_to_dict(req.get("headers"))

        auth = headers.get("authorization")
        if (not token) and auth and auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1].strip()

        if (not base_url) and url:
            parsed = urllib.parse.urlparse(url)
            if parsed.scheme and parsed.netloc:
                base_url = f"{parsed.scheme}://{parsed.netloc}"

        if (not user_agent) and headers.get("user-agent"):
            user_agent = headers.get("user-agent")

        if (not chatgpt_account_id) and headers.get("chatgpt-account-id"):
            chatgpt_account_id = headers.get("chatgpt-account-id")

        if ("/v0/management/api-call" in url) and method == "POST":
            post_text = ((req.get("postData") or {}).get("text")) or ""
            if post_text:
                try:
                    payload = json.loads(post_text)
                    header = payload.get("header") or {}
                    if (not chatgpt_account_id) and header.get("Chatgpt-Account-Id"):
                        chatgpt_account_id = header.get("Chatgpt-Account-Id")
                    if (not user_agent) and header.get("User-Agent"):
                        user_agent = header.get("User-Agent")
                except Exception:
                    pass

    return {
        "token": token,
        "base_url": base_url,
        "chatgpt_account_id": chatgpt_account_id,
        "user_agent": user_agent,
    }


def load_config_json(config_path):
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            conf = json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        raise RuntimeError(f"读取配置文件失败: {e}")

    if not isinstance(conf, dict):
        raise RuntimeError("配置文件格式错误: 顶层必须是 JSON 对象")

    return conf


def fetch_auth_files(base_url, token, timeout):
    resp = requests.get(
        f"{base_url}/v0/management/auth-files",
        headers=mgmt_headers(token),
        timeout=timeout,
    )
    resp.raise_for_status()
    data = safe_json(resp)
    return data.get("files", [])


def build_probe_payload(auth_index, user_agent, chatgpt_account_id=None):
    call_header = {
        "Authorization": "Bearer $TOKEN$",
        "Content-Type": "application/json",
        "User-Agent": user_agent,
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
    session,
    semaphore,
    base_url,
    token,
    item,
    user_agent,
    fallback_account_id=None,
    timeout=DEFAULT_TIMEOUT,
    retries=0,
):
    auth_index = item.get("auth_index")
    name = item.get("name") or item.get("id")
    account = item.get("account") or item.get("email") or ""

    result = {
        "name": name,
        "account": account,
        "auth_index": auth_index,
        "type": get_item_type(item),
        "provider": item.get("provider"),
        "status_code": None,
        "invalid_401": False,
        "error": None,
    }

    if not auth_index:
        result["error"] = "missing auth_index"
        return result

    chatgpt_account_id = extract_chatgpt_account_id(item) or fallback_account_id
    payload = build_probe_payload(auth_index, user_agent, chatgpt_account_id)

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
                    result["invalid_401"] = (sc == 401)
                    if sc is None:
                        result["error"] = "missing status_code in api-call response"
                    else:
                        result["error"] = None
                    return result
        except Exception as e:
            result["error"] = str(e)
            if attempt >= retries:
                return result

    return result


async def delete_account_async(session, semaphore, base_url, token, name, timeout):
    if not name:
        return {"name": None, "deleted": False, "error": "missing name"}

    encoded_name = urllib.parse.quote(name, safe="")
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


def prompt_int(label, default_value, min_value=1):
    raw = input(f"{label}（默认 {default_value}）: ").strip()
    if not raw:
        return default_value
    try:
        value = int(raw)
        if value < min_value:
            print(f"输入过小，使用最小值 {min_value}")
            return min_value
        return value
    except Exception:
        print("输入无效，使用默认值")
        return default_value


def choose_mode_interactive():
    print("\n请选择操作:")
    print("1) 仅检查 401 并导出")
    print("2) 检查 401 并立即删除")
    print("3) 直接删除 output 文件中的账号")
    print("0) 退出")
    while True:
        choice = input("请输入选项编号: ").strip()
        if choice == "1":
            return "check"
        if choice == "2":
            return "check_delete"
        if choice == "3":
            return "delete_from_output"
        if choice == "0":
            return "exit"
        print("无效选项，请重新输入。")


def ensure_aiohttp():
    if aiohttp is None:
        print("错误: 未安装 aiohttp。", file=sys.stderr)
        print("请先执行: pip install aiohttp", file=sys.stderr)
        sys.exit(1)


async def run_probe_async(
    base_url,
    token,
    target_type,
    provider,
    workers,
    timeout,
    retries,
    user_agent,
    chatgpt_account_id,
    output,
):
    files = fetch_auth_files(base_url, token, timeout)
    candidates = []
    for f in files:
        if str(get_item_type(f) or "").lower() != target_type.lower():
            continue
        if provider and str(f.get("provider", "")).lower() != provider.lower():
            continue
        candidates.append(f)

    print(f"总账号数: {len(files)}")
    print(f"符合过滤条件账号数: {len(candidates)}")
    print(f"异步检测并发: workers={workers}, timeout={timeout}s, retries={retries}")

    if not candidates:
        with open(output, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=2)
        print(f"已导出: {output}")
        return []

    connector = aiohttp.TCPConnector(limit=max(1, workers), limit_per_host=max(1, workers))
    client_timeout = aiohttp.ClientTimeout(total=max(1, timeout))
    semaphore = asyncio.Semaphore(max(1, workers))

    probe_results = []
    async with aiohttp.ClientSession(connector=connector, timeout=client_timeout, trust_env=True) as session:
        tasks = [
            asyncio.create_task(
                probe_account_async(
                    session,
                    semaphore,
                    base_url,
                    token,
                    item,
                    user_agent,
                    chatgpt_account_id,
                    timeout,
                    retries,
                )
            )
            for item in candidates
        ]

        total = len(tasks)
        done = 0
        next_report = 100
        for task in asyncio.as_completed(tasks):
            probe_results.append(await task)
            done += 1
            if (done >= next_report) or (done == total):
                print(f"检测进度: {done}/{total}")
                next_report += 100

    invalid_401 = [r for r in probe_results if r.get("invalid_401")]
    failed_probe = [r for r in probe_results if r.get("error")]

    invalid_401.sort(key=lambda x: (x.get("name") or ""))
    print(f"探测完成: 401失效={len(invalid_401)}，探测异常={len(failed_probe)}")

    for r in invalid_401:
        print(f"[401] {r.get('name')} | account={r.get('account')} | auth_index={r.get('auth_index')}")

    with open(output, "w", encoding="utf-8") as f:
        json.dump(invalid_401, f, ensure_ascii=False, indent=2)
    print(f"已导出: {output}")

    return invalid_401


def load_names_from_output(output):
    try:
        with open(output, "r", encoding="utf-8") as f:
            rows = json.load(f)
    except Exception as e:
        raise RuntimeError(f"读取 output 文件失败: {e}")

    names_to_delete = []
    for r in rows if isinstance(rows, list) else []:
        name = (r or {}).get("name")
        if name:
            names_to_delete.append(name)
    return names_to_delete


async def run_delete_async(base_url, token, names_to_delete, delete_workers, timeout, need_confirm=True):
    if not names_to_delete:
        print("没有可删除账号。")
        return

    print(f"待删除账号数: {len(names_to_delete)}")
    if need_confirm:
        confirm = input(f"即将删除 {len(names_to_delete)} 个账号，输入 DELETE 确认: ").strip()
        if confirm != "DELETE":
            print("已取消删除。")
            return

    connector = aiohttp.TCPConnector(limit=max(1, delete_workers), limit_per_host=max(1, delete_workers))
    client_timeout = aiohttp.ClientTimeout(total=max(1, timeout))
    semaphore = asyncio.Semaphore(max(1, delete_workers))

    delete_results = []
    async with aiohttp.ClientSession(connector=connector, timeout=client_timeout, trust_env=True) as session:
        tasks = [
            asyncio.create_task(
                delete_account_async(session, semaphore, base_url, token, name, timeout)
            )
            for name in names_to_delete
        ]

        total = len(tasks)
        done = 0
        next_report = 100
        for task in asyncio.as_completed(tasks):
            delete_results.append(await task)
            done += 1
            if (done >= next_report) or (done == total):
                print(f"删除进度: {done}/{total}")
                next_report += 100

    success = [r for r in delete_results if r.get("deleted")]
    failed = [r for r in delete_results if not r.get("deleted")]
    print(f"删除完成: 成功={len(success)}，失败={len(failed)}")
    if failed:
        for r in failed:
            print(f"[删除失败] {r.get('name')} | {r.get('error')}")


def main():
    parser = argparse.ArgumentParser(description="批量检查 type=codex 账号并删除 401 失效账号")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="配置文件路径（默认: config.json）")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--token", default=os.getenv("MGMT_TOKEN"))
    parser.add_argument("--har", default=None, help="从浏览器导出的 HAR 自动提取 token/base-url/UA/Chatgpt-Account-Id")
    parser.add_argument("--target-type", default="codex", help="按 files[].type（或 typo）过滤")
    parser.add_argument("--provider", default=None, help="可选：再按 provider 过滤")
    parser.add_argument("--workers", type=int, default=120, help="并发数（401检测）")
    parser.add_argument("--delete-workers", type=int, default=20, help="并发数（删除）")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="每次请求超时秒数")
    parser.add_argument("--retries", type=int, default=1, help="单账号探测失败重试次数")
    parser.add_argument("--user-agent", default=DEFAULT_UA)
    parser.add_argument("--chatgpt-account-id", default=os.getenv("CHATGPT_ACCOUNT_ID"))
    parser.add_argument("--output", default="invalid_codex_accounts.json")
    parser.add_argument("--delete", action="store_true", help="开启后执行删除")
    parser.add_argument("--delete-from-output", action="store_true", help="从 output 文件读取账号直接删除（跳过401检测）")
    parser.add_argument("--yes", action="store_true", help="删除时跳过二次确认")
    args = parser.parse_args()

    try:
        conf = load_config_json(args.config)
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)

    # config.json 优先填充默认值；命令行显式参数仍可覆盖
    if conf.get("base_url") and args.base_url == DEFAULT_BASE_URL:
        args.base_url = conf.get("base_url")
    if conf.get("token") and (not args.token):
        args.token = conf.get("token")
    if conf.get("cpa_password") and (not args.token):
        args.token = conf.get("cpa_password")
    if conf.get("user_agent") and args.user_agent == DEFAULT_UA:
        args.user_agent = conf.get("user_agent")
    if conf.get("chatgpt_account_id") and (not args.chatgpt_account_id):
        args.chatgpt_account_id = conf.get("chatgpt_account_id")
    if conf.get("target_type") and args.target_type == "codex":
        args.target_type = conf.get("target_type")
    if conf.get("provider") and (not args.provider):
        args.provider = conf.get("provider")
    if isinstance(conf.get("workers"), int) and args.workers == 120:
        args.workers = conf.get("workers")
    if isinstance(conf.get("delete_workers"), int) and args.delete_workers == 20:
        args.delete_workers = conf.get("delete_workers")
    if isinstance(conf.get("timeout"), int) and args.timeout == DEFAULT_TIMEOUT:
        args.timeout = conf.get("timeout")
    if isinstance(conf.get("retries"), int) and args.retries == 1:
        args.retries = conf.get("retries")
    if conf.get("output") and args.output == "invalid_codex_accounts.json":
        args.output = conf.get("output")

    if args.har:
        try:
            ctx = load_context_from_har(args.har)
        except Exception as e:
            print(f"错误: 解析 HAR 失败: {e}", file=sys.stderr)
            sys.exit(1)

        if (not args.token) and ctx.get("token"):
            args.token = ctx.get("token")
        if (args.base_url == DEFAULT_BASE_URL) and ctx.get("base_url"):
            args.base_url = ctx.get("base_url")
        if (args.user_agent == DEFAULT_UA) and ctx.get("user_agent"):
            args.user_agent = ctx.get("user_agent")
        if (not args.chatgpt_account_id) and ctx.get("chatgpt_account_id"):
            args.chatgpt_account_id = ctx.get("chatgpt_account_id")

    args.base_url = (args.base_url or DEFAULT_BASE_URL).rstrip("/")

    if not args.token:
        try:
            args.token = input("请输入管理 token（Bearer 后面的值）: ").strip()
        except Exception:
            args.token = None

    if not args.token:
        print("错误: 缺少管理 token。请提供 --har（从抓包提取）或 --token/MGMT_TOKEN。", file=sys.stderr)
        sys.exit(1)

    ensure_aiohttp()

    # 交互模式：无命令行动作参数时，给用户菜单选择
    if not args.delete and not args.delete_from_output:
        mode = choose_mode_interactive()
        if mode == "exit":
            print("已退出。")
            return

        # 用户自定义并发参数
        args.workers = prompt_int("请输入检测并发 workers", args.workers)
        args.delete_workers = prompt_int("请输入删除并发 delete-workers", args.delete_workers)
        args.timeout = prompt_int("请输入请求超时 timeout(秒)", args.timeout)
        args.retries = prompt_int("请输入失败重试 retries", args.retries, min_value=0)

        if mode == "check":
            asyncio.run(
                run_probe_async(
                    args.base_url,
                    args.token,
                    args.target_type,
                    args.provider,
                    args.workers,
                    args.timeout,
                    args.retries,
                    args.user_agent,
                    args.chatgpt_account_id,
                    args.output,
                )
            )
            return

        if mode == "check_delete":
            invalid_401 = asyncio.run(
                run_probe_async(
                    args.base_url,
                    args.token,
                    args.target_type,
                    args.provider,
                    args.workers,
                    args.timeout,
                    args.retries,
                    args.user_agent,
                    args.chatgpt_account_id,
                    args.output,
                )
            )
            names_to_delete = [r.get("name") for r in invalid_401 if r.get("name")]
            asyncio.run(
                run_delete_async(
                    args.base_url,
                    args.token,
                    names_to_delete,
                    args.delete_workers,
                    args.timeout,
                    need_confirm=not args.yes,
                )
            )
            return

        if mode == "delete_from_output":
            try:
                names_to_delete = load_names_from_output(args.output)
            except Exception as e:
                print(f"错误: {e}", file=sys.stderr)
                sys.exit(1)
            asyncio.run(
                run_delete_async(
                    args.base_url,
                    args.token,
                    names_to_delete,
                    args.delete_workers,
                    args.timeout,
                    need_confirm=not args.yes,
                )
            )
            return

    # 命令行参数模式（兼容旧用法）
    if args.delete_from_output:
        try:
            names_to_delete = load_names_from_output(args.output)
        except Exception as e:
            print(f"错误: {e}", file=sys.stderr)
            sys.exit(1)
        asyncio.run(
            run_delete_async(
                args.base_url,
                args.token,
                names_to_delete,
                args.delete_workers,
                args.timeout,
                need_confirm=not args.yes,
            )
        )
        return

    invalid_401 = asyncio.run(
        run_probe_async(
            args.base_url,
            args.token,
            args.target_type,
            args.provider,
            args.workers,
            args.timeout,
            args.retries,
            args.user_agent,
            args.chatgpt_account_id,
            args.output,
        )
    )

    if args.delete:
        names_to_delete = [r.get("name") for r in invalid_401 if r.get("name")]
        asyncio.run(
            run_delete_async(
                args.base_url,
                args.token,
                names_to_delete,
                args.delete_workers,
                args.timeout,
                need_confirm=not args.yes,
            )
        )
    else:
        print("当前为仅检查模式。")


if __name__ == "__main__":
    main()