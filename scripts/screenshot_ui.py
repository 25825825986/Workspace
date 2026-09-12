"""界面自查脚本：用系统 Chrome/Edge headless 截取关键页面。

为什么不用 Playwright：本机沙箱禁止子进程管道（EPERM），Playwright 启动浏览器会失败；
Chrome 的 `--headless --screenshot` 只需继承式 stdio，可直接运行。

用法（先启动服务 python run.py）：
    python scripts/screenshot_ui.py             # 浅色，桌面
    python scripts/screenshot_ui.py --dark      # 深色
    python scripts/screenshot_ui.py --mobile    # 移动端宽度
输出：screenshots/<序号_名称>[_dark|_mobile].png
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import urllib.request

BASE = "http://127.0.0.1:8000"
OUT = pathlib.Path("screenshots")

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def find_browser() -> str:
    for path in CHROME_CANDIDATES:
        if pathlib.Path(path).exists():
            return path
    raise SystemExit("未找到 Chrome/Edge，可执行文件缺失")


def api_post(path: str, payload: dict) -> dict:
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch(path: str) -> str:
    with urllib.request.urlopen(BASE + path, timeout=20) as r:
        return r.read().decode("utf-8")


def first_id(html: str, marker: str) -> str:
    idx = html.find(marker)
    if idx < 0:
        return ""
    return html[idx + len(marker):idx + len(marker) + 20].split('"')[0].split("?")[0].split("/")[0]


def shoot(browser: str, url: str, out: pathlib.Path, width: int, height: int) -> None:
    profile = (OUT / ".profile").resolve()
    cmd = [browser, "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-crash-reporter",
           "--hide-scrollbars", "--no-first-run", "--no-default-browser-check",
           f"--user-data-dir={profile}", f"--window-size={width},{height}",
           f"--screenshot={out.resolve()}", url]
    with open(OUT / "_chrome.log", "a", encoding="utf-8") as log:
        log.write(" ".join(cmd) + "\n")
        subprocess.run(cmd, stdout=log, stderr=log, timeout=120)
    if not out.exists():
        raise RuntimeError(f"Chrome 未生成截图（见 {OUT / '_chrome.log'}）")


def dom(browser: str, url: str) -> str:
    profile = (OUT / ".profile").resolve()
    cmd = [browser, "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run",
           "--disable-crash-reporter", f"--user-data-dir={profile}", "--dump-dom", url]
    with open(OUT / "_chrome.log", "a", encoding="utf-8") as log:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=log, timeout=120)
    return res.stdout.decode("utf-8", errors="ignore")


def main() -> int:
    dark = "--dark" in sys.argv
    mobile = "--mobile" in sys.argv
    api_post("/api/settings", {"theme": "dark" if dark else "light"})
    OUT.mkdir(exist_ok=True)
    browser = find_browser()
    print("browser:", browser)

    home = fetch("/")
    iid = first_id(home, "/interviews/")
    qid = ""
    if iid:
        data = json.loads(fetch(f"/api/interviews/{iid}/data"))
        qid = data["qas"][0]["id"] if data["qas"] else ""
    knowledge = fetch("/knowledge")
    eid = first_id(knowledge, "/knowledge/compare/")
    mock_html = fetch("/mock")
    sid = first_id(mock_html[mock_html.find("历史模拟"):] if "历史模拟" in mock_html else mock_html, "/mock/")

    pages = [
        ("01_records", "/"),
        ("02_import", "/import"),
        ("03_interview", f"/interviews/{iid}"),
        ("04_single_qa", f"/interviews/{iid}/qa/{qid}"),
        ("05_knowledge", "/knowledge"),
        ("06_compare", f"/knowledge/compare/{eid}"),
        ("07_mock_setup", "/mock"),
        ("08_mock_run", f"/mock/{sid}"),
        ("09_mock_report", f"/mock/{sid}/report"),
        ("10_settings", "/settings"),
    ]
    suffix = ("_dark" if dark else "") + ("_mobile" if mobile else "")
    width, height = (390, 3600) if mobile else (1440, 3400)
    for name, path in pages:
        target = OUT / f"{name}{suffix}.png"
        try:
            shoot(browser, BASE + path, target, width, height)
            print(f"shot: {target}  ({target.stat().st_size // 1024} KB)")
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {name}: {exc}")

    # DOM 级校验：确认页面渲染出关键元素（服务端 + 前端都在）
    checks = [
        ("/", ["面试记录", 'name="q"', "导入面试", "搜索 ⌘K"]),
        ("/import", ["面试地点", "期望薪资", "btn-title", "载入示例文本", "重新识别"]),
        (f"/interviews/{iid}", ["隐藏答案", "快速编辑", "answer-mask", "加入知识库", "更多"]),
        ("/knowledge", ["更新相似关联", "重新归类", "自我介绍", "反问环节", "导出预览"]),
        (f"/knowledge/compare/{eid}", ["历史作答对比", "相似问题关联", "标为最佳作答"]),
        ("/mock", ["题库随机", "整场面试复盘", "简历 + 题库 + AI", "开始模拟面试", "语音播报"]),
        (f"/mock/{sid}", ["播报问题", "我的回答", "结束并生成报告", "参考要点", "save-state"]),
        ("/settings", ["主题外观", "AI 模式", "模型 API", "数据信息", "测试连接"]),
    ]
    bad = 0
    for path, markers in checks:
        html = dom(browser, BASE + path)
        missing = [m for m in markers if m not in html]
        if missing:
            bad += 1
            print(f"DOM MISSING {path}: {missing}")
        else:
            print(f"DOM OK {path}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
