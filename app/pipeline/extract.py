"""提取器抽象与实现（Pipeline 第②步）。

item 统一结构（供 postprocess 组装）：
    {
      "q_text": 逐字问题文本,
      "q_turn": 规则路径=所属轮次 idx | LLM 路径=None(待模糊回填),
      "answers": [{"text": 逐字回答, "turn": idx | None}],
      "category": 知识点建议或 None,
      "confidence": 0-1,
      "note": 说明或 None,
    }

红线 R2 语义：无论哪条路径，q_text/answers[].text 语义上必须是原文逐字引用；
LLM 路径在 postprocess 用原文区间回填 source_ref 并做一致性核验。
"""
from __future__ import annotations

from typing import Any

from .. import config
from .. import knowledge
from . import llm as llm_mod
from . import preprocess

MAX_BLOCK_CHARS = 6000  # 单块对话文本上限，超出切块多次调用

_CATEGORY_KEYWORDS: list[tuple[str, str]] = [
    ("JVM", "JVM"), ("jvm", "JVM"), ("内存模型", "JVM内存模型"), ("垃圾回收", "垃圾回收"),
    ("Redis", "Redis"), ("redis", "Redis"), ("本地缓存", "缓存"), ("缓存", "缓存"),
    ("算法", "算法"), ("时间复杂度", "算法"), ("数据结构", "数据结构"),
    ("数据库", "数据库"), ("MySQL", "MySQL"), ("mysql", "MySQL"), ("索引", "数据库索引"),
    ("SQL", "SQL"), ("sql", "SQL"), ("事务", "事务"),
    ("HTTP", "HTTP"), ("http", "HTTP"), ("TCP", "TCP"), ("tcp", "TCP"), ("网络", "网络"),
    ("线程", "线程与并发"), ("并发", "线程与并发"), ("锁", "线程与并发"),
    ("分布式", "分布式"), ("消息队列", "消息队列"), ("设计模式", "设计模式"),
    ("Spring", "Spring"), ("spring", "Spring"), ("系统设计", "系统设计"),
    ("自我介绍", "自我介绍"), ("离职", "离职原因"), ("薪资", "薪资期望"),
    ("职业规划", "职业规划"), ("为什么", "求职动机"), ("优缺点", "优缺点"),
    ("项目", "项目经历"), ("STAR", "行为问题"), ("star", "行为问题"),
    ("冲突", "行为问题"), ("压力", "行为问题"), ("失败", "行为问题"), ("困难", "行为问题"),
]


def suggest_category(q_text: str) -> str | None:
    for kw, name in _CATEGORY_KEYWORDS:
        if kw in q_text:
            return name
    return None


class BaseExtractor:
    name = "base"

    def extract(self, turns: list[dict], role_of: dict[str, str]) -> list[dict]:
        raise NotImplementedError


class RuleExtractor(BaseExtractor):
    """有标签/角色文本的确定性切分：interviewer 轮次开问题，self 连续轮次并作回答。

    忠实性由构造保证：问题/回答文本直接取轮次原文（含口误卡壳），并携带轮次区间。
    """

    name = "rule"

    def extract(self, turns: list[dict], role_of: dict[str, str]) -> list[dict]:
        items: list[dict] = []
        cur: dict | None = None
        invite: dict | None = None      # 面试官"你有什么想问我们的吗"（仅在没有后续提问时才保留成题）
        in_reverse = False              # 刚发出邀请，等待"我提问"
        reverse_open = False            # 已抓到"我提问"，等待面试官回答

        def close(item: dict | None) -> None:
            if item is None:
                return
            if not item["answers"]:
                item["confidence"] = 0.5
            items.append(item)

        for t in turns:
            role = role_of.get(t["label"], "unknown")
            text = t["text"]
            if role == "interviewer":
                if reverse_open and cur is not None and cur.get("direction") == "reverse":
                    # 面试官在回答我的提问 → 记为该反问的回答
                    cur["answers"].append({"text": text, "turn": t["idx"]})
                    reverse_open = False
                    close(cur)
                    cur = None
                    in_reverse = False
                    continue
                close(cur)
                if invite is not None:
                    close(invite)       # 邀请之后面试官又发言 → 邀请按"未作答问题"保留
                    invite = None
                in_reverse = False
                cur = {"q_text": text, "q_turn": t["idx"], "answers": [],
                       "category": suggest_category(text), "confidence": 0.9,
                       "note": None, "direction": "normal"}
                if knowledge.is_reverse_invite(text):
                    # 邀请本身不是"待回答的问题"：暂存，等待候选人提问
                    invite = cur
                    cur = None
                    in_reverse = True
            elif role == "self":
                if in_reverse:
                    # 候选人提问 → 建立反转向条目（邀请话术不再单独成题）
                    invite = None
                    close(cur)
                    cur = {"q_text": text, "q_turn": t["idx"], "answers": [],
                           "category": "反问环节", "confidence": 0.85,
                           "note": None, "direction": "reverse"}
                    in_reverse = False
                    reverse_open = True
                elif reverse_open and cur is not None:
                    # 反问后我又补充说明 → 并入问题文本（仍是逐字原文）
                    cur["q_text"] = f"{cur['q_text']}\n{text}"
                elif cur is not None and cur.get("direction") != "reverse":
                    cur["answers"].append({"text": text, "turn": t["idx"]})
                else:
                    continue            # 候选人先于任何提问发言（寒暄）：忽略
            # 其他/未知角色轮次：噪音，跳过
        close(cur)
        close(invite)                   # 面试全程没有提问环节 → 邀请作为待确认条目保留
        return items


class MockExtractor(RuleExtractor):
    """内置 Mock：用规则实现模拟"LLM 结构化输出"，无 Key 时跑通整条闭环。"""

    name = "mock"


class DeepSeekExtractor(BaseExtractor):
    """真实 LLM 路径（OpenAI 兼容 + function calling）。"""

    name = "deepseek"

    def infer_roles(self, turns: list[dict]) -> dict[str, str]:
        """无标签文本的角色推断：seg_id=轮次 idx → role。"""
        lines = "\n".join(f"[{t['idx']}] {t['text']}" for t in turns)
        args = llm_mod.chat_tool(
            "对以下无标签面试候选段推断说话人角色：interviewer=面试官/提问方，self=候选人/回答方。"
            "依据句式与语义，无法判断输出 unknown。",
            lines, llm_mod.TOOL_INFER_ROLES)
        result: dict[str, str] = {}
        for seg in args.get("segments", []):
            seg_id = str(seg.get("seg_id"))
            role = str(seg.get("role", "unknown"))
            conf = float(seg.get("confidence") or 0)
            if conf >= 0.6 and role in ("interviewer", "self", "other"):
                result[seg_id] = role
        return result

    def extract(self, turns: list[dict], role_of: dict[str, str]) -> list[dict]:
        blocks: list[list[dict]] = []
        cur_block: list[dict] = []
        cur_len = 0
        for t in turns:
            if role_of.get(t["label"]) not in ("interviewer", "self"):
                continue
            if cur_len + len(t["text"]) > MAX_BLOCK_CHARS and cur_block:
                blocks.append(cur_block)
                cur_block, cur_len = [], 0
            cur_block.append(t)
            cur_len += len(t["text"])
        if cur_block:
            blocks.append(cur_block)

        items: list[dict] = []
        for block in blocks:
            text = preprocess.turn_text_block(block, role_of)
            args = llm_mod.chat_tool(llm_mod.EXTRACT_SYSTEM_PROMPT, text, llm_mod.TOOL_EXTRACT)
            for it in args.get("items", []):
                q_text = str(it.get("question") or "").strip()
                if not q_text:
                    continue
                answers = [{"text": str(a).strip(), "turn": None}
                           for a in (it.get("answers") or []) if str(a).strip()]
                items.append({
                    "q_text": q_text, "q_turn": None, "answers": answers,
                    "category": str(it.get("category") or "").strip() or None,
                    "confidence": float(it.get("confidence") or 0.5),
                    "note": str(it.get("note") or "").strip() or None,
                    "direction": (str(it.get("direction") or "normal").strip().lower()
                                  if str(it.get("direction") or "").strip().lower() in ("normal", "reverse")
                                  else "normal"),
                })
        if not items:
            raise llm_mod.LLMError("LLM 未提取到任何问答，请检查文本或稍后重试")
        return items


def make_extractor(mode: str | None = None) -> BaseExtractor:
    mode = mode or config.extractor_mode()
    if mode == "deepseek":
        return DeepSeekExtractor()
    if mode == "mock":
        return MockExtractor()
    return RuleExtractor()


def describe() -> str:
    """当前提取方式的可读描述（跟随设置页实时变化）。"""
    return config.extractor_label()


def has_llm() -> bool:
    return config.has_llm()
