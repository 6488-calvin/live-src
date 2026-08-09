# -*- coding: utf-8 -*-
"""通过 Gitee v5 Contents API 推送 live.txt 和 live.m3u8 到指定仓库

绕开 Gitee 对 OAuth2 over HTTPS git push 的 IP 屏蔽（Actions 来源 IP 经常被 Gitee 拒 403）。
Gitee API 用 HTTP POST/PUT，对 IP 屏蔽不敏感，只要有 projects 权限的 token 即可推送。
"""
import base64
import os
import sys
import time

import requests

API = "https://gitee.com/api/v5"
REPO = "live-src"


def _safe_json_dict(r):
    """requests response 安全解析：空响应/非 JSON/list 返回一律返回 None。"""
    try:
        data = r.json()
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def get_sha(owner: str, repo: str, path: str, token: str):
    """查询现有文件的 sha。Gitee API 限流时可能返回空响应/list/非 JSON，全部视为无文件。"""
    r = requests.get(
        f"{API}/repos/{owner}/{repo}/contents/{path}",
        params={"access_token": token},
        timeout=20,
    )
    data = _safe_json_dict(r)
    return data.get("sha") if data else None


def push_file(owner: str, repo: str, path: str, content: str,
              token: str, message: str):
    sha = get_sha(owner, repo, path, token)
    payload = {
        "access_token": token,
        "content": base64.b64encode(content.encode("utf-8")).decode(),
        "message": message,
    }
    url = f"{API}/repos/{owner}/{repo}/contents/{path}"
    if sha:
        payload["sha"] = sha
        r = requests.put(url, json=payload, timeout=30)
    else:
        r = requests.post(url, json=payload, timeout=30)
    if r.status_code not in (200, 201):
        raise RuntimeError(
            f"推送 {path} 失败 HTTP {r.status_code}: {r.text[:200]}")
    return _safe_json_dict(r) or {}


def main():
    owner = os.environ.get("GITEE_USER")
    token = os.environ.get("GITEE_TOKEN")
    if not (owner and token):
        print("缺少环境变量 GITEE_USER / GITEE_TOKEN")
        sys.exit(1)
    local_files = sys.argv[1:] if len(sys.argv) > 1 else ["live.txt", "live.m3u8"]
    ok = 0
    for local in local_files:
        if not os.path.exists(local):
            print(f"[跳过] 本地不存在: {local}")
            continue
        with open(local, "r", encoding="utf-8") as f:
            content = f.read()
        msg = f"auto update {local} {time.strftime('%Y-%m-%d %H:%M:%S')}"
        push_file(owner, REPO, local, content, token, msg)
        print(f"[OK] 推送 {local}")
        ok += 1
    print(f"完成，共 {ok} 个文件推到 Gitee {owner}/{REPO}")


if __name__ == "__main__":
    main()
