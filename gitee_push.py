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
REPO = "live-src"   # 必须与 Gitee 仓库名一致（README-云端部署.md 第 3 步）


def get_sha(owner: str, repo: str, path: str, token: str):
    """查询现有文件的 sha（更新时需要）。Gitee API 在限流或边缘情况可能返回
    list 形式的错误而非 dict，需健壮处理。"""
    r = requests.get(
        f"{API}/repos/{owner}/{repo}/contents/{path}",
        params={"access_token": token},
        timeout=20,
    )
    if r.status_code == 200:
        data = r.json()
        if isinstance(data, dict):
            return data.get("sha")
    return None


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
    return r.json()


def main():
    owner = os.environ.get("GITEE_USER")
    token = os.environ.get("GITEE_TOKEN")
    if not (owner and token):
        print("缺少环境变量 GITEE_USER / GITEE_TOKEN")
        sys.exit(1)

    # 命令行参数：可指定多个本地文件路径（依次推送到 Gitee 同名文件）
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