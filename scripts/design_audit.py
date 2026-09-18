"""设计评审证据采集（不依赖浏览器）：页面/组件量化指标 + CSS/JS/无障碍统计。

用法：python scripts/design_audit.py
输出：结构化文本证据，供 docs/11-design-review.md 引用。
"""
from __future__ import annotations

import pathlib
import re
import sys
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.main import app  # noqa: E402
from app import repository  # noqa: E402

client = app.test_client()

TAG_RE = {
    "a": re.compile(r"<a\b", re.I),
    "button": re.compile(r"<button\b", re.I),
    "input": re.compile(r"<input\b", re.I),
    "select": re.compile(r"<select\b", re.I),
    "textarea": re.compile(r"<textarea\b", re.I),
    "form": re.compile(r"<form\b", re.I),
    "table": re.compile(r"<table\b", re.I),
    "details": re.compile(r"<details\b", re.I),
    "card": re.compile(r'class="[^"]*\bcard\b', re.I),
    "chip": re.compile(r'class="[^"]*\bchip\b', re.I),
    "btn": re.compile(r'class="[^"]*\bbtn\b', re.I),
    "label": re.compile(r"<label\b", re.I),
    "inline_script": re.compile(r"<script(?![^>]*\bsrc=)", re.I),
    "empty_state": re.compile(r"empty-state|知识库还是空的|没有匹配的面试记录|还没有面试记录|本场未提取到问答", re.I),
    "loading_hint": re.compile(r"识别中|解析中|保存中|生成题目中|测试中|检索中|测试连接|正在重算", re.I),
    "aria": re.compile(r"aria-|role=", re.I),
    "confirm": re.compile(r"confirm\(|alert\(|prompt\(", re.I),
}


def page_metrics(path: str, html: str) -> dict:
    out = {}
    for name, rx in TAG_RE.items():
        out[name] = len(rx.findall(html))
    out["bytes"] = len(html.encode("utf-8"))
    return out


def main() -> int:
    print("=" * 78)
    print("一、页面清单与组件量化（渲染后 HTML 统计）")
    print("=" * 78)
    home = client.get("/")
    iid = ""
    m = re.search(r"/interviews/([0-9a-f]+)", home.get_data(as_text=True))
    if m:
        iid = m.group(1)
    qid = ""
    if iid:
        data = repository.list_qa(iid)
        qid = data[0]["id"] if data else ""
    kb = client.get("/knowledge").get_data(as_text=True)
    me = re.search(r"/knowledge/compare/([0-9a-f]+)", kb)
    eid = me.group(1) if me else ""
    mk = client.get("/mock").get_data(as_text=True)
    ms = re.findall(r"/mock/([0-9a-f]{6,})", mk)
    sid = ms[0] if ms else ""

    pages = [
        ("/ 面试记录", "/"),
        ("/import 导入面试", "/import"),
        ("/interviews/<id> 本场复盘", f"/interviews/{iid}"),
        ("/interviews/<id>/qa/<qid> 单题复盘", f"/interviews/{iid}/qa/{qid}"),
        ("/knowledge 知识库", "/knowledge"),
        ("/knowledge/compare/<id> 对比", f"/knowledge/compare/{eid}"),
        ("/mock 模拟设置", "/mock"),
        ("/mock/<sid> 模拟作答", f"/mock/{sid}"),
        ("/mock/<sid>/report 模拟报告", f"/mock/{sid}/report"),
        ("/settings 设置", "/settings"),
    ]
    header = (f"{'页面':30s} {'KB':>5s} {'链接':>4s} {'按钮':>4s} {'输入':>4s} "
              f"{'表单':>4s} {'卡片':>4s} {'标签':>4s} {'内联JS':>5s} {'确认框':>5s} {'空态':>4s} {'加载':>4s}")
    print(header)
    print("-" * len(header))
    totals = Counter()
    for label, path in pages:
        resp = client.get(path)
        html = resp.get_data(as_text=True)
        met = page_metrics(path, html)
        for k, v in met.items():
            totals[k] += v
        print(f"{label:30s} {met['bytes']/1024:5.1f} {met['a']:4d} {met['button']:4d} "
              f"{met['input']:4d} {met['form']:4d} {met['card']:4d} {met['label']:4d} "
              f"{met['inline_script']:5d} {met['confirm']:5d} {met['empty_state']:4d} "
              f"{met['loading_hint']:4d}")
    print("-" * len(header))
    print(f"{'合计':30s} {totals['bytes']/1024:5.1f} {totals['a']:4d} {totals['button']:4d} "
          f"{totals['input']:4d} {totals['form']:4d} {totals['card']:4d} {totals['label']:4d} "
          f"{totals['inline_script']:5d} {totals['confirm']:5d}")

    print()
    print("=" * 78)
    print("二、导航与路由")
    print("=" * 78)
    routes = re.findall(r'@app\.route\("([^"]+)"(?:, methods=(\[[^\]]+\]))?\)\s*\n(?:@[\w\.]+\n)*def (\w+)',
                        (ROOT / "app" / "main.py").read_text(encoding="utf-8"))
    page_routes = [r for r in routes if not r[0].startswith("/api") and not r[0].startswith("/download")]
    api_routes = [r for r in routes if r[0].startswith("/api")]
    dl_routes = [r for r in routes if r[0].startswith("/download")]
    print(f"页面路由 {len(page_routes)} 条 / API {len(api_routes)} 条 / 下载 {len(dl_routes)} 条（共 {len(routes)}）")
    for path, _methods, fn in page_routes:
        print(f"  页面 {path:38s} -> {fn}")
    print()
    print("  顶栏导航项：", re.findall(r'<a href="([^"]+)"[^>]*>([^<]+)</a>',
                                    (ROOT / "app/templates/base.html").read_text(encoding="utf-8")))

    print()
    print("=" * 78)
    print("三、视觉规范（style.css）")
    print("=" * 78)
    css = (ROOT / "app/static/style.css").read_text(encoding="utf-8")
    tokens = re.findall(r"^\s*(--[\w-]+):", css, re.M)
    dark_tokens = re.findall(r"--[\w-]+:", css[css.find('[data-theme="dark"]'):css.find("}", css.find('[data-theme="dark"]'))])
    hexes = re.findall(r"#[0-9a-fA-F]{3,8}\b", css)
    hex_outside_root = [h for h in hexes if h.lower() not in
                        (ROOT / "app/static/style.css").read_text(encoding="utf-8")
                        .split(":root{")[1].split("}")[0].lower()]
    print(f"CSS 尺寸 {len(css)/1024:.1f} KB / 变量 {len(set(tokens))} 个 / 深色主题变量 {len(dark_tokens)} 个")
    print(f"硬编码颜色出现 {len(hexes)} 次（其中 :root 之外 {len(hex_outside_root)} 次）：{sorted(set(hex_outside_root))[:12]}")
    print("transition 声明：", len(re.findall(r"transition:", css)))
    print("animation/keyframes：", len(re.findall(r"animation:", css)), "/", len(re.findall(r"@keyframes", css)))
    print("媒体查询断点：", re.findall(r"@media\([^)]+\)", css))
    print("hover/focus 规则：", len(re.findall(r":hover", css)), "/", len(re.findall(r":focus-visible|:focus", css)))
    print("圆角变量：", sorted(set(re.findall(r"--r[\w-]*:\s*([^;]+);", css))))

    print()
    print("=" * 78)
    print("四、脚本与交互实现")
    print("=" * 78)
    appjs = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
    print(f"全局脚本 {len(appjs.splitlines())} 行；页面内联脚本块合计 {totals['inline_script']} 个")
    for tpl in sorted((ROOT / "app/templates").glob("*.html")):
        text = tpl.read_text(encoding="utf-8")
        lines = len(text.splitlines())
        scripts = len(re.findall(r"<script(?![^>]*src)", text))
        fetches = len(re.findall(r"fetch\(", text))
        dialogs = len(re.findall(r"confirm\(|alert\(|prompt\(", text))
        arias = len(re.findall(r"aria-|role=", text))
        print(f"  {tpl.name:24s} 行数 {lines:4d} 内联JS {scripts:2d} fetch {fetches:2d} "
              f"原生对话框 {dialogs:2d} aria/role {arias:2d}")

    print()
    print("=" * 78)
    print("五、反馈状态与无障碍覆盖")
    print("=" * 78)
    all_tpl = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "app/templates").glob("*.html"))
    print("空状态文案/样式：", len(re.findall(r"empty-state|还是空的|没有匹配|还没有", all_tpl)))
    print("加载/进行中文案：", len(re.findall(r"中…|中\.\.\.|识别中|解析中|保存中|生成题目中|测试中|检索中", all_tpl)))
    print("错误提示处理（showErr/alert/错误文案）：", len(re.findall(r"showErr|alert\(", all_tpl)))
    print("<label> 数量：", len(re.findall(r"<label", all_tpl)), " / <input|select|textarea> 数量：",
          len(re.findall(r"<(?:input|select|textarea)", all_tpl)))
    print("aria-/role 使用：", len(re.findall(r"aria-|role=", all_tpl)),
          " / alt 属性：", len(re.findall(r"\balt=", all_tpl)))
    print("键盘快捷键：", len(re.findall(r"keydown", all_tpl)), "处")
    print("原生 confirm/alert/prompt：", len(re.findall(r"confirm\(|alert\(|prompt\(", all_tpl)), "处")

    print()
    print("=" * 78)
    print("六、设计系统硬指标（边注本，见 docs/14-frontend-design-plan.md §7）")
    print("=" * 78)
    base_html = (ROOT / "app/templates/base.html").read_text(encoding="utf-8")
    settings_html = (ROOT / "app/templates/settings.html").read_text(encoding="utf-8")
    config_py = (ROOT / "app/config.py").read_text(encoding="utf-8")
    fail: list[str] = []

    def gate(label: str, ok: bool, detail: str = "") -> None:
        print(f"  {'✓' if ok else '✗'} {label}" + (f"　{detail}" if detail else ""))
        if not ok:
            fail.append(label)

    # --- 三层面色 token 齐备，且原文面是最亮的面 ---
    gate("三层面色 token（paper / desk / margin）在浅色与深色两套都定义",
         css.count("--paper:") == 2 and css.count("--desk:") == 2 and css.count("--margin:") == 2)
    gate("原文面是直角（--r-surface:0，.card 与阅读面用它）",
         "--r-surface:0" in css and "border-radius:var(--r-surface)" in css)
    gate("原文用宋体栈、界面用黑体栈（--serif / --font 各自独立）",
         "--serif:" in css and "Noto Serif SC" in css and "--font:" in css and "Noto Sans SC" in css)
    gate("原文排版 token：字号 ≥15.5px、行高 ≥1.85",
         "--fs-read:16.5px" in css and "--lh-read:1.9" in css)
    gate("阅读面宽度按 40 全角字以内取值（655px / 16.5px ≈ 39.7）",
         "--maxw-read:655px" in css)

    # --- 颜色只做状态：主操作是墨黑，彩色只剩 seal / flag ---
    gate("主操作 = 墨黑（--brand 指向 --ink，不再用彩色主按钮）", "--brand:var(--ink)" in css)
    gate("内容语义色只有两个（--seal / --flag）",
         "--seal:" in css and "--flag:" in css and "--brand:#2563eb" not in css,
         "旧的默认蓝 #2563eb 已移除")
    gate("强调色 accent-color 收进墨黑（原生 radio/checkbox 不再是系统蓝）",
         "accent-color:var(--ink)" in css)

    # --- §5 六项模板化痕迹清理 ---
    footer = re.search(r'<footer class="wrap foot">(.*?)</footer>', base_html, re.S)
    gate("页脚不再用「·」串成一串元信息", bool(footer) and "·" not in footer.group(1))
    gate("模型徽标不再把模型 ID 用「·」拼进顶栏", '"·"' not in config_py.split("def mode_badge")[1][:400])
    # 「→」只允许出现在方向性导航上（与「← 上一题」成对的上一题/下一题/返回），
    # 内容链接与普通按钮的装饰箭头在阶段一已全部删除。
    NAV_ARROW = re.compile(r">[^<>]{0,24}(上一题|下一题|上一步|下一步|返回)[^<>]{0,4}→</(?:a|button)>")
    arrow_links = re.findall(r">[^<>]{0,24}→</(?:a|button)>", all_tpl)
    bad_arrows = [a for a in arrow_links if not NAV_ARROW.search(a)]
    gate("装饰性「→」已删（只剩与「← 上一题」成对的方向性导航箭头）",
         not bad_arrows, f"发现非导航箭头 {bad_arrows[:3]}")
    gate("设置页不再用装饰性圈码 ①-⑤",
         not re.search(r"[①②③④⑤]", settings_html))
    gate("删掉了每张卡片入场都播一次的动画（.ui-rise）",
         "ui-rise" not in css and not re.search(r"\.card,\.rec-card,\.qa-row,\.entry\{animation", css))
    gate("记录卡元信息分组（.rec-when / .rec-rest 而不是空格硬拼）",
         ".rec-when" in css and ".rec-rest" in css)

    print()
    print("=" * 78)
    if fail:
        print(f"设计系统硬指标未达标 {len(fail)} 项：")
        for f in fail:
            print("  -", f)
        return 1
    print("设计系统硬指标全部达标。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
