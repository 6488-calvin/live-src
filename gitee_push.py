# -*- coding: utf-8 -*-
"""通过 Gitee v5 Contents API 推送 live.txt 和 live.m3u8 到指定仓库

绕开 Gitee 对 OAuth2 over HTTPS git push 的 IP 屏蔽（Actions 来源 IP 经常被 Gitee 拒 403）。
Gitee API 用 HTTP POST/PUT，对 IP 屏蔽不敏感，只要有 projects 权限的 token 即可推送。

针对 Gitee 限流/缓存导致的 "Blob SHA does not match" 做了重试：
  - get_sha 失败时（限流返回非 dict）自动重试取最新 sha；
  - PUT 报 Blob SHA 不匹配时，重新拉取最新 sha 再重试。
"""
import base64
import os
import sys
import time
import json
import urllib.request
import urllib.error

API = "https://gitee.com/api/v5"
REPO = "live-src"   # 必须与 Gitee 仓库名一致（README-云端部署.md 第 3 步）


def _req(method, url, token, payload=None, retries=3):
    """带退避重试的请求；返回 (status, dict)。限流(403/429)会重试。"""
    last = None
    for i in range(retries):
        try:
            data = json.dumps(payload).encode() if payload is not None else None
            req = urllib.request.Request(
                url, data=data, method=method,
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            last = e
            body = e.read().decode()
            try:
                resp = json.loads(body) if body.strip().startswith("{") else {"message": body}
            except ValueError:
                resp = {"message": body}
            # 限流 / 服务器忙 -> 退避重试
            if e.code in (403, 429) or "rate" in body.lower() or "busy" in body.lower():
                time.sleep(2 * (i + 1))
                continue
            return e.code, resp
        except Exception as e:  # 网络抖动等
            last = e
            time.sleep(2 * (i + 1))
            continue
    return getattr(last, "code", -1), {"message": str(last)}


def get_sha(owner, repo, path, token, retries=4):
    """查询现有文件的 sha（更新时需要）。Gitee 限流时可能返回非 dict，
    做最健壮处理：重试直到拿到有效 sha；都不行则返回 None（视为新文件）。"""
    for i in range(retries):
        st, d = _req("GET",
                     f"{API}/repos/{owner}/{repo}/contents/{path}?access_token={token}",
                     token)
        if st == 200 and isinstance(d, dict) and d.get("sha"):
            return d["sha"]
        time.sleep(1.5 * (i + 1))
    return None


def push_file(owner, repo, path, content, token, message, max_tries=3):
    """推送单个文件：带 sha 重试，规避 "Blob SHA does not match"。"""
    sha = get_sha(owner, repo, path, token)
    for attempt in range(max_tries):
        payload = {
            "access_token": token,
            "content": base64.b64encode(content.encode("utf-8")).decode(),
            "message": message,
        }
        url = f"{API}/repos/{owner}/{repo}/contents/{path}"
        if sha:
            payload["sha"] = sha
            st, resp = _req("PUT", url, token, payload)
        else:
            st, resp = _req("POST", url, token, payload)

        if st in (200, 201):
            return resp

        msg = (resp.get("message") if isinstance(resp, dict) else str(resp)) or ""
        # Blob SHA 不匹配：重新拉取最新 sha 再试一次
        if "Blob SHA" in msg or "sha" in msg.lower():
            sha = get_sha(owner, repo, path, token)
            time.sleep(1.5)
            continue
        # 其它错误直接抛出
        raise RuntimeError(f"推送 {path} 失败 HTTP {st}: {msg[:200]}")

    raise RuntimeError(f"推送 {path} 失败：重试 {max_tries} 次仍 Blob SHA 不匹配")


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
        name = os.path.basename(local)  # 远程用文件名，不是完整路径
        msg = f"auto update {name} {time.strftime('%Y-%m-%d %H:%M:%S')}"
        push_file(owner, REPO, name, content, token, msg)
        print(f"[OK] 推送 {name}")
        ok += 1

    print(f"完成，共 {ok} 个文件推到 Gitee {owner}/{REPO}")


if __name__ == "__main__":
    main()
