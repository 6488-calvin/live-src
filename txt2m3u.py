# -*- coding: utf-8 -*-
"""将 DIYP txt 直播源转换为 M3U 播放列表（电脑播放器测试用）

txt 格式:  分组,#genre# / 名称,url
m3u 格式:  #EXTM3U / #EXTINF:-1 group-title="分组",名称 / url

用法: python txt2m3u.py [输入txt] [输出m3u]
"""
import os
import sys


def txt2m3u(txt_path: str, m3u_path: str) -> int:
    group = None
    lines = ["#EXTM3U"]
    count = 0
    with open(txt_path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.endswith("#genre#"):
                group = line.rsplit(",", 1)[0].strip()
                continue
            parts = line.split(",", 1)
            if len(parts) != 2:
                continue
            name, url = parts[0].strip(), parts[1].strip()
            if url.startswith(("http://", "https://", "rtmp://", "rtsp://")):
                lines.append(f'#EXTINF:-1 group-title="{group or ""}",{name}')
                lines.append(url)
                count += 1
    with open(m3u_path, "w", encoding="utf-8-sig") as f:
        f.write("\n".join(lines) + "\n")
    return count


if __name__ == "__main__":
    base = os.path.dirname(os.path.abspath(__file__))
    txt = sys.argv[1] if len(sys.argv) > 1 else os.path.join(base, "live_simulate.txt")
    m3u = sys.argv[2] if len(sys.argv) > 2 else os.path.splitext(txt)[0] + ".m3u8"
    n = txt2m3u(txt, m3u)
    print(f"已生成: {m3u}")
    print(f"共 {n} 条直播源")
