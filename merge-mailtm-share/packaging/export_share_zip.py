#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_NAME = "merge-mailtm-share"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "dist" / "share"
TEXT_SUFFIXES = {
    ".cmd",
    ".command",
    ".dockerignore",
    ".gitattributes",
    ".gitignore",
    ".js",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".sh",
    ".spec",
    ".toml",
    ".txt",
    ".xml",
    ".yml",
    ".yaml",
}
EXCLUDED_TOP_LEVEL = {
    ".git",
    ".idea",
    ".venv",
    ".venv-build-macos",
    ".venv-build-windows",
    "account",
    "build",
    "dist",
    "failed_register_tasks",
    "logs",
    "output_fixed",
}
EXCLUDED_PARTS = {"__pycache__"}
EXCLUDED_SUFFIXES = {".log", ".pyc", ".pyo", ".tar.gz", ".zip"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导出可对外分享的脱敏 zip 包")
    parser.add_argument(
        "--name",
        default=DEFAULT_NAME,
        help="zip 根目录名称，默认 merge-mailtm-share",
    )
    parser.add_argument(
        "--output",
        default="",
        help="输出 zip 路径，默认写入 dist/share/<name>.zip",
    )
    return parser.parse_args()


def list_repo_files(root_dir: Path) -> List[Path]:
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--modified", "--others", "--exclude-standard"],
            cwd=root_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        files: List[Path] = []
        seen = set()
        for line in result.stdout.splitlines():
            rel = line.strip()
            if not rel:
                continue
            abs_path = (root_dir / rel).resolve()
            if abs_path in seen or not abs_path.is_file():
                continue
            seen.add(abs_path)
            files.append(abs_path)
        return files
    except Exception:
        return [path for path in root_dir.rglob("*") if path.is_file()]


def should_exclude(rel_path: Path) -> bool:
    if not rel_path.parts:
        return True
    if rel_path.parts[0] in EXCLUDED_TOP_LEVEL:
        return True
    if any(part in EXCLUDED_PARTS for part in rel_path.parts):
        return True
    if rel_path.name in {".DS_Store"}:
        return True
    if any(rel_path.name.endswith(suffix) for suffix in EXCLUDED_SUFFIXES):
        return True
    return False


def load_repo_config(root_dir: Path) -> Dict[str, Any]:
    config_path = root_dir / "config.json"
    if not config_path.exists():
        return {}
    try:
        data = json.loads(config_path.read_text("utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def clone_json(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def sanitize_config_data(data: Dict[str, Any]) -> Dict[str, Any]:
    sanitized = clone_json(data)
    if not isinstance(sanitized, dict):
        return {}

    clean_cfg = sanitized.get("clean")
    if isinstance(clean_cfg, dict):
        if "base_url" in clean_cfg:
            clean_cfg["base_url"] = "http://your-management-host:8318"
        if "token" in clean_cfg:
            clean_cfg["token"] = "your-management-token"
        if "cpa_password" in clean_cfg:
            clean_cfg["cpa_password"] = "your-management-token"

    email_cfg = sanitized.get("email")
    if isinstance(email_cfg, dict):
        if "worker_domain" in email_cfg:
            email_cfg["worker_domain"] = "https://your-temp-mail-api.example.com"
        if "site_password" in email_cfg:
            email_cfg["site_password"] = "your-email-secret"
        if "custom_auth" in email_cfg:
            email_cfg["custom_auth"] = "your-email-secret"
        if "api_key" in email_cfg:
            email_cfg["api_key"] = "your-email-secret"
        if "duckmail_api_key" in email_cfg:
            email_cfg["duckmail_api_key"] = "your-email-secret"
        if "duckmail_bearer" in email_cfg:
            email_cfg["duckmail_bearer"] = "dk_your_duckmail_bearer"
        if isinstance(email_cfg.get("email_domains"), list) and email_cfg["email_domains"]:
            email_cfg["email_domains"] = ["your-domain.example.com"]

    run_cfg = sanitized.get("run")
    if isinstance(run_cfg, dict) and run_cfg.get("proxy"):
        run_cfg["proxy"] = "http://127.0.0.1:7897"

    upload_cfg = sanitized.get("upload")
    if isinstance(upload_cfg, dict):
        if "base_url" in upload_cfg:
            upload_cfg["base_url"] = "http://your-management-host:8318"
        if "token" in upload_cfg:
            upload_cfg["token"] = "your-management-token"
        if "cpa_password" in upload_cfg:
            upload_cfg["cpa_password"] = "your-management-token"

    if "duckmail_bearer" in sanitized:
        sanitized["duckmail_bearer"] = "dk_your_duckmail_bearer"
    return sanitized


def build_exact_replacements(config_data: Dict[str, Any]) -> List[Tuple[str, str]]:
    replacements: List[Tuple[str, str]] = []

    def add(old: Any, new: str) -> None:
        text = str(old or "").strip()
        if not text:
            return
        if text == new:
            return
        pair = (text, new)
        if pair not in replacements:
            replacements.append(pair)

    clean_cfg = config_data.get("clean")
    if isinstance(clean_cfg, dict):
        add(clean_cfg.get("base_url"), "http://your-management-host:8318")
        add(clean_cfg.get("token"), "your-management-token")
        add(clean_cfg.get("cpa_password"), "your-management-token")

    upload_cfg = config_data.get("upload")
    if isinstance(upload_cfg, dict):
        add(upload_cfg.get("base_url"), "http://your-management-host:8318")
        add(upload_cfg.get("token"), "your-management-token")
        add(upload_cfg.get("cpa_password"), "your-management-token")

    email_cfg = config_data.get("email")
    if isinstance(email_cfg, dict):
        add(email_cfg.get("worker_domain"), "https://your-temp-mail-api.example.com")
        add(email_cfg.get("site_password"), "your-email-secret")
        add(email_cfg.get("custom_auth"), "your-email-secret")
        add(email_cfg.get("api_key"), "your-email-secret")
        add(email_cfg.get("duckmail_api_key"), "your-email-secret")
        add(email_cfg.get("duckmail_bearer"), "dk_your_duckmail_bearer")
        domains = email_cfg.get("email_domains")
        if isinstance(domains, list):
            for domain in domains:
                add(domain, "your-domain.example.com")

    run_cfg = config_data.get("run")
    if isinstance(run_cfg, dict):
        proxy = str(run_cfg.get("proxy") or "").strip()
        if proxy and proxy != "http://127.0.0.1:7897":
            add(proxy, "http://127.0.0.1:7897")

    return sorted(replacements, key=lambda item: len(item[0]), reverse=True)


def is_text_file(path: Path) -> bool:
    return path.suffix.lower() in TEXT_SUFFIXES or path.name in {"Dockerfile", "LICENSE", "Makefile"}


def sanitize_text(rel_path: Path, text: str, config_data: Dict[str, Any], replacements: List[Tuple[str, str]]) -> str:
    if rel_path == Path("config.json"):
        return json.dumps(sanitize_config_data(config_data), ensure_ascii=False, indent=2) + "\n"

    sanitized = text
    for old, new in replacements:
        sanitized = sanitized.replace(old, new)

    sanitized = re.sub(r"dk_[0-9a-fA-F]{24,}", "dk_your_duckmail_bearer", sanitized)
    sanitized = re.sub(
        r'(["\']authorization["\']\s*:\s*["\']Bearer\s+)([^"\']+)(["\'])',
        r"\1your-management-token\3",
        sanitized,
        flags=re.IGNORECASE,
    )
    sanitized = re.sub(
        r'(["\']duckmail_bearer["\']\s*:\s*["\'])([^"\']*)(["\'])',
        r"\1dk_your_duckmail_bearer\3",
        sanitized,
        flags=re.IGNORECASE,
    )
    return sanitized


def write_manifest(export_root: Path, included_files: Iterable[Path], excluded_files: Iterable[Path]) -> None:
    manifest = {
        "note": "This package is generated for external sharing. Sensitive runtime files are excluded and selected config/example values are redacted.",
        "included_files": [path.as_posix() for path in sorted(included_files)],
        "excluded_files": [path.as_posix() for path in sorted(excluded_files)],
    }
    (export_root / "share_package_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def assert_no_sensitive_values(export_root: Path, replacements: List[Tuple[str, str]]) -> None:
    forbidden_values = {old for old, _ in replacements if len(old) >= 4}
    for path in export_root.rglob("*"):
        if not path.is_file() or not is_text_file(path):
            continue
        try:
            text = path.read_text("utf-8")
        except Exception:
            continue
        for value in forbidden_values:
            if value and value in text:
                raise RuntimeError(f"导出包仍包含敏感值，请检查: {path}")
        if re.search(r"dk_[0-9a-fA-F]{24,}", text):
            raise RuntimeError(f"导出包仍包含 DuckMail Bearer 风格密钥，请检查: {path}")


def build_zip(source_dir: Path, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(source_dir.rglob("*")):
            if not path.is_file():
                continue
            zf.write(path, arcname=path.relative_to(source_dir))


def main() -> int:
    args = parse_args()
    package_name = args.name.strip() or DEFAULT_NAME
    output_path = Path(args.output).expanduser().resolve() if args.output else (DEFAULT_OUTPUT_DIR / f"{package_name}.zip")

    config_data = load_repo_config(ROOT_DIR)
    replacements = build_exact_replacements(config_data)

    repo_files = list_repo_files(ROOT_DIR)
    included: List[Path] = []
    excluded: List[Path] = []

    with tempfile.TemporaryDirectory(prefix="merge_mailtm_share_") as tmpdir:
        temp_root = Path(tmpdir) / package_name
        temp_root.mkdir(parents=True, exist_ok=True)

        for abs_path in repo_files:
            rel_path = abs_path.relative_to(ROOT_DIR)
            if should_exclude(rel_path):
                excluded.append(rel_path)
                continue

            target_path = temp_root / rel_path
            target_path.parent.mkdir(parents=True, exist_ok=True)

            if is_text_file(rel_path):
                text = abs_path.read_text("utf-8")
                sanitized = sanitize_text(rel_path, text, config_data, replacements)
                target_path.write_text(sanitized, encoding="utf-8", newline="\n")
            else:
                shutil.copy2(abs_path, target_path)
            included.append(rel_path)

        write_manifest(temp_root, included, excluded)
        assert_no_sensitive_values(temp_root, replacements)
        build_zip(temp_root.parent, output_path)

    print(f"导出完成: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
