# -*- coding: utf-8 -*-
"""R-23：把 Google Fonts 的 latin 子集下载到 static/fonts/，生成本地 fonts.css。

策略：只自托管 latin / latin-ext 分片（数字、拉丁字母、标点——UI 里最吃字形的部分），
中文使用系统字体回退（Windows 宋体/黑体、macOS Songti/PingFang），
从而在保持版式观感的同时让页面完全离线可用。
"""
import re
from pathlib import Path
from urllib.request import Request, urlopen

BASE = Path(__file__).resolve().parent / "agenttrace" / "static"
RAW = Path(__file__).resolve().parent / "agenttrace" / "_gf_raw.css"
FONT_DIR = BASE / "fonts"
FONT_DIR.mkdir(parents=True, exist_ok=True)

css = RAW.read_text(encoding="utf-8")

# 切分 @font-face 块，保留 latin / latin-ext（不保中文分片）
blocks = re.findall(r"/\*\s*([a-z0-9\-]+)\s*\*/\s*(@font-face\s*\{[^}]+\})", css)
kept = []
for subset, block in blocks:
    if subset not in ("latin", "latin-ext"):
        continue
    kept.append((subset, block))

print(f"font-face blocks: total={len(blocks)}, kept(latin)={len(kept)}")

out = ["/* R-23 自托管字体（latin 子集）——中文回退系统字体，页面完全离线可用 */", ""]
downloaded = 0
seen: dict[str, str] = {}
for subset, block in kept:
    m_url = re.search(r"url\((https://[^)]+\.woff2)\)", block)
    if not m_url:
        continue
    url = m_url.group(1)
    if url in seen:
        local_name = seen[url]
    else:
        fam = re.search(r"font-family:\s*'([^']+)'", block).group(1)
        weight = re.search(r"font-weight:\s*(\d+)", block).group(1)
        local_name = f"{fam.replace(' ', '')}-{weight}-{subset}.woff2"
        dest = FONT_DIR / local_name
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(req, timeout=30) as r:
                dest.write_bytes(r.read())
            downloaded += 1
            seen[url] = local_name
        except Exception as e:  # 单个分片失败不阻断，回退系统字体
            print(f"  ! 下载失败 {local_name}: {e}")
            continue
    out.append(block.replace(url, f"/static/fonts/{local_name}"))

(BASE / "fonts.css").write_text("\n".join(out) + "\n", encoding="utf-8")
print(f"downloaded {downloaded} woff2 files -> fonts/")
print("fonts dir size:", sum(p.stat().st_size for p in FONT_DIR.glob('*.woff2')) // 1024, "KB")
