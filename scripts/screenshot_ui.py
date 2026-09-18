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
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8000"
OUT = pathlib.Path("screenshots")

# Windows 上 Chrome headless 的窗口宽度有下限（实测 512px）：
# --window-size=390 时页面其实按 512px 排版，截图却裁成 390px，
# 会拍出「右侧被切掉」的假象，让人误判移动端布局坏了（实测真实横向溢出为 0）。
# 所以窄屏一律套一层固定宽度的 iframe 再截。
MIN_WINDOW_WIDTH = 512


def frame_url(url: str, width: int) -> str:
    """把目标页装进一个固定宽度的 iframe，绕开窗口最小宽度限制。

    注意：**不能用 data: URL**——Chrome 会拒绝在 data: 页面里加载 http 子框架，
    截出来是一张只有「加载失败」占位图的白图（而且不报错）。写成临时 .html
    再用 file:// 打开才正常。
    """
    doc = (
        '<!doctype html><html><head><meta charset="utf-8"><style>'
        'html,body{margin:0;padding:0;height:100%;background:#fff}'
        f'iframe{{display:block;width:{width}px;height:100vh;border:0;margin:0 auto}}'
        '</style></head><body>'
        f'<iframe src="{url}"></iframe></body></html>'
    )
    path = (OUT / f".frame-{width}.html").resolve()
    path.write_text(doc, encoding="utf-8")
    return path.as_uri()

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


def session_id(html: str) -> str:
    """取「历史模拟」表里的第一个会话 id。

    不能直接在整页里找 "/mock/"：页内脚本含 postJSON('/api/mock/sessions', …)，
    会被误当成会话链接（没有历史模拟时就会拼出 /mock/sessions', payload); 这种坏 URL）。
    """
    if "历史模拟" not in html:
        return ""
    section = html[html.find("历史模拟"):]
    match = re.search(r'href="/mock/([0-9a-zA-Z_-]+)"', section)
    return match.group(1) if match else ""


def shoot(browser: str, url: str, out: pathlib.Path, width: int, height: int) -> None:
    profile = (OUT / ".profile").resolve()
    # 窄屏（移动端）走 iframe 包装，避免被窗口最小宽度改写视口
    target = url if width >= MIN_WINDOW_WIDTH else frame_url(url, width)
    win_w = max(width, MIN_WINDOW_WIDTH)
    cmd = [browser, "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-crash-reporter",
           "--hide-scrollbars", "--no-first-run", "--no-default-browser-check",
           f"--user-data-dir={profile}", f"--window-size={win_w},{height}",
           # 卡片有 0.18s 入场淡入（.ui-rise）；不加虚拟时间预算会在动画中途截图，
           # 拍出半透明的"褪色"页面，导致像素审计误判对比度。
           "--virtual-time-budget=4000",
           f"--screenshot={out.resolve()}", target]
    with open(OUT / "_chrome.log", "a", encoding="utf-8") as log:
        log.write(" ".join(cmd) + "\n")
        subprocess.run(cmd, stdout=log, stderr=log, timeout=120)
    if not out.exists():
        raise RuntimeError(f"Chrome 未生成截图（见 {OUT / '_chrome.log'}）")
    # 空图保护：iframe/加载失败时 Chrome 会静默产出一张几 KB 的空白图冒充证据。
    # 正常页面动辄几十上百 KB，低于阈值一律当失败报出来。
    if out.stat().st_size < 8000:
        raise RuntimeError(
            f"截图疑似空白（{out.stat().st_size} B）：页面可能没渲染出来，见 {OUT / '_chrome.log'}")


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
    sid = session_id(mock_html)

    pages = [
        ("01_records", "/"),
        ("02_import", "/import"),
        ("03_interview", f"/interviews/{iid}"),
        ("04_single_qa", f"/interviews/{iid}/qa/{qid}"),
        ("05_knowledge", "/knowledge"),
        ("06_compare", f"/knowledge/compare/{eid}"),
        ("07_mock_setup", "/mock"),
        ("10_settings", "/settings"),
    ]
    if sid:
        pages += [("08_mock_run", f"/mock/{sid}"), ("09_mock_report", f"/mock/{sid}/report")]
    else:
        print("skip 08_mock_run / 09_mock_report：当前没有模拟会话"
              "（先跑 python scripts/dev_seed.py 或手动开始一轮模拟）")
    suffix = ("_dark" if dark else "") + ("_mobile" if mobile else "")
    width, height = (390, 3600) if mobile else (1440, 3400)
    for name, path in pages:
        target = OUT / f"{name}{suffix}.png"
        try:
            shoot(browser, BASE + path, target, width, height)
            print(f"shot: {target}  ({target.stat().st_size // 1024} KB)")
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {name}: {exc}")

    # DOM 级校验：确认页面渲染出关键元素（服务端 + 前端都在）。
    # 只断言稳定的页面结构/控件文案，不断言正文说明句——那类文案会随设计调整变动。
    kb_markers = ["更新相似关联", "重新归类", "导出预览", "下载快照"]
    # 专题标题（自我介绍 / 个人发展 / 反问环节）只有沉淀过对应条目才存在，数据为空时不强求
    kb_markers += [t for t in ("自我介绍", "反问环节") if t in knowledge]
    checks = [
        ("/", ["面试记录", 'name="q"', "导入面试", "搜索 ⌘K"]),
        ("/import", ["面试地点", "期望薪资", "btn-title", "载入示例文本", "重新识别",
                     "支持哪些说话人标签格式？"]),
        (f"/interviews/{iid}", ["隐藏答案", "快速编辑", "answer-mask", "加入知识库", "更多"]),
        ("/knowledge", kb_markers),
        (f"/knowledge/compare/{eid}", ["历史作答对比", "相似问题关联", "标为最佳作答"]),
        ("/mock", ["题库随机", "整场面试复盘", "简历 + 题库 + AI", "开始模拟面试", "语音播报"]),
        ("/settings", ["主题外观", "AI 模式", "模型 API", "数据信息", "测试连接", "拉取可用模型",
                       "填写要点与常见错误"]),
    ]
    if sid:
        checks.insert(6, (f"/mock/{sid}",
                          ["播报问题", "我的回答", "结束并生成报告", "参考要点", "save-state"]))
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
