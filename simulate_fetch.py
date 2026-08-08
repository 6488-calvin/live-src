# -*- coding: utf-8 -*-
"""模拟测试：从 foodieguide (tonkiang 备用入口) 扒取直播源，生成 DIYP txt

需求（按用户要求）：
- 频道：翡翠台 / 无线新闻台 / 广州综合 / now新闻台（4 个主菜单）
- 仅取最近三个月内（>= 2026-05-08）的源
- 分辨率策略：翡翠台/无线新闻台/now新闻台 仅 1920x1080；广州综合放宽到 720p+ 或未标注
- 排除：组播源、华丽翡翠台、TVB Jade、4K
- 频道内按日期倒序，条目以日期(MMdd)命名，如 0808
- 不遗漏简体/繁体频道名（不做名称白名单过滤）

用法: python simulate_fetch.py [max_pages]
"""
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, timedelta

from bs4 import BeautifulSoup

BASE = "http://www.foodieguide.com/iptvsearch/"
PROFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fg_profile")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "live.txt")
# 动态最近三个月（滚动窗口，云端每天跑不会过期）
_cutoff = date.today() - timedelta(days=90)
CUTOFF = (_cutoff.year, _cutoff.month, _cutoff.day)

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
]


@dataclass
class Item:
    name: str
    url: str
    date: tuple
    res: tuple
    info: str = ""


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


def fetch_all(max_pages: int):
    """启动一次浏览器，跑完所有频道关键词，返回 {频道名: [item...]}"""
    from playwright.sync_api import sync_playwright
    collected = {ch["name"]: [] for ch in CHANNELS}
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=PROFILE, headless=True, locale="zh-CN",
            viewport={"width": 1366, "height": 900}, user_agent=UA,
            args=[
"--disable-blink-features=AutomationControlled",
"--no-sandbox", # Actions root 用户必需
"--disable-dev-shm-usage", # /dev/shm 太小会段错误
"--disable-gpu", # headless 不需要 GPU
"--single-process", # 进一步降低崩溃概率
])
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page = ctx.new_page()
        page.set_default_timeout(20000)
        for ch in CHANNELS:
            ch_items = []
            for kw in ch["keywords"]:
                print(f"[抓取] {ch['name']} <- 关键词: {kw}")
                try:
                    page.goto(BASE, timeout=45000, wait_until="domcontentloaded")
                except Exception as e:
                    print("  [warn] 打开首页异常:", e)
                page.wait_for_timeout(12000)  # 搜索功能需约10s才就绪，太短会退化为分享页
                box = _find_box(page)
                if box is None:
                    print("  [warn] 找不到搜索框")
                    continue
                try:
                    box.fill(kw)
                except Exception:
                    box.type(kw)
                page.keyboard.press("Enter")
                page.wait_for_timeout(5000)
                html = page.content()
                print(f"  [debug] URL={page.url} 含resultplus={html.count('resultplus')} "
                      f"含About={html.count('About')} 含Serverbusy={html.count('Server busy')}")
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
                            h2 = page.content()
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


def build_txt(channel_results):
    lines = ["# 模拟测试 - foodieguide(tonkiang) 扒取",
             "# 更新时间: " + time.strftime("%Y-%m-%d %H:%M:%S")]
    for ch in CHANNELS:
        name = ch["name"]
        items = channel_results.get(name, [])
        lines.append(f"{name},#genre#")
        if not items:
            lines.append(f"# 无符合条件的直播源")
        for it in items:
            label = f"{it.date[1]:02d}{it.date[2]:02d}"
            lines.append(f"{label},{it.url}")
    return "\n".join(lines) + "\n"


def main():
    max_pages = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    print("=" * 56)
    print("模拟测试：foodieguide (tonkiang 备用入口) 扒源")
    print("条件: 最近三个月(>=2026-05-08) | 翡翠台/无线/now=1080p, 广州综合=720p+或未标注")
    print("排除: 组播/华丽翡翠台/TVB Jade")
    print(f"分页抓取: 最多 {max_pages} 页")
    print("=" * 56)
    collected = fetch_all(max_pages)
    result = {}
    for ch in CHANNELS:
        raw = collected[ch["name"]]
        print("-" * 56)
        print(f"[{ch['name']}] 抓取到 {len(raw)} 条原始结果")
        good = process(ch, raw)
        result[ch["name"]] = good
        print(f"  -> 符合条件 {len(good)} 条")
        for it in good[:8]:
            res_str = f"{it.res[0]}x{it.res[1]}" if it.res else "未标注"
            print(f"     {it.date[1]:02d}{it.date[2]:02d} {it.name} "
                  f"{res_str} {it.url[:70]}")
    content = build_txt(result)
    with open(OUT, "w", encoding="utf-8-sig") as f:
        f.write(content)
    print("=" * 56)
    print("已生成:", OUT)
    for ch in CHANNELS:
        print(f"  {ch['name']}: {len(result[ch['name']])} 条")
    print("=" * 56)


if __name__ == "__main__":
    main()
