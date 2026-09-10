"""后处理（Pipeline 第③步）：把提取 item 组装为入库的 qa 记录。

- source_ref 回填：优先取规则路径携带的轮次区间；LLM 路径做"归一化逐字对齐"，
  对齐失败则 source_ref 置空并把条目标记 pending（供人工核对，绝不猜测引用）。
- 红线 R2：answers[].extract_text 一律取自 item 原文文本（修正仅能经 corrected_text）。
"""
from __future__ import annotations

import re

from .. import knowledge, repository
from .extract import suggest_category
from . import similar

_PUNCT = re.compile(r"[\s,，。.．？?、；;：:!！~～·•\-—_/\\|（）()【】\[\]《》〈〉<>「」『』“”\"'‘’…*#@&]+")


def _norm_with_index(text: str) -> tuple[str, list[int]]:
    kept: list[str] = []
    idxs: list[int] = []
    for i, ch in enumerate(text):
        if not _PUNCT.match(ch):
            kept.append(ch.lower())
            idxs.append(i)
    return "".join(kept), idxs


def find_verbatim(hay: str, needle: str) -> tuple[int, int] | None:
    """在 hay 中定位 needle 的忠实子串（忽略空白与标点差异），返回 [start,end]。"""
    n_hay, i_hay = _norm_with_index(hay)
    n_needle, _ = _norm_with_index(needle)
    if len(n_needle) < 4:
        return None
    pos = n_hay.find(n_needle)
    if pos < 0:
        return None
    return i_hay[pos], i_hay[pos + len(n_needle) - 1]


def assemble(items: list[dict], turns: list[dict],
             role_of: dict[str, str], speaker_of_label: dict[str, str]) -> list[dict]:
    """items → 可入库 qa 记录（未含 interview_id，由调用方补充）。

    turns: 轮次 {idx,label,text,start,end}
    """
    turn_by_idx = {t["idx"]: t for t in turns}
    out: list[dict] = []
    for seq, item in enumerate(items, start=1):
        direction = item.get("direction") or "normal"
        q_role = "self" if direction == "reverse" else "interviewer"      # 提问方
        a_role = "interviewer" if direction == "reverse" else "self"      # 回答方
        q_turn = turn_by_idx.get(item["q_turn"]) if item["q_turn"] is not None else None
        q_ref = None
        q_speaker = None
        if q_turn is not None:
            q_ref = {"start": q_turn["start"], "end": q_turn["end"], "quote": q_turn["text"]}
            q_speaker = speaker_of_label.get(q_turn["label"])
        else:
            # LLM 路径：在提问方轮次中做归一化逐字对齐（反转问答的提问方是"我"）
            for t in turns:
                if role_of.get(t["label"]) != q_role:
                    continue
                span = find_verbatim(t["text"], item["q_text"])
                if span:
                    q_ref = {"start": t["start"] + span[0],
                             "end": t["start"] + span[1],
                             "quote": t["text"][span[0]:span[1] + 1]}
                    q_speaker = speaker_of_label.get(t["label"])
                    break
        if q_speaker is None:
            for label, role in role_of.items():
                if role == q_role:
                    q_speaker = speaker_of_label.get(label)
                    break

        answers: list[dict] = []
        any_unmatched = False
        q_turn_start = q_turn["idx"] if q_turn is not None else -1
        answer_turns = [t for t in turns if role_of.get(t["label"]) == a_role]
        for a in item.get("answers", []):
            a_turn = turn_by_idx.get(a["turn"]) if a.get("turn") is not None else None
            a_ref = None
            a_speaker = None
            if a_turn is not None:
                a_ref = {"start": a_turn["start"], "end": a_turn["end"], "quote": a_turn["text"]}
                a_speaker = speaker_of_label.get(a_turn["label"])
            else:
                # LLM 路径：在该问之后按序匹配回答方轮次
                candidates = [t for t in answer_turns if t["idx"] > q_turn_start]
                for t in candidates:
                    span = find_verbatim(t["text"], a["text"])
                    if span:
                        a_ref = {"start": t["start"] + span[0],
                                 "end": t["start"] + span[1],
                                 "quote": t["text"][span[0]:span[1] + 1]}
                        a_speaker = speaker_of_label.get(t["label"])
                        break
                else:
                    a_ref, a_speaker = None, None
            if a_ref is None:
                any_unmatched = True
            answers.append({
                "extract_text": a["text"],
                "corrected_text": None,
                "is_corrected": False,
                "source_ref": a_ref,
                "speaker_id": a_speaker,
                "duration_sec": None,
            })

        conf = float(item.get("confidence") or 0.5)
        # LLM 未对齐原文引用 → 低置信，强制待确认（红线 R2 可审计）
        if item.get("q_turn") is None and q_ref is None:
            any_unmatched = True
            conf = min(conf, 0.5)
        if any_unmatched:
            conf = min(conf, 0.6)
        # Phase 4：未提取到回答的条目（连续追问/候选人未作答）也保留，强制待人工确认
        if not answers:
            conf = min(conf, 0.5)

        category = item.get("category") or suggest_category(item["q_text"]) or "未分类"
        out.append({
            "seq": seq,
            "status": "confirmed" if conf >= 0.75 else "pending",
            "direction": direction,
            "q_text": item["q_text"],
            "q_speaker_id": q_speaker,
            "q_source_ref": q_ref,
            "q_confidence": round(item.get("confidence") or 0.5, 3),
            "q_is_corrected": False,
            "q_corrected_text": None,
            "answers": answers,
            "annotation": "",
            "optimization": "",
            "category_suggestion": category,
            "entry_id": None,
            "tag_ids": [],
            "confidence": round(conf, 3),
        })
    return out


def merge_into_bank(interview_id: str, qa_id: str | None = None) -> dict:
    """R3 入库合并：把已确认问答并入面经库。

    命中规则（见原型 §5.5）：同知识点下问题归一化键命中 head/variants → 并入既有条目；
    否则按知识点 norm 幂等取 Category，新建 KnowledgeEntry。
    返回统计：{merged, skipped}。
    """
    qas = repository.list_qa(interview_id)
    if qa_id:
        qas = [q for q in qas if q["id"] == qa_id]
    merged = 0
    skipped = 0
    for q in qas:
        if q["status"] not in ("confirmed", "corrected"):
            skipped += 1
            continue
        if q.get("entry_id"):
            continue
        cat = repository.get_or_create_category(q.get("category_name") or "未分类")
        # Phase 6：专题 + 一级分类（反问环节由 direction 决定）
        topic, group = knowledge.classify(q["q_text"], cat.get("name", ""), cat.get("type", ""),
                                          q.get("direction") or "normal")
        entry = repository.find_entry_by_question(cat["id"], _q_norm(q["q_text"]))
        if not entry:
            entry = repository.create_entry(cat["id"], q["q_text"], topic, group)
        elif not entry.get("group_name") or not entry.get("topic"):
            repository.set_entry_classification(entry["id"], topic or entry.get("topic") or "",
                                                group or entry.get("group_name") or "")
        repository.add_qa_to_entry(entry["id"], q, q["id"])
        repository.update_qa(q["id"], {"category_id": cat["id"], "entry_id": entry["id"]})
        merged += 1

    similar_stats = None
    if merged:
        # Phase 6：入库后增量重算相似关联（规则层，快；AI 灰区判定在知识库页手动触发）
        similar_stats = similar.recompute(use_ai=False)
    return {"merged": merged, "skipped": skipped, "similar": similar_stats}


def _q_norm(text: str) -> str:
    from ..util import norm_key
    return norm_key(text)
