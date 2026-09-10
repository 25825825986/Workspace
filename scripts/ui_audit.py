"""界面像素审计：不依赖人眼，客观检查渲染结果。

检查项：
1. 非空白（内容墨迹占比 > 阈值）
2. 无明显横向溢出（最右 10px 列不应被内容占满）
3. 主题生效（浅色背景亮、深色背景暗；同页深浅主题亮度差异明显）
4. 顶部导航存在（顶部 60px 带内有墨迹）
5. 内容未贴底（底部 10px 行墨迹占比低，说明截图高度足够）

用法：python scripts/ui_audit.py   （需先跑截图脚本）
"""
from __future__ import annotations

import pathlib
import sys
import warnings

warnings.simplefilter("ignore", DeprecationWarning)

from PIL import Image  # noqa: E402

SHOTS = pathlib.Path("screenshots")
BG_TOLERANCE = 12          # 与背景色的容差（判断"是否是内容"）
MIN_CONTRAST = 4.0         # 正文最暗像素相对背景的最低对比度（WCAG AA 正文为 4.5，留少量余量）


def _pixels(img: Image.Image) -> list[tuple[int, int, int]]:
    return list(img.convert("RGB").getdata())


def ink_ratio(img: Image.Image, box: tuple[int, int, int, int] | None = None,
              bg: tuple[int, int, int] | None = None) -> float:
    region = img.crop(box) if box else img
    px = _pixels(region)
    total = 0
    ink = 0
    for r, g, b in px:
        total += 1
        if bg is None:
            if not (r > 245 and g > 245 and b > 245):
                ink += 1
        else:
            if (abs(r - bg[0]) > BG_TOLERANCE or abs(g - bg[1]) > BG_TOLERANCE
                    or abs(b - bg[2]) > BG_TOLERANCE):
                ink += 1
    return ink / max(total, 1)


def content_bbox(img: Image.Image, bg: tuple[int, int, int],
                 y_start: int = 64) -> tuple[int, int, int, int]:
    """主体内容包围盒（跳过全宽顶栏，用于检查容器是否居中留白、是否贴边）。"""
    px = img.convert("RGB")
    w, h = px.size
    data = px.load()
    min_x, max_x, min_y, max_y = w, 0, h, 0
    step = 3
    for y in range(min(y_start, h - 1), h, step):
        for x in range(0, w, step):
            r, g, b = data[x, y]
            if (abs(r - bg[0]) > BG_TOLERANCE or abs(g - bg[1]) > BG_TOLERANCE
                    or abs(b - bg[2]) > BG_TOLERANCE):
                min_x = min(min_x, x)
                max_x = max(max_x, x)
                min_y = min(min_y, y)
                max_y = max(max_y, y)
    return min_x, min_y, max_x, max_y


def text_contrast(img: Image.Image, bg: tuple[int, int, int]) -> float:
    """正文对比度估计：深色背景取最亮 0.5% 像素，浅色背景取最暗 0.5% 像素。"""
    px = [p for p in _pixels(img) if (abs(p[0] - bg[0]) > BG_TOLERANCE
                                      or abs(p[1] - bg[1]) > BG_TOLERANCE
                                      or abs(p[2] - bg[2]) > BG_TOLERANCE)]
    if not px:
        return 0.0
    dark_bg = luminance(bg) < 128
    px.sort(key=luminance, reverse=dark_bg)
    head = px[:max(1, len(px) // 200)]
    fg = tuple(sum(c[i] for c in head) // len(head) for i in range(3))
    l1, l2 = luminance(fg), luminance(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 5) / (lo + 5)


def dominant_bg(img: Image.Image) -> tuple[tuple[int, int, int], float]:
    """取左侧 5px 列的众数颜色作为背景估计（避开内容）。"""
    strip = img.crop((0, 0, 5, img.height)).convert("RGB")
    counts: dict[tuple[int, int, int], int] = {}
    for p in strip.getdata():
        counts[p] = counts.get(p, 0) + 1
    color = max(counts, key=counts.get)
    total = sum(counts.values())
    return color, counts[color] / max(total, 1)


def luminance(color: tuple[int, int, int]) -> float:
    return 0.2126 * color[0] + 0.7152 * color[1] + 0.0722 * color[2]


def audit(path: pathlib.Path, expect_dark: bool) -> tuple[int, list[str]]:
    img = Image.open(path)
    w, h = img.size
    bg, bg_share = dominant_bg(img)
    problems: list[str] = []

    overall = ink_ratio(img, bg=bg)
    if overall < 0.005:
        problems.append(f"内容过少（墨迹 {overall:.3%}），疑似空白页")
    if bg_share < 0.5:
        problems.append(f"背景色不稳定（主色占比 {bg_share:.0%}），可能有整屏背景块")

    right = ink_ratio(img, box=(w - 10, 0, w, h), bg=bg)
    if right > 0.35:
        problems.append(f"右侧 10px 墨迹 {right:.0%}，疑似横向溢出")

    bottom = ink_ratio(img, box=(0, h - 10, w, h), bg=bg)
    if bottom > 0.20:
        problems.append(f"底部 10px 墨迹 {bottom:.0%}，页面可能被截断")

    nav = ink_ratio(img, box=(0, 0, w, min(60, h)), bg=bg)
    if nav < 0.01:
        problems.append("顶部导航区无内容")

    # 容器留白：桌面宽度下，主体内容左右应各有 ≥80px 边距（max-width 生效）
    bbox = content_bbox(img, bg)
    left_inset, right_inset = bbox[0], w - bbox[2]
    if w >= 1200 and min(left_inset, right_inset) < 80:
        problems.append(f"内容贴边（左 {left_inset}px / 右 {right_inset}px），容器居中留白不足")

    # 正文对比度
    contrast = text_contrast(img, bg)
    if contrast < MIN_CONTRAST:
        problems.append(f"文字对比度过低（{contrast:.1f}:1 < {MIN_CONTRAST}:1）")

    lum = luminance(bg)
    if expect_dark and lum > 120:
        problems.append(f"期望深色主题但背景偏亮（亮度 {lum:.0f}）")
    if not expect_dark and lum < 120:
        problems.append(f"期望浅色主题但背景偏暗（亮度 {lum:.0f}）")

    print(f"{path.name:28s} {w}x{h} bg={bg} lum={lum:5.1f} ink={overall:6.2%} "
          f"contrast={contrast:4.1f} inset={left_inset}/{right_inset} "
          f"bottom={bottom:5.1%}"
          + ("  ⚠ " + "；".join(problems) if problems else "  OK"))
    return (1 if problems else 0), problems


def main() -> int:
    files = sorted(SHOTS.glob("*.png"))
    if not files:
        print("没有截图，请先运行 python scripts/screenshot_ui.py")
        return 1
    failed = 0
    for f in files:
        if f.name.startswith("_"):
            continue
        dark = "_dark" in f.name
        code, _ = audit(f, expect_dark=dark)
        failed += code
    print()
    print("AUDIT_OK" if not failed else f"AUDIT_ISSUES={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
