"""personal-crm 数据质量看门狗客户端（langgraph-pse 侧）。

单一真源：数据质量检查逻辑现在只存在于 personal-crm 的 `crud.get_qa_report`
（经 `routers/qa.py` 的 `GET /api/qa/report` 暴露）。本文件不再重扫数据库，
而是调用该 API，确保检查项只有一份定义、不会双仓漂移。

认证（按优先级）：
- 环境变量 `CRM_API_TOKEN`：直接作为 Bearer token 使用（用户登录后填入）。
- 若未设置 token，但设置了 `CRM_API_USER` / `CRM_API_PASS`，则自动
  `POST /api/auth/login` 获取 token 后重试。
- 若服务端未启用认证（Demo 模式），可不带 token 直接访问。

用法（独立 CLI）：
    python qa_scan.py [--api-base-url URL] [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from urllib.parse import urljoin

# 中性占位默认值——真实后端地址只通过环境变量 CRM_API_BASE_URL 注入，
# 避免在源码中硬编码个人本地地址（隐私考量）。
DEFAULT_API_BASE_URL = os.getenv("CRM_API_BASE_URL", "http://127.0.0.1:8000")


def _resolve_token(base_url: str) -> str | None:
    """按优先级解析 Bearer token：先 CRM_API_TOKEN，否则尝试自动登录。"""
    token = os.getenv("CRM_API_TOKEN")
    if token:
        return token
    user = os.getenv("CRM_API_USER")
    password = os.getenv("CRM_API_PASS")
    if user and password:
        try:
            login_url = urljoin(base_url.rstrip("/") + "/", "api/auth/login")
            req = urllib.request.Request(
                login_url,
                data=json.dumps({"username": user, "password": password}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            return body.get("access_token")
        except Exception as e:  # noqa: BLE001
            print(f"⚠️ 自动登录失败: {e}", file=sys.stderr)
    return None


def fetch_qa_report(base_url: str | None = None, token: str | None = None) -> dict:
    """调用 personal-crm 的 GET /api/qa/report，返回结构化质量报告。

    返回结构与旧版裸 sqlite 扫描一致（含 summary / findings / ok），
    因此下游 run.py 的 verify_fn 等无需改动即可复用。
    """
    base = (base_url or DEFAULT_API_BASE_URL).rstrip("/")
    url = urljoin(base + "/", "api/qa/report")
    token = token or _resolve_token(base)
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # 401 且无 token：尝试自动登录后重试一次
        if e.code == 401 and not token:
            new_token = _resolve_token(base)
            if new_token:
                headers["Authorization"] = f"Bearer {new_token}"
                req2 = urllib.request.Request(url, headers=headers, method="GET")
                with urllib.request.urlopen(req2, timeout=15) as resp2:
                    return json.loads(resp2.read().decode("utf-8"))
        raise RuntimeError(
            f"调用 {url} 失败 (HTTP {e.code})。\n"
            f"请在 langgraph-pse 的 .env 设置 CRM_API_TOKEN，或 CRM_API_USER/CRM_API_PASS 以自动登录；\n"
            f"并确保 personal-crm 后端服务正在运行（默认 {base}）。"
        ) from e
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"无法连接 personal-crm 后端 {base}：{e.reason}\n"
            f"请先启动 personal-crm 后端（uvicorn app.main:app），或用 CRM_API_BASE_URL 指向正确地址。"
        ) from e


def main():
    ap = argparse.ArgumentParser(description="personal-crm 数据质量看门狗客户端（调用 /api/qa/report）")
    ap.add_argument("--api-base-url", default=DEFAULT_API_BASE_URL,
                   help="personal-crm 后端地址（含 http://），默认取 CRM_API_BASE_URL 或 http://127.0.0.1:8000")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出完整报告")
    args = ap.parse_args()
    try:
        result = fetch_qa_report(args.api_base_url)
    except RuntimeError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    s = result.get("summary", {})
    print(f"数据质量 API: {args.api_base_url}/api/qa/report")
    print(f"概况: contacts={s.get('contacts')}  contact_records={s.get('contact_records')}  "
          f"chat_messages={s.get('chat_messages')}")
    findings = result.get("findings", [])
    print(f"\n发现 {len(findings)} 项:")
    for f in findings:
        print(f"  [{f['severity'].upper()}] {f['check']} (count={f['count']})")
        print(f"      {f['description']}")
        if f.get("samples"):
            print(f"      样本: {', '.join(f['samples'][:5])}")
    print("\n结论:", "✅ 无明显高/中危问题" if result.get("ok") else "⚠️ 存在需关注的问题")


if __name__ == "__main__":
    main()
