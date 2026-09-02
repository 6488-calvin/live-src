# -*- coding: utf-8 -*-
"""通过 Gitee v5 Contents API 推送 live.txt 和 live.m3u8 到指定仓库

绕开 Gitee 对 OAuth2 over HTTPS git push 的 IP 屏蔽（Actions 来源 IP 经常被 Gitee 拒 403）。
Gitee API 用 HTTP POST/PUT，对 IP 屏蔽不敏感，只要有 projects 权限的 token 即可推送。

针对 Gitee 对 Actions 运行机 IP 的间歇限流做了多层重试：
  - _req：遇 403/429/服务器忙 自动退避重试；
  - get_sha：多次重试取最新 sha（限流时常返回非 dict）；
  - push_file：PUT 报 Blob SHA 不匹配、或 POST 报"文件已存在/新建失败"时，
    都重新拉取 sha 再走 PUT，直到成功或穷尽重试。
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


def _req(method, url, token, payload=None, retries=5):
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
            if e.code in (403, 429) or "rate" in body.lower() or "busy" in body.lower():
                time.sleep(min(3 * (i + 1), 20))  # 退避 3/6/9/12/15s
                continue
            return e.code, resp
        except Exception as e:  # 网络抖动等
            last = e
            time.sleep(min(3 * (i + 1), 20))
            continue
    return getattr(last, "code", -1), {"message": str(last)}


def get_sha(owner, repo, path, token, retries=8):
    """查询现有文件的 sha（更新时需要）。Gitee 限流时可能返回非 dict，
    做最健壮处理：重试直到拿到有效 sha；都不行则返回 None（视为新文件）。"""
    for i in range(retries):
        st, d = _req("GET",
                     f"{API}/repos/{owner}/{repo}/contents/{path}?access_token={token}",
                     token)
        if st == 200 and isinstance(d, dict) and d.get("sha"):
            return d["sha"]
        time.sleep(min(2 * (i + 1), 16))  # 退避 2/4/6/8/10/12/14/16s
    return None


def push_file(owner, repo, path, content, token, message, max_tries=8):
    """推送单个文件：带 sha 重试，覆盖 Gitee 限流导致的两类失败：
      - PUT 报 Blob SHA 不匹配 -> 重新取 sha 再 PUT；
      - 无 sha 时 POST 报"文件已存在/新建失败" -> 取 sha 改走 PUT。
    """
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
        low = msg.lower()
        # 需要 sha（文件已存在但我们没拿到 / sha 过期）-> 重新取 sha 再试
        if ("blob sha" in low or "新建失败" in msg or "already exists" in low
                or "sha" in low):
            sha = get_sha(owner, repo, path, token)
            time.sleep(2)
            continue
        # 其它错误直接抛出
        raise RuntimeError(f"推送 {path} 失败 HTTP {st}: {msg[:200]}")

    raise RuntimeError(f"推送 {path} 失败：重试 {max_tries} 次仍失败")


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
