"""视觉规范验收探针：用真实浏览器读取 computed style，检查设计系统的硬指标。

与 `ui_kit_check.py`（查交互行为）、`screenshot_ui.py`（出截图 + DOM 标记）互补：
本脚本查**视觉规范本身**是否达标——对比度、原文排版、横向溢出、强调色数量。

用法（先启动服务 python run.py）：
    python scripts/style_probe.py                 # 全站三档宽度 + 浅色
    python scripts/style_probe.py --dark          # 深色主题
    python scripts/style_probe.py /knowledge      # 只查指定页面
    python scripts/style_probe.py --width 390     # 只查一档宽度

退出码：0 = 全部达标；1 = 有指标不达标（可直接用于回归）。

验收标准（见 docs/14-frontend-design-plan.md §7）：
  · 文字对比度：全站 0 处低于 4.5:1
  · 原文排版：字号 ≥15.5px、行高 ≥1.85、行长 ≤40 全角字
  · 响应式：任何档位下横向溢出 = 0
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8000"
OUT = pathlib.Path("screenshots")
PROFILE = (OUT / ".probe-profile").resolve()
PROBE_PAGE = "/static/style-probe.html"

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

WIDTHS = (390, 768, 1440)
MIN_READ_FONT = 15.5
MIN_READ_LH = 1.85
MAX_READ_CHARS = 40.0


def find_browser() -> str:
    for path in CHROME_CANDIDATES:
        if pathlib.Path(path).exists():
            return path
    raise SystemExit("未找到 Chrome/Edge，可执行文件缺失")


def fetch(path: str) -> str:
    with urllib.request.urlopen(BASE + path, timeout=20) as r:
        return r.read().decode("utf-8")


def first_id(html: str, marker: str) -> str:
    i = html.find(marker)
    if i < 0:
        return ""
    return html[i + len(marker):i + len(marker) + 20].split('"')[0].split("?")[0].split("/")[0]


def api_post(path: str, payload: dict) -> None:
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=20).read()


def probe(browser: str, page: str, width: int) -> dict:
    url = f"{BASE}{PROBE_PAGE}?page={urllib.parse.quote(page)}&w={width}"
    cmd = [browser, "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-crash-reporter",
           "--no-first-run", "--no-default-browser-check", "--disk-cache-size=1",
           f"--user-data-dir={PROFILE}", "--virtual-time-budget=9000", "--dump-dom", url]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=180)
    html = res.stdout.decode("utf-8", errors="ignore")
    m = re.search(r'<pre id="out">(.*?)</pre>', html, re.S)
    if not m:
        return {"page": page, "width": width, "error": "探针没有输出（页面加载失败？）"}
    text = m.group(1)
    for a, b in (("&quot;", '"'), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&#39;", "'")):
        text = text.replace(a, b)
    try:
        return json.loads(text)
    except Exception as exc:  # noqa: BLE001
        return {"page": page, "width": width, "error": f"JSON 解析失败：{exc}"}


def collect_pages() -> list[str]:
    home = fetch("/")
    iid = first_id(home, "/interviews/")
    qid = ""
    if iid:
        d = json.loads(fetch(f"/api/interviews/{iid}/data"))
        qid = d["qas"][0]["id"] if d["qas"] else ""
    kb = fetch("/knowledge")
    eid = first_id(kb, "/knowledge/compare/")
    return [p for p in ["/", "/import", "/knowledge", "/mock", "/settings",
                        f"/interviews/{iid}", f"/interviews/{iid}/qa/{qid}",
                        f"/knowledge/compare/{eid}"] if p and not p.endswith("/None")]


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dark = "--dark" in sys.argv
    widths = WIDTHS
    if "--width" in sys.argv:
        widths = (int(sys.argv[sys.argv.index("--width") + 1]),)

    browser = find_browser()
    api_post("/api/settings", {"theme": "dark" if dark else "light"})
    pages = args or collect_pages()
    theme = "深色" if dark else "浅色"

    print(f"主题：{theme}　宽度：{widths}　页面：{len(pages)}")
    print("=" * 78)
    failures: list[str] = []
    total_aa = 0
    read_blocks: list[tuple[str, dict]] = []
    for page in pages:
        for w in widths:
            r = probe(browser, page, w)
            if "error" in r:
                failures.append(f"{page} @{w}px: {r['error']}")
                print(f"  ✗ {page} @{w}px  {r['error']}")
                continue
            total_aa += r["below_aa_count"]
            if w == 1440:
                for rb in r["reading_blocks"]:
                    read_blocks.append((page, rb))
            tag = f"{page} @{w}px"
            flags = []
            if r["below_aa_count"]:
                flags.append(f"对比度低于 AA {r['below_aa_count']} 处")
            if r["overflow_px"]:
                flags.append(f"横向溢出 {r['overflow_px']}px")
            for rb in r["reading_blocks"]:
                if rb["font_px"] < MIN_READ_FONT:
                    flags.append(f"原文 {rb['font_px']}px < {MIN_READ_FONT}px")
                if rb["line_height"] < MIN_READ_LH:
                    flags.append(f"原文行高 {rb['line_height']} < {MIN_READ_LH}")
                if rb["chars_per_line"] > MAX_READ_CHARS:
                    flags.append(f"原文行长 {rb['chars_per_line']} 字 > {MAX_READ_CHARS}")
            if flags:
                failures.append(f"{tag}: " + "；".join(sorted(set(flags))))
                print(f"  ✗ {tag}  " + "；".join(sorted(set(flags))))
                for b in r["below_aa"]:
                    print(f"        {b['cr']}:1  {b['size']}px  {b['cls']:<26} {b['text']}")
                for o in r["overflow_offenders"]:
                    print(f"        溢出 {o['by']}px  {o['tag']}.{o['cls']} w={o['width']} minW={o['minWidth']}")
            else:
                print(f"  ✓ {tag}  文本 {r['text_elements']} 个，全部 ≥4.5:1，无横向溢出")

    # 原文排版单独打印一份，便于人工核对
    print()
    print("原文排版（宋体阅读块，1440px 下实测）：")
    if not read_blocks:
        failures.append("没有测到任何宋体原文块——检查 .verbatim/--serif 是否还在")
        print("  ✗ 没有测到任何宋体原文块")
    for page, rb in read_blocks[:12]:
        ok = (rb["font_px"] >= MIN_READ_FONT and rb["line_height"] >= MIN_READ_LH
              and rb["chars_per_line"] <= MAX_READ_CHARS)
        print(f"  {'✓' if ok else '✗'} {page:<40} {rb['font_px']}px / 行高 {rb['line_height']}"
              f" / 行长 {rb['chars_per_line']} 全角字   {rb['text']}")
    print()
    print("=" * 78)
    if failures:
        print(f"未达标 {len(failures)} 项：")
        for f in failures:
            print("  -", f)
        return 1
    print(f"全部达标：{len(pages)} 页 × {len(widths)} 档宽度，低于 AA 合计 {total_aa} 处，"
          f"横向溢出 0；宋体原文块 {len(read_blocks)} 处全部满足 "
          f"字号 ≥{MIN_READ_FONT}px、行高 ≥{MIN_READ_LH}、行长 ≤{MAX_READ_CHARS} 全角字。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
