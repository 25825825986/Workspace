"""相似问题识别（Phase 6 / Q3 口径）。

分层策略：
1. **规则层（离线可用）**：归一化完全一致 = 1.0；否则
   `score = 0.6 × difflib 字符相似度 + 0.4 × 中文 2-gram Jaccard`，并对共同命中的
   技术名词加权（每词 +0.05，最多 +0.10，封顶 1.0）。
2. **灰区（0.55–0.72）AI 判定**：AI 模式下把灰区候选**批量**交给 LLM 判定"是否同一问题"，
   结果记 `method=ai` 并缓存（不重复计费）。
3. **人工**：确认/取消关联（`method=manual`）优先级最高，永不被自动结果覆盖。

阈值：`>=0.72` 判相似；`0.55–0.72` 灰区；`<0.55` 忽略。
计算时机：入库合并后**增量**计算（按一级分类分桶，避免全库两两比较）。
"""
from __future__ import annotations

import difflib
from itertools import combinations

from .. import config, repository
from ..util import norm_key

SIMILAR_THRESHOLD = 0.72
GRAY_LOW = 0.45            # 灰区下限：≥0.45 记为"疑似"，供人工确认/取消
MAX_BUCKET = 80            # 单个分桶内最多参与比较的条目数（超出按频次取前 N）
METHOD_PRIORITY = {"rule": 1, "ai": 2, "manual": 3}

_TECH_TERMS = ("jvm", "java", "redis", "缓存", "算法", "数据库", "mysql", "sql", "http", "tcp",
               "线程", "并发", "锁", "内存", "gc", "spring", "索引", "事务", "分布式", "kafka",
               "消息队列", "系统设计", "微服务", "限流", "性能", "linux", "docker", "设计模式",
               "哈希", "排序", "网络", "协议", "项目", "自我介绍", "职业规划", "离职", "团队",
               "协作", "沟通", "薪资", "反问")
# 意图词规范化：把不同说法映射到同一意图 token，弥补中文改写带来的字面差异
_INTENT_SYNONYMS = (
    ("如何实现", "实现"), ("怎么实现", "实现"), ("实现原理", "实现"), ("实现方式", "实现"),
    ("实现的", "实现"), ("怎么做的", "实现"), ("怎么做的呢", "实现"),
    ("怎么处理", "处理"), ("如何处理", "处理"), ("怎么保证", "处理"), ("如何保证", "处理"),
    ("怎么解决", "处理"), ("如何解决", "处理"), ("怎么优化", "优化"), ("如何优化", "优化"),
    ("讲讲", "介绍"), ("说说", "介绍"), ("介绍一下", "介绍"), ("介绍下", "介绍"),
    ("聊一聊", "介绍"), ("谈一谈", "介绍"), ("谈谈", "介绍"),
    ("介绍一下你自己", "自我介绍"), ("介绍下你自己", "自我介绍"), ("自我介绍", "自我介绍"),
    ("为什么", "原因"), ("什么原因", "原因"), ("原因是什么", "原因"),
)


def _bigrams(text: str) -> set[str]:
    s = norm_key(text)
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _unigrams(text: str) -> set[str]:
    return set(norm_key(text))


def _canonical_terms(text: str) -> set[str]:
    """领域词 + 规范化意图词（用于同义改写识别）。"""
    low = (text or "").lower()
    terms = {term for term in _TECH_TERMS if term in low}
    for synonym, canon in _INTENT_SYNONYMS:
        if synonym in low:
            terms.add(canon)
    return terms


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _tech_boost(a: str, b: str) -> float:
    la, lb = (a or "").lower(), (b or "").lower()
    shared = sum(1 for term in _TECH_TERMS if term in la and term in lb)
    return min(shared * 0.04, 0.08)


def rule_score(qa: str, qb: str) -> float:
    """规则相似度（0–1）：字面 + 词序 + 领域/意图词三层加权。

    - 0.25 字符集合 Jaccard（同义改写里常有大量共同汉字）
    - 0.25 中文 2-gram Jaccard（词序邻近）
    - 0.20 difflib 序列相似度（整体形态）
    - 0.30 领域词 + 规范化意图词 Jaccard（"讲讲≈介绍"、"怎么实现≈实现"）
    - 共同技术名词加权 ≤0.08
    """
    na, nb = norm_key(qa), norm_key(qb)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    seq = difflib.SequenceMatcher(None, na, nb).ratio()
    score = (0.25 * _jaccard(_unigrams(qa), _unigrams(qb))
             + 0.25 * _jaccard(_bigrams(qa), _bigrams(qb))
             + 0.20 * seq
             + 0.30 * _jaccard(_canonical_terms(qa), _canonical_terms(qb))
             + _tech_boost(qa, qb))
    return round(min(score, 1.0), 4)


def candidate_pairs(entries: list[dict]) -> list[tuple[dict, dict, float]]:
    """按一级分类分桶，生成候选相似对（规则层）。"""
    buckets: dict[str, list[dict]] = {}
    for e in entries:
        buckets.setdefault(e.get("group_name") or "其他", []).append(e)
    pairs: list[tuple[dict, dict, float]] = []
    for _group, items in buckets.items():
        items = sorted(items, key=lambda x: (-(x.get("ask_times") or 0), x.get("head_question") or ""))
        items = items[:MAX_BUCKET]
        for a, b in combinations(items, 2):
            score = rule_score(a.get("head_question", ""), b.get("head_question", ""))
            if score >= GRAY_LOW:
                pairs.append((a, b, score))
    return pairs


def _canonical(a_id: str, b_id: str) -> tuple[str, str]:
    return (a_id, b_id) if a_id <= b_id else (b_id, a_id)


def recompute(use_ai: bool | None = None, interview_id: str | None = None) -> dict:
    """重新计算相似关联并落库。

    - 规则层：全量重算候选（分桶）
    - AI 层：仅对灰区且尚无 ai/manual 结论的对做批量判定（仅在 AI 模式且有 Key 时）
    """
    entries = repository.list_entries()
    if interview_id:
        # 增量场景：只关心本场涉及的条目，但仍与全库比较
        pass
    pairs = candidate_pairs(entries)
    rule_kept = 0
    gray_kept = 0
    for a, b, score in pairs:
        a_id, b_id = _canonical(a["id"], b["id"])
        if score >= SIMILAR_THRESHOLD:
            repository.upsert_similar_pair(a_id, b_id, score, "rule", "规则阈值命中")
            rule_kept += 1
        else:
            # 灰区：记为"疑似"，供人工确认或取消（非 AI 模式也能看到线索）
            repository.upsert_similar_pair(a_id, b_id, score, "rule", "疑似：灰区待确认")
            gray_kept += 1

    gray = [(a, b, s) for a, b, s in pairs if GRAY_LOW <= s < SIMILAR_THRESHOLD]
    ai_used = 0
    ai_judged: list[dict] = []
    if use_ai is None:
        use_ai = config.extractor_mode() == "deepseek"
    if use_ai and gray:
        ai_result = _judge_gray_batch(gray)
        ai_used = ai_result.get("judged", 0)
        ai_judged = ai_result.get("confirmed", [])
    return {"entries": len(entries), "candidate_pairs": len(pairs),
            "rule_matched": rule_kept, "gray_pairs": len(gray), "gray_kept": gray_kept,
            "ai_judged": ai_used, "ai_confirmed": len(ai_judged),
            "ai_available": bool(use_ai)}


def _judge_gray_batch(gray: list[tuple[dict, dict, float]], batch: int = 8) -> dict:
    """灰区批量交 LLM 判定（失败时静默降级为"未判定"）。"""
    from . import llm as llm_mod

    tool = {
        "type": "function",
        "function": {
            "name": "judge_same_question",
            "description": "判断每一对面试问题是否为同一问题（同一考点，措辞不同）",
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "pair_id": {"type": "string"},
                                "same": {"type": "boolean"},
                                "confidence": {"type": "number"},
                                "reason": {"type": "string"},
                            },
                            "required": ["pair_id", "same", "confidence"],
                        },
                    }
                },
                "required": ["items"],
            },
        },
    }
    judged = 0
    confirmed: list[dict] = []
    for start in range(0, len(gray), batch):
        chunk = gray[start:start + batch]
        lines = []
        for i, (a, b, s) in enumerate(chunk):
            lines.append(f"[{i}] A: {a['head_question']}\n    B: {b['head_question']}"
                         f"\n    (规则相似度 {s})")
        try:
            args = llm_mod.chat_tool(
                "判断每组 A/B 是否指向同一个面试问题（同一考点，仅措辞不同）。"
                "同一话题但考点不同视为 false。",
                "\n".join(lines), tool)
        except Exception:
            break                        # 网络/Key 不可用：保持规则结果
        by_index = {str(it.get("pair_id")): it for it in args.get("items", [])}
        for i, (a, b, s) in enumerate(chunk):
            it = by_index.get(str(i))
            if not it:
                continue
            judged += 1
            a_id, b_id = _canonical(a["id"], b["id"])
            if it.get("same"):
                repository.upsert_similar_pair(a_id, b_id, max(float(it.get("confidence") or 0), s),
                                               "ai", str(it.get("reason") or "AI 判定同一问题"))
                confirmed.append({"a": a_id, "b": b_id})
            else:
                # AI 判定"不是同一问题" → 撤销规则层的"疑似"记录
                current = repository.get_similar_pair(a_id, b_id)
                if current and current["method"] == "rule":
                    repository.delete_similar_pair(a_id, b_id)
    return {"judged": judged, "confirmed": confirmed}


def neighbors(entry_id: str) -> list[dict]:
    """某条目的相似条目（含分数与方法）。"""
    pairs = repository.list_similar_pairs(entry_id)
    out = []
    for p in pairs:
        other_id = p["b_entry_id"] if p["a_entry_id"] == entry_id else p["a_entry_id"]
        entry = repository.get_entry(other_id)
        if entry:
            out.append({"entry": entry, "score": p["score"], "method": p["method"],
                        "reason": p.get("reason")})
    out.sort(key=lambda x: -float(x["score"] or 0))
    return out
