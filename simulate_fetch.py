# -*- coding: utf-8 -*-
"""模拟测试：从 foodieguide (tonkiang 备用入口) 扒取直播源，生成 DIYP txt

需求（按用户要求）：
- 频道：翡翠台 / 无线新闻台 / 广州综合 / now新闻台 / 湖南卫视（5 个主菜单）
- 仅取最近三个月内（>= 2026-05-08）的源（仅作用于 tonkiang 主源）
- 分辨率策略：翡翠台/无线新闻台/now新闻台 仅 1920x1080；广州综合放宽到 720p+ 或未标注
- 排除：组播源、华丽翡翠台、TVB Jade、4K
- 频道内按日期倒序，条目以日期(MMdd)命名，如 0808
- 不遗漏简体/繁体频道名（不做名称白名单过滤

== 2026-08-22 黑名单机制 ==
- 新增 BLACKLIST（黑名单）：凡 URL 含 "catvod" 的源永久不收录（CatVOD 类是「网页 JS 播放器」
  包装，DIYP 纯 m3u8 客户端直连只回 CatVOD 主页无法播放，与 yes2049 同类 webplayer 包装；
  用户电视盒实测反复踩坑）。关键词匹配所有途径（tonkiang / GitHub 备源 / 白名单），
  在 process() 早过滤 + main() 末尾兜底两层生效。后续若发现其它同类包装器（如某新站
  域名），往 BLACKLIST 列表加关键词即可全网永久屏蔽。
== 2026-08-22 修正 ==
- 误删还原：最初把「用户要删的坏源」错当成「白名单 cdn3.indevs.in 那条」删了。
  真正坏的是 tonkiang 抓的 live.catvod.com/iptv.php?id=fctsub（DIYP 直连回 CatVOD 主页，
  与 yes2049 同类 webplayer 包装），那条已从 live.txt 删除；白名单 cdn3.indevs.in 经用户
  实测可用，已还原保留。
- 广州综合白名单（tencentplaygzrb01.gztv.com 官方 https 源）用户实测电视盒仍播不了，已删除；
  广州综合退回纯 tonkiang 源（用户此前反馈广州综合秒播即 tonkiang 源，不受影响）。
== 2026-08-21 增强 ==
- 白名单源（WHITELIST）：用户指定长期保留、不参与三个月日期过滤、置顶首选、对同 url 去重。
  当前仅含：翡翠台 https://cdn3.indevs.in/stream/tvb/fct/（用户实测可用，改名 白名单-翡翠台）。
- 翡翠台：从 GitHub 聚合源（Kimentanm / epg.pw 等）提取「翡翠台 / TVB Jade」备源并入，
  不受三个月日期过滤影响；并对翡翠台全部候选源做「直连连通性测试」（绕过系统代理，
  模拟电视盒无代理环境），活源排前、死源排后（保留不剔除）
- 无线新闻台 / now新闻台：维持原逻辑不变

用法: python simulate_fetch.py [max_pages]
"""
import concurrent.futures as cf
import os
import re
import sys
import time
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

BASE = "http://www.foodieguide.com/iptvsearch/"
PROFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fg_profile")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "live.txt")
CUTOFF = (2026, 5, 8)  # 最近三个月（含 5 月 8 日之后）

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

CHANNELS = [
    {"name": "翡翠台", "keywords": ["翡翠台"],
     "exclude": ["华丽", "tvb jade"], "res_rule": "1080p"},
    {"name": "无线新闻台", "keywords": ["无线新闻台"],
     "res_rule": "1080p"},
    {"name": "广州综合", "keywords": ["广州综合"],
     "res_rule": "720p+"},
    {"name": "now新闻台", "keywords": ["now新闻台", "now新闻"],
     "res_rule": "1080p"},
    {"name": "湖南卫视", "keywords": ["湖南卫视"],
     "exclude": ["国际"], "res_rule": "1080p"},
]

# ===== 2026-08-21 新增：白名单源（固定保留，不参与三个月日期过滤）=====
# 用户指定长期保留的源：改名 + 置顶首选 + 对同 url 的 tonkiang 源去重（避免重复）。
# 广州综合用广州台官方 https 无签名源（电视盒 DIYP 兼容 https；无 txSecret 永不过期）；
#   之前 http 版电视盒播不了，已改为 https 版。
WHITELIST = {
    # 翡翠台用户实测可用的直链，固定置顶首选、不参与三个月日期过滤。
    "翡翠台": [
        ("白名单-翡翠台", "https://cdn3.indevs.in/stream/tvb/fct/"),
    ],
}

# ===== 2026-08-22 新增：黑名单（永久不收录）=====
# CatVOD 类属于「网页 JS 播放器」包装：DIYP 纯 m3u8 客户端直连只回 CatVOD 主页，
# 无法播放（与 yes2049 同类 webplayer 包装）。用户电视盒实测反复踩坑，故永久拉黑。
# 关键词匹配 URL（不区分大小写），凡 URL 含以下任一子串的源一律剔除（所有途径：
# tonkiang / GitHub 备源 / 白名单 都适用）。后续发现其它同类包装器，往 BLACKLIST 加关键词即可。
BLACKLIST = [
    "catvod",
]

def is_blacklisted(url: str) -> bool:
    u = (url or "").lower()
    return any(k.lower() in u for k in BLACKLIST)

# 翡翠台 GitHub/聚合备源：每个源给若干候选 raw 地址，按顺序尝试，命中第一个可用即解析。
# 匹配频道名含「翡翠 / jade」的条目（排除「华丽」）。无日期、不受三个月过滤。
JADE_GITHUB_SOURCES = [
    {"label": "Kimentanm",
     "urls": ["https://raw.githubusercontent.com/Kimentanm/aptv/master/m3u/iptv.m3u"]},
    {"label": "epg.pw",
     "urls": ["https://epg.pw/test_channels_hong_kong.m3u"]},
    {"label": "YueChan",
     "urls": ["https://raw.githubusercontent.com/YueChan/Live/main/IPTV.m3u"]},
    {"label": "Guovin",
     "urls": ["https://ghproxy.com/https://raw.githubusercontent.com/Guovin/iptv-api/results/result_m3u_ipv4.m3u"]},
]
JADE_KEYWORDS = [r"翡翠", r"jade", r"tvb\s*jade", r"無綫翡翠", r"无线翡翠"]
JADE_EXCLUDE = ["华丽"]


@dataclass
class Item:
    name: str
    url: str
    date: tuple
    res: tuple
    info: str = ""


@dataclass
class JadeEntry:
    label: str
    url: str
    kind: str        # 'tk'=tonkiang主源, 'gh'=GitHub备源
    date: tuple      # tonkiang 才有日期，github 为 None


def parse_date(info: str):
    """tonkiang 日期主格式为 MM-DD-YYYY，也有 YYYY-MM-DD；带合法性校验"""
    m = re.search(r"(\d{2})-(\d{2})-(\d{4})", info)
    if m:
        mon, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mon <= 12 and 1 <= day <= 31:
            return (year, mon, day)
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", info)
    if m:
        year, mon, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mon <= 12 and 1 <= day <= 31:
            return (year, mon, day)
    return None


def parse_res(info: str):
    m = re.search(r"(\d{3,4})\s*[xX\u00d7*]\s*(\d{3,4})", info)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    if re.search(r"1080p", info, re.I):
        return (1920, 1080)
    return None


def is_multicast(url: str, info: str, name: str) -> bool:
    if re.search(r"udp://|rtp://", url, re.I):
        return True
    if "组播" in info or "组播" in name:
        return True
    return False


def parse_html(html: str, keyword: str):
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for rp in soup.find_all("div", class_="resultplus"):
        name = ""
        for tip in rp.select("div.channel div.tip"):
            t = tip.get_text(strip=True)
            if t:
                name = t.replace("\ufeff", "").strip()
                break
        info = " ".join(d.get_text(" ", strip=True) for d in rp.find_all(
            "div", style=re.compile(r"font-size:\s*10px")))
        urls = [t.get_text(strip=True) for t in rp.find_all("tba")]
        urls = [u for u in urls if u.startswith(("http://", "https://", "rtmp://"))]
        for u in dict.fromkeys(urls):
            items.append({"name": name, "url": u, "info": info,
                          "keyword": keyword})
    return items, soup


def _find_box(page):
    for sel in ["input#search", "input[type='text']", "form input"]:
        try:
            loc = page.locator(sel).first
            if loc.count():
                return loc
        except Exception:
            continue
    return None


def _get_content_robust(page, retries=8, wait_ms=3000):
    """page.content() 在页面导航期间会抛 'navigating' 错误或返回空文档；带重试兜底等待导航结束。"""
    last = None
    for _ in range(retries):
        try:
            html = page.content()
        except Exception as e:
            last = e
            msg = str(e).lower()
            if "navigat" in msg or "content" in msg:
                page.wait_for_timeout(wait_ms)
                continue
            raise
        if len(html.strip()) < 500:
            page.wait_for_timeout(wait_ms)
            continue
        return html
    raise RuntimeError(f"多次尝试仍无法获取页面内容: {last}")


def _search_keyword(page, kw, attempts=4):
    """对单个关键词执行一次搜索并返回 HTML。

    站点偶发两种异常：①导航竞态（content 抛错/空文档，由 _get_content_robust 兜底）；
    ②搜索未真正执行、退化成分享页（resultplus=0 且 About=0）。此处对②做整轮重试。
    """
    html = ""
    for a in range(1, attempts + 1):
        try:
            page.goto(BASE, timeout=45000, wait_until="domcontentloaded")
        except Exception as e:
            print("  [warn] 打开首页异常:", e)
        page.wait_for_timeout(12000)  # 搜索功能需约10s才就绪，太短会退化为分享页
        box = _find_box(page)
        if box is None:
            print("  [warn] 找不到搜索框")
            return None
        try:
            box.fill(kw)
        except Exception:
            box.type(kw)
        page.keyboard.press("Enter")
        page.wait_for_timeout(5000)
        try:
            page.wait_for_load_state("load", timeout=15000)
        except Exception:
            pass
        html = _get_content_robust(page)
        rp = html.count("resultplus")
        about = html.count("About")
        print(f"  [debug][第{a}次] resultplus={rp} About={about} "
              f"Serverbusy={html.count('Server busy')}")
        if rp > 0 or about > 0:
            return html
        print(f"  [warn] 第{a}次搜索未取到结果页，重试...")
    return html  # 多次仍空则返回最后一次，交由上层按 0 条处理


def fetch_all(max_pages: int):
    """启动一次浏览器，跑完所有频道关键词，返回 {频道名: [item...]}"""
    from playwright.sync_api import sync_playwright
    collected = {ch["name"]: [] for ch in CHANNELS}
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=PROFILE, headless=True, locale="zh-CN",
            viewport={"width": 1366, "height": 900}, user_agent=UA,
            args=["--disable-blink-features=AutomationControlled"])
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page = ctx.new_page()
        page.set_default_timeout(20000)
        for ch in CHANNELS:
            ch_items = []
            for kw in ch["keywords"]:
                print(f"[抓取] {ch['name']} <- 关键词: {kw}")
                html = _search_keyword(page, kw)
                if html is None:
                    continue
                ddir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "debug_fg")
                os.makedirs(ddir, exist_ok=True)
                with open(os.path.join(ddir, f"{ch['name']}_{kw}_p1.html"),
                          "w", encoding="utf-8") as f:
                    f.write(html)
                items, _ = parse_html(html, kw)
                print(f"  第一页 {len(items)} 条")
                ch_items += items

                if max_pages > 1:
                    links = re.findall(r'href="(\?page=\d+&[^"]*)"', html)
                    seen = set()
                    for href in links:
                        m = re.search(r"page=(\d+)", href)
                        if not m:
                            continue
                        n = int(m.group(1))
                        if n in seen or n > max_pages or n <= 1:
                            continue
                        seen.add(n)
                        try:
                            page.goto(BASE + href.replace("&amp;", "&"),
                                      timeout=30000,
                                      wait_until="domcontentloaded")
                            page.wait_for_timeout(3000)
                            h2 = _get_content_robust(page)
                            it2, _ = parse_html(h2, kw)
                            ch_items += it2
                            print(f"  第{n}页 {len(it2)} 条")
                        except Exception as e:
                            print(f"  [warn] 第{n}页失败: {e}")
            collected[ch["name"]] = ch_items
        ctx.close()
    return collected


def process(channel_cfg, items):
    out = []
    excl = channel_cfg.get("exclude", [])
    rule = channel_cfg.get("res_rule", "1080p")  # 1080p=仅1920x1080 | 720p+=720p及以上或未标注
    for it in items:
        name = it["name"]
        if any(e.lower() in name.lower() for e in excl):
            continue
        if is_multicast(it["url"], it["info"], name):
            continue
        if is_blacklisted(it["url"]):
            continue
        d = parse_date(it["info"])
        if not d:
            print(f"  [跳过] 无日期: [{name}] {it['url'][:70]}")
            continue
        if d < CUTOFF:
            continue
        r = parse_res(it["info"])
        if rule == "1080p":
            if not r or r != (1920, 1080):
                if not r:
                    print(f"  [跳过] 无分辨率: [{name}] {it['url'][:70]}")
                continue
        elif rule == "720p+":
            if r is not None and r[1] < 720:
                continue  # 低于 720p 的排除；未标注(None)接受
        out.append(Item(name=name, url=it["url"], date=d, res=r,
                        info=it["info"]))
    # 跨关键词按 URL 去重
    seen = set()
    dedup = []
    for it in out:
        if it.url in seen:
            continue
        seen.add(it.url)
        dedup.append(it)
    dedup.sort(key=lambda x: (x.date, x.url), reverse=True)
    return dedup


# ============ 2026-08-21 新增：GitHub 备源提取 ============
def parse_m3u_text(text: str):
    """通用 m3u 解析：返回 [(频道名, url), ...]。兼容两种格式：
       #EXTINF:-1 ...,频道名 \\n url
       频道名,url
    """
    pairs = []
    name = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            m = re.search(r",([^,\n]*)$", line)  # 取最后一个逗号后的字段作为频道名
            name = m.group(1).strip() if m else None
            continue
        if line.startswith("#"):
            continue
        if re.match(r"https?://", line) or line.startswith(("rtmp://", "rtsp://")):
            pairs.append((name, line))
            name = None
        elif "," in line and re.match(r"^[^,]+,https?://", line):
            nm, u = line.split(",", 1)
            pairs.append((nm.strip(), u.strip()))
    return pairs


def _jade_match(name: str) -> bool:
    if not name:
        return False
    if any(e in name for e in JADE_EXCLUDE):
        return False
    return any(re.search(k, name, re.I) for k in JADE_KEYWORDS)


def fetch_github_jade():
    """从 JADE_GITHUB_SOURCES 提取翡翠台备源，返回 [(url, label, idx), ...]。

    每个源按顺序尝试候选地址，命中第一个可用的即解析；无翡翠台或抓取失败则贡献 0 条（优雅降级）。
    注意：此处用系统默认网络（用户 PC 上走代理可访问 GitHub），不做直连。
    """
    out = []
    seen = set()
    for src in JADE_GITHUB_SOURCES:
        got = False
        for u in src["urls"]:
            try:
                r = requests.get(u, timeout=25, headers={"User-Agent": UA})
                if not str(r.status_code).startswith("2"):
                    print(f"  [github][{src['label']}] HTTP {r.status_code}: {u}")
                    continue
                pairs = parse_m3u_text(r.text)
                cnt = 0
                for name, url in pairs:
                    if not _jade_match(name):
                        continue
                    if is_blacklisted(url):
                        continue
                    if url in seen:
                        continue
                    seen.add(url)
                    cnt += 1
                    out.append((url, src["label"], cnt))
                print(f"  [github][{src['label']}] 匹配翡翠台 {cnt} 条 (来源 {u.split('/')[-1]})")
                got = True
                break  # 该源第一个可用地址即可
            except Exception as e:
                print(f"  [github][{src['label']}] 抓取失败: {e}")
        if not got:
            print(f"  [github][{src['label']}] 所有候选地址均不可用，跳过")
    return out


# ============ 2026-08-21 新增：翡翠台直连连通性测试 ============
def _probe_one(session, url, timeout):
    """直连探测单条源（绕过系统代理，模拟电视盒环境）。返回 (url, status)。
       status: 'ok'=可连通且拿到数据；'dead'=明确不可达；'err'=探测异常（网络波动等）
    """
    try:
        r = session.get(url, timeout=timeout, stream=True)
        chunk = b""
        try:
            chunk = next(r.iter_content(2048), b"")
        finally:
            r.close()
        if r.status_code in (200, 206) and len(chunk) > 0:
            return url, "ok"
        return url, "dead"
    except Exception:
        return url, "err"


def connectivity_test(urls, timeout=7, workers=10):
    """对 url 列表做直连连通性测试（绕过代理）。返回 {url: 'ok'|'dead'|'err'}。

    排序策略：
      - 'ok'  视为活源，排前
      - 'dead' 视为死源，排后（保留不剔除，DIYP 可手动切）
      - 'err' 视为探测异常：若整体探测基本失效（真实结果占比过低），则全部按活源处理，
              避免把本可达的源误判后排。
    """
    if not urls:
        return {}
    session = requests.Session()
    session.trust_env = False           # 关键：忽略 HTTP_PROXY/HTTPS_PROXY 等环境变量
    session.proxies = {"http": None, "https": None}  # 关键：强制直连，不走系统代理
    session.headers.update({"User-Agent": UA})
    results = {}
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_probe_one, session, u, timeout): u for u in urls}
        for f in cf.as_completed(futs):
            u, st = f.result()
            results[u] = st
    # 探测可靠性自检：真实判定('ok'+'dead')过少说明测试本身不可靠（如本机断网）
    total = len(results)
    real = sum(1 for v in results.values() if v in ("ok", "dead"))
    if real < max(3, int(total * 0.2)):
        print(f"  [连通性测试] 有效判定仅 {real}/{total}，疑似测试环境异常，全部按活源处理")
        for u in results:
            if results[u] == "err":
                results[u] = "ok"
    ok_n = sum(1 for v in results.values() if v == "ok")
    dead_n = sum(1 for v in results.values() if v == "dead")
    print(f"  [连通性测试] 共 {total} 条，活 {ok_n} / 死 {dead_n} / 异常 {total-ok_n-dead_n}")
    return results


def _tk_label(it: Item) -> str:
    return f"{it.date[1]:02d}{it.date[2]:02d}{it.name}"


def build_jade_entries(tk_items, gh_items):
    """合并 tonkiang 翡翠台 + GitHub 备源，做连通性测试并排序。

    返回 [(label, url), ...]：活源在前（tonkiang 按日期倒序优先，其次 github），死源在后（保留原序）。
    """
    entries = []
    for it in tk_items:
        entries.append(JadeEntry(label=_tk_label(it), url=it.url, kind="tk", date=it.date))
    for url, slab, idx in gh_items:
        entries.append(JadeEntry(label=f"{slab}{idx}-翡翠台", url=url, kind="gh", date=None))

    results = connectivity_test([e.url for e in entries])

    def sort_key(e: JadeEntry):
        st = results.get(e.url, "ok")
        alive = (st == "ok")
        kind_rank = 0 if e.kind == "tk" else 1
        date_key = (e.date[0] * 10000 + e.date[1] * 100 + e.date[2]) if e.date else 0
        return (0 if alive else 1, kind_rank, -date_key)

    ordered = sorted(entries, key=sort_key)
    return [(e.label, e.url) for e in ordered]


def apply_whitelist(name, items):
    """白名单源置顶首选、固定保留（不参与日期过滤）；并对同 url 的 tonkiang 源去重。

    items: [(label, url), ...]（该频道已排好序的源）。
    返回: [(label, url), ...]（白名单在前，tonkiang/github 源在后，同 url 去重）。
    """
    wl = WHITELIST.get(name, [])
    wl_urls = {u for _, u in wl}
    kept = [(l, u) for l, u in items if u not in wl_urls]
    return wl + kept


def build_txt(channel_lines: dict):
    """channel_lines: {频道名: [(label, url), ...]}"""
    lines = ["# 模拟测试 - foodieguide(tonkiang) 扒取 + 官方/GitHub 备源",
             "# 更新时间: " + time.strftime("%Y-%m-%d %H:%M:%S")]
    for ch in CHANNELS:
        name = ch["name"]
        items = channel_lines.get(name, [])
        lines.append(f"{name},#genre#")
        if not items:
            lines.append(f"# 无符合条件的直播源")
        for label, url in items:
            lines.append(f"{label},{url}")
    return "\n".join(lines) + "\n"


def main():
    max_pages = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    print("=" * 56)
    print("模拟测试：foodieguide (tonkiang 备用入口) 扒源")
    print("条件: 最近三个月(>=2026-05-08) | 翡翠台/无线/now=1080p, 广州综合=720p+或未标注")
    print("排除: 组播/华丽翡翠台/TVB Jade")
    print("增强: 广州综合插入官方源 | 翡翠台并入GitHub备源+直连连通性测试")
    print(f"分页抓取: 最多 {max_pages} 页")
    print("=" * 56)
    cfg_by_name = {ch["name"]: ch for ch in CHANNELS}
    collected = fetch_all(max_pages)

    channel_lines = {}
    for ch in CHANNELS:
        name = ch["name"]
        raw = collected[name]
        print("-" * 56)
        print(f"[{name}] 抓取到 {len(raw)} 条原始结果")
        good = process(ch, raw)
        print(f"  -> tonkiang 符合条件 {len(good)} 条")

        if name == "翡翠台":
            gh = fetch_github_jade()
            print(f"  -> GitHub 备源 {len(gh)} 条")
            channel_lines[name] = build_jade_entries(good, gh)
            print(f"  -> 翡翠台最终 {len(channel_lines[name])} 条（已按连通性排序）")
        else:
            channel_lines[name] = [(_tk_label(it), it.url) for it in good]

        # 应用白名单：置顶首选 + 同 url 去重（固定保留、不参与三个月过滤）
        channel_lines[name] = apply_whitelist(name, channel_lines[name])
        # 黑名单过滤（永久不收录，如 CatVOD 网页包装器）——兜底覆盖所有途径
        before = len(channel_lines[name])
        channel_lines[name] = [(l, u) for (l, u) in channel_lines[name]
                               if not is_blacklisted(u)]
        removed = before - len(channel_lines[name])
        if removed:
            print(f"  -> 黑名单剔除 {removed} 条（catvod 等网页包装器）")

    content = build_txt(channel_lines)
    with open(OUT, "w", encoding="utf-8-sig") as f:
        f.write(content)
    print("=" * 56)
    print("已生成:", OUT)
    for ch in CHANNELS:
        print(f"  {ch['name']}: {len(channel_lines[ch['name']])} 条")
    print("=" * 56)


if __name__ == "__main__":
    main()
