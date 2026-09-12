"""UI Kit 回归检查：在真实浏览器里执行 `app/static/ui-kit-selftest.html` 并读取结果。

验证 Toast / 确认弹层（含 Esc、焦点、返回值）/ 进度条 / 按钮 loading / 抽屉 / ⌘K 命令面板。
用法（需先启动服务 python run.py）：
    python scripts/ui_kit_check.py
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]
URL = "http://127.0.0.1:8000/static/ui-kit-selftest.html"
OUT = pathlib.Path("screenshots")


def find_browser() -> str:
    for path in CHROME_CANDIDATES:
        if pathlib.Path(path).exists():
            return path
    raise SystemExit("未找到 Chrome/Edge")


def main() -> int:
    OUT.mkdir(exist_ok=True)
    profile = (OUT / ".uikit").resolve()
    cmd = [find_browser(), "--headless=new", "--disable-gpu", "--no-sandbox",
           "--disable-crash-reporter", f"--user-data-dir={profile}",
           "--virtual-time-budget=8000", "--dump-dom", URL]
    with open(OUT / "_uikit.log", "w", encoding="utf-8") as log:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=log, timeout=120)
    html = res.stdout.decode("utf-8", errors="ignore")
    if not html:
        print("EMPTY_DOM（浏览器未返回内容，见 screenshots/_uikit.log）")
        return 1
    block = re.search(r'<div id="results">(.*?)</div>', html, re.S)
    if not block:
        print("NO_RESULTS_BLOCK")
        return 1
    lines = [ln.strip() for ln in block.group(1).splitlines() if ln.strip()]
    for ln in lines:
        print(ln)
    failed = [ln for ln in lines if ln.startswith("FAIL")]
    print()
    print(f"UIKIT: 通过 {len(lines) - len(failed)} / {len(lines)}")
    print("UIKIT_OK" if not failed else "UIKIT_ISSUES")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
