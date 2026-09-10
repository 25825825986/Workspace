"""导出：Markdown 生成（本场复盘报告 / 面经库快照）。

结构遵循原型 §M8：元信息 → 每题（Q 原文 / A 原文 / 优化建议 / 批注 / 类别）；
忠实原文与优化建议分节输出（红线 R2 在导出层同样分离）。
"""
from __future__ import annotations

import re

from . import repository
from .config import EXPORT_DIR
from .service import ROLE_LABEL

_ILLEGAL_FN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def save_export(filename: str, md: str) -> str:
    """把导出内容落盘到 data/export/（Phase 4：EXPORT_DIR 不再闲置，便于留档与备份）。"""
    name = _ILLEGAL_FN.sub("_", (filename or "export.md").strip())[:150].strip(" .") or "export.md"
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = EXPORT_DIR / name
    path.write_text(md, encoding="utf-8")
    return str(path)


def _speaker_display(iv: dict, sid: str | None) -> str:
    if not sid:
        return "未知"
    for sp in iv.get("speaker_map", []):
        if sp["id"] == sid:
            return ROLE_LABEL.get(sp["role"]) or sp.get("label") or sid
    return sid


def _quote_block(ref: dict | None, fallback: str | None = None) -> str:
    if ref and ref.get("quote"):
        return "  > " + str(ref["quote"]).replace("\n", "\n  > ")
    if fallback:
        return "  > " + fallback.replace("\n", "\n  > ")
    return "  > （原文区间缺失，需人工核对）"


def interview_to_md(iid: str) -> str:
    iv = repository.get_interview(iid)
    qas = repository.list_qa(iid)
    if not iv:
        raise KeyError(f"面试记录不存在: {iid}")
    lines: list[str] = []
    lines.append("# 面试复盘报告")
    lines.append("")
    lines.append(f"- **标题**：{iv['title']}")
    if iv.get("company"):
        lines.append(f"- **公司**：{iv['company']} ｜ **岗位**：{iv.get('position') or '-'}")
    lines.append(f"- **类型**：{iv.get('interview_type')} ｜ **日期**：{iv.get('date') or '-'}")
    lines.append(f"- **解析器**：{iv.get('parser_version') or '-'} ｜ **文本标签格式**：{iv.get('transcript_format')}")
    lines.append(f"- **题目数**：{len(qas)}")
    lines.append("")
    lines.append("> 说明：本报告「忠实原文」均为逐字引用（含口误/卡壳）；「优化建议 / 批注」为独立记录，不与原文混写。")
    lines.append("")
    if not qas:
        lines.append("_（本场尚未提取到任何问答）_")
    for q in qas:
        lines.append(f"## {q['seq']}. [{q.get('category_name') or '未分类'}] {q.get('q_text') or ''}")
        lines.append("")
        lines.append(f"- **状态**：{q.get('status')} ｜ **知识点**：{q.get('category_name') or '未分类'}")
        lines.append(f"- **提问者**：{_speaker_display(iv, q.get('q_speaker_id'))}")
        q_text = q.get("q_text") or ""
        if q.get("q_is_corrected") and q.get("q_corrected_text"):
            q_text = q["q_corrected_text"]
        lines.append(f"- **忠实问题**：{q_text}")
        lines.append("  原文引用：")
        lines.append(_quote_block(q.get("q_source_ref")))
        lines.append("")
        for a in q.get("answers", []):
            lines.append(f"- **回答（{_speaker_display(iv, a.get('speaker_id'))}）**：")
            body = a.get("extract_text") or ""
            if a.get("is_corrected") and a.get("corrected_text"):
                lines.append(f"  - 修正后文本：{a['corrected_text']}")
                lines.append(f"  - 原始忠实提取：{body}")
            else:
                lines.append(f"  - {body}")
            lines.append("  原文引用：")
            lines.append(_quote_block(a.get("source_ref")))
            lines.append("")
        opt = (q.get("optimization") or "").strip()
        ann = (q.get("annotation") or "").strip()
        lines.append(f"- **优化建议**（独立栏）：{opt or '（未填写）'}")
        lines.append(f"- **批注**：{ann or '（无）'}")
        lines.append("")
    return "\n".join(lines)


def bank_to_md() -> str:
    entries = repository.list_entries()
    stats = repository.entry_stats()
    lines: list[str] = []
    lines.append("# 面经库快照")
    lines.append("")
    lines.append(f"- 知识点条目：{stats['entries']} ｜ 覆盖类别：{stats['categories']} ｜ "
                 f"累计提问：{stats['total_ask']} ｜ 面试场次：{stats['interviews']}")
    lines.append("")
    lines.append("> 按知识点跨场聚合（R3）：频次 = 历史被问总次数；「忠实核心答案」为逐字原文，"
                 "「优化建议」独立记录。")
    lines.append("")
    if not entries:
        lines.append("_（面经库为空，请先在单场复盘中「纳入面经库」）_")
    current_cat: str | None = None
    for e in entries:
        cat = e.get("category_name") or "未分类"
        if cat != current_cat:
            lines.append(f"## {cat}")
            lines.append("")
            current_cat = cat
        variants = e.get("question_variants") or []
        lines.append(f"### {e.get('head_question') or '-'}  （频次 {e['ask_times']} · 最近 {e.get('last_asked_at') or '-'} · "
                     f"出处 {e.get('source_interview_count', 0)} 场）")
        if variants:
            lines.append(f"- **变体问法**（{len(variants)}）：{('；'.join(variants[:5]))}")
        lines.append(f"- **忠实核心答案（原文）**：{(e.get('core_extract') or '（暂无）')}")
        lines.append(f"- **优化建议**（独立栏）：{(e.get('optimization') or '（未填写）')}")
        lines.append("")
    return "\n".join(lines)
