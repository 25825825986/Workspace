"""模拟面试出题（Phase 7 / FR-04）。

来源：
- `whole`  ：指定某场面试记录，按原顺序（可选打乱）出题；
- `bank`   ：从知识库按频次加权随机抽取；
- `resume` ：简历关键词 + 题库随机 + **AI 生成**（比例可配）；无 Key 时自动退化为纯题库。

每条题目附带"参考要点"（来自知识库条目的忠实核心答案 + 优化建议），
默认在答题界面隐藏（可手动翻开）。
"""
from __future__ import annotations

import random

from .. import config, repository


def _entry_to_question(entry: dict, source: str, is_follow_up: bool = False) -> dict:
    ref_answer = (entry.get("core_extract") or "").strip()
    tips = (entry.get("optimization") or "").strip()
    return {
        "question_text": entry.get("head_question") or "",
        "source": source,
        "reference_entry_id": entry.get("id"),
        "reference_answer": ref_answer or None,
        "reference_tips": tips or None,
        "is_follow_up": is_follow_up,
    }


def _whole_questions(interview_id: str, shuffle: bool) -> list[dict]:
    qas = repository.list_qa(interview_id)
    if not qas:
        return []
    items = []
    for q in qas:
        ref_answer = ""
        if q.get("answers"):
            first = q["answers"][0]
            ref_answer = first.get("corrected_text") if first.get("is_corrected") else first.get("extract_text")
        items.append({
            "question_text": q.get("q_corrected_text") if q.get("q_is_corrected") else q.get("q_text"),
            "source": "whole",
            "reference_entry_id": q.get("entry_id"),
            "reference_answer": (ref_answer or "").strip() or None,
            "reference_tips": (q.get("optimization") or "").strip() or None,
            "is_follow_up": False,
        })
    if shuffle:
        random.shuffle(items)
    return items


def _bank_pool(exclude_ids: set[str] | None = None) -> list[dict]:
    entries = [e for e in repository.list_entries() if (e.get("head_question") or "").strip()]
    if exclude_ids:
        entries = [e for e in entries if e["id"] not in exclude_ids]
    return entries


def _weighted_sample(entries: list[dict], count: int) -> list[dict]:
    pool = list(entries)
    picked: list[dict] = []
    while pool and len(picked) < count:
        weights = [max(1, int(e.get("ask_times") or 1)) for e in pool]
        choice = random.choices(pool, weights=weights, k=1)[0]
        picked.append(choice)
        pool.remove(choice)
    return picked


def _ai_questions(resume: dict, count: int) -> list[dict]:
    """AI 生成针对性问题；失败或未配置时返回空列表（调用方自动回退题库）。"""
    from . import llm as llm_mod

    tool = {
        "type": "function",
        "function": {
            "name": "generate_questions",
            "description": "基于候选人简历与技能栈生成面试问题",
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "question": {"type": "string"},
                                "focus": {"type": "string", "description": "考察点/参考要点"},
                                "difficulty": {"type": "string", "enum": ["easy", "medium", "hard"]},
                            },
                            "required": ["question", "focus"],
                        },
                    }
                },
                "required": ["items"],
            },
        },
    }
    skills = "、".join(resume.get("skills") or []) or "（未识别到明确技能）"
    projects = "、".join(resume.get("projects") or []) or "（未识别到项目名）"
    prompt = (f"候选人技能：{skills}\n项目：{projects}\n简历正文节选：\n"
              f"{(resume.get('content_text') or '')[:1500]}\n\n"
              f"请生成 {count} 个面试问题，覆盖其技能栈深度、项目细节与技术选型权衡，"
              "避免泛泛而谈，每个问题给出考察点作为参考要点。")
    try:
        args = llm_mod.chat_tool("你是资深技术面试官，负责设计有区分度的问题。", prompt, tool)
    except Exception:
        return []
    out = []
    for it in args.get("items", [])[:count]:
        q = str(it.get("question") or "").strip()
        if not q:
            continue
        out.append({"question_text": q, "source": "ai", "reference_entry_id": None,
                    "reference_answer": None,
                    "reference_tips": (str(it.get("focus") or "").strip() or None),
                    "is_follow_up": False})
    return out


def _follow_up_question(entry: dict) -> dict | None:
    """基于条目生成一条追问（优先用历史变体问法，否则用通用深挖句式）。"""
    variants = [v for v in (entry.get("question_variants") or []) if (v or "").strip()]
    if variants:
        text = f"追问：{random.choice(variants)}"
    else:
        text = f"追问：关于「{entry.get('head_question') or '这个问题'}」，如果规模扩大 10 倍，你的方案要改哪些地方？"
    item = _entry_to_question(entry, "whole" if False else "bank", is_follow_up=True)
    item["question_text"] = text
    item["reference_answer"] = None
    return item


def build_questions(mode: str = "bank", interview_id: str | None = None,
                    resume_id: str | None = None, count: int = 8, shuffle: bool = False,
                    include_follow_up: bool = False, ai_ratio: float = 0.4) -> dict:
    """返回 {questions: [...], ai_used: int, bank_used: int, note: str}"""
    count = max(1, min(int(count or 8), 30))
    questions: list[dict] = []
    note = ""
    ai_used = 0

    if mode == "whole" and interview_id:
        questions = _whole_questions(interview_id, shuffle)
        if not questions:
            raise ValueError("该面试记录还没有可用的问答，请先在面试记录页确认题目")
        if len(questions) > count:
            questions = questions[:count]
    elif mode == "resume":
        resume = repository.get_resume(resume_id) if resume_id else None
        if not resume:
            raise ValueError("请先导入或选择一份简历")
        ai_target = int(round(count * max(0.0, min(ai_ratio, 0.8))))
        ai_items = _ai_questions(resume, ai_target) if ai_target else []
        if not ai_items and ai_target:
            note = "AI 生成不可用（未配置 Key 或调用失败），已全部改用题库随机题"
        questions.extend(ai_items)
        ai_used = len(ai_items)
        pool = _bank_pool()
        need = count - len(questions)
        if need > 0:
            questions.extend(_entry_to_question(e, "bank")
                             for e in _weighted_sample(pool, need))
        if shuffle:
            random.shuffle(questions)
    else:                                    # bank
        pool = _bank_pool()
        if not pool:
            raise ValueError("知识库还没有条目：请先在面试记录页把已确认题目「入库」")
        questions = [_entry_to_question(e, "bank") for e in _weighted_sample(pool, count)]

    if include_follow_up and questions:
        entries = {e["id"]: e for e in repository.list_entries()}
        follow_sources = [q for q in questions if q.get("reference_entry_id")
                          in entries][:2]
        for q in follow_sources:
            fu = _follow_up_question(entries[q["reference_entry_id"]])
            if fu:
                questions.append(fu)
    return {"questions": questions[:max(count, len(questions))], "ai_used": ai_used,
            "bank_used": sum(1 for q in questions if q["source"] == "bank"), "note": note}
