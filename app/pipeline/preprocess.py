"""预处理：说话人标签嗅探、轮次切分（带原文字符区间）、无标签文本候选段。

红线 R1 落实：
- 有标签文本：以标签直读归属（source=label），不推测、不改写标签；
- 无标签文本：切分为候选段，角色交由 LLM/启发式推断（source=inferred，低置信），
  且全程保留人工纠正入口（role_map 覆盖）。
"""
from __future__ import annotations

import re
from typing import Any

RE_BRACKET = re.compile(r"^\s*[\[【]\s*([^\]】]{1,20}?)\s*[\]】]\s*[:：]?\s*(.*?)\s*$")
RE_SPEAKER_N = re.compile(r"^\s*[Ss]peaker\s*(\d+)\s*[:：]\s*(.*?)\s*$")
RE_A_B = re.compile(r"^\s*([A-Za-z])\s*[:：]\s*(.*?)\s*$")
RE_COLON = re.compile(r"^\s*([^:：\s][^:：]{0,18}?)\s*[:：]\s*(.*?)\s*$")

ALIAS_INTERVIEWER = {"面试官", "面试官1", "面试官2", "interviewer", "hr", "主考官"}
ALIAS_SELF = {"我", "候选人", "应聘者", "求职者", "面试者", "candidate", "interviewee", "self", "张三", "本人"}

# 启发式问句/面试官特征词（仅 mock 无 AI 时用于低置信推断，标注 source=inferred）
_QUESTION_WORDS = ("你好", "您好", "欢迎", "请", "能", "可以", "你", "你们", "讲", "介绍", "说",
                   "聊聊", "如何", "怎么", "为什么", "如果", "谈谈", "说一下", "有关于", "请问")
_QUESTION_END = re.compile(r"[?？]\s*$")


def detect_format(text: str) -> str:
    """嗅探整篇文本最可能的标签格式：bracket/ab/speaker/colon/none。"""
    counts = {"bracket": 0, "ab": 0, "speaker": 0, "colon": 0}
    sampled = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        sampled += 1
        if sampled > 40:
            break
        if RE_BRACKET.match(line):
            counts["bracket"] += 1
        elif RE_SPEAKER_N.match(line):
            counts["speaker"] += 1
        elif RE_A_B.match(line):
            counts["ab"] += 1
        elif _colon_valid(line):
            counts["colon"] += 1
    if sampled == 0:
        return "none"
    best, best_n = "none", 0
    for fmt, n in counts.items():
        if n > best_n:
            best, best_n = fmt, n
    # 至少要覆盖 1/3 的行才认为是标签文本，否则视为无标签
    if best_n * 3 >= sampled:
        return best
    return "none"


def _colon_valid(line: str) -> bool:
    m = RE_COLON.match(line)
    if not m:
        return False
    label, rest = m.group(1), m.group(2)
    if len(rest) < 2:
        return False
    if re.search(r"[。！？!?]$", label) or re.search(r"[。！？，,]", label):
        return False
    if re.search(r"\s", label):
        return False
    return True


def parse_turns(text: str, fmt: str) -> list[dict]:
    """按格式把原文切成轮次。返回元素：
    {idx, label, text, start, end}（start/end 为 raw_transcript 字符区间，
    供 source_ref 溯源与高亮，保证忠实可审计）。

    Phase 4：无标签文本（fmt=none）按**逐行**切分为候选发言段（text=原文切片），
    随后由 merge_by_role 依据推断角色把相邻同角色的行归并——这样"逐行转写"与
    "多行一段"两种写法都能正确处理。
    """
    if fmt == "none":
        return _line_turns(text)
    turns: list[dict] = []
    pos = 0
    seg_no = 0
    for line in text.splitlines():
        raw = line + "\n"
        start = pos
        pos += len(raw)
        stripped = line.strip()
        if not stripped:
            continue
        label, content = _split_line(stripped, fmt)
        if label is None:
            seg_no += 1
            label = f"未知{seg_no}"
            content = stripped
        # 定位 content 在原文中的真实区间（跳过行首空白与标签部分）
        real_start = text.find(content, start, pos)
        if real_start < 0:
            real_start = start
            real_end = start + len(content)
        else:
            real_end = real_start + len(content)
        turns.append({"idx": len(turns), "label": label,
                      "text": content, "start": real_start, "end": real_end})
    return _merge_same_label(turns)


def _line_turns(text: str) -> list[dict]:
    """无标签文本：逐行作为候选发言段（text=原文精确切片，逐字忠实）。

    行内空白与换行不影响角色推断；相邻同角色行稍后由 merge_by_role 归并。
    """
    turns: list[dict] = []
    pos = 0
    for line in text.splitlines():
        raw = line + "\n"
        line_start = pos
        pos += len(raw)
        if not line.strip():
            continue
        start = line_start + (len(line) - len(line.lstrip()))
        end = line_start + len(line.rstrip()) - 1
        turns.append(_segment(len(turns), text, start, end))
    return turns


def _segment(idx: int, text: str, start: int, end: int) -> dict:
    end = max(end, start)
    return {"idx": idx, "label": f"说话人{idx + 1:02d}",
            "text": text[start:end + 1], "start": start, "end": end}


def merge_by_role(turns: list[dict], role_of: dict[str, str], raw: str = "") -> list[dict]:
    """把相邻且角色相同的段合并为一条轮次（Phase 4：让无标签文本的问答切分可用）。

    仅合并 interviewer/self 两类（other/unknown 保留原样）；合并后的文本取
    **原文连续切片** raw[start:end+1]，因此仍是逐字原文（R2）。合并后重新编号 idx。
    """
    merged: list[dict] = []
    for t in turns:
        role = role_of.get(t["label"], "unknown")
        can_merge = (bool(merged) and role in ("interviewer", "self")
                     and merged[-1].get("role") == role
                     and not _ends_with_question(merged[-1]["text"])
                     and not _looks_like_question(t["text"]))
        if can_merge:
            prev = merged[-1]
            prev["end"] = max(prev["end"], t["end"])
        else:
            item = dict(t)
            item["role"] = role
            merged.append(item)
    for i, t in enumerate(merged):
        t["idx"] = i
        if raw:
            t["text"] = raw[t["start"]:t["end"] + 1]
    return merged


def _split_line(stripped: str, fmt: str) -> tuple[str | None, str]:
    if fmt == "bracket":
        m = RE_BRACKET.match(stripped)
        if m:
            return m.group(1).strip(), m.group(2).strip()
        return None, stripped
    if fmt == "speaker":
        m = RE_SPEAKER_N.match(stripped)
        if m:
            return f"Speaker {m.group(1)}", m.group(2).strip()
        return None, stripped
    if fmt == "ab":
        m = RE_A_B.match(stripped)
        if m:
            return m.group(1), m.group(2).strip()
        return None, stripped
    if fmt == "colon":
        m = RE_COLON.match(stripped)
        if m:
            return m.group(1).strip(), m.group(2).strip()
        return None, stripped
    # none：按段落切分说话人候选（每段一个占位说话人，真实归属需推断/人工）
    return None, stripped


def _merge_same_label(turns: list[dict]) -> list[dict]:
    """合并同一说话人的连续行（多行回答、被换行拆开的同一句话）。

    Phase 4：遇到"新的一个问题"就不再合并——上一行以问号结束，或新一行本身是问句
    （问号结尾/以疑问引导词开头）时，各自成为独立轮次，避免"连续追问"被并成一题；
    这样 RuleExtractor 能把它们切成两条问答（无回答的那条标为待确认）。
    """
    merged: list[dict] = []
    for t in turns:
        can_merge = (bool(merged) and merged[-1]["label"] == t["label"]
                     and not _ends_with_question(merged[-1]["text"])
                     and not _looks_like_question(t["text"]))
        if can_merge:
            prev = merged[-1]
            prev["end"] = t["end"]
            prev["text"] = prev["text"] + "\n" + t["text"] if prev["text"] else t["text"]
        else:
            merged.append(dict(t))
    # 重新编号
    for i, t in enumerate(merged):
        t["idx"] = i
    return merged


def _ends_with_question(text: str) -> bool:
    return bool(re.search(r"[?？]\s*$", (text or "").strip()))


def _looks_like_question(text: str) -> bool:
    """是否需要作为"新问题"起一条（用于避免连续追问被并成同一题）。"""
    t = (text or "").strip()
    if not t:
        return False
    return bool(_ends_with_question(t)) or t.startswith(_QUESTION_WORDS)


def alias_role(label: str) -> str | None:
    """标签直读角色：有明确别名才返回，否则 None（不猜）。"""
    l = (label or "").strip()
    if l in ALIAS_INTERVIEWER:
        return "interviewer"
    if l in ALIAS_SELF:
        return "self"
    return None


def heuristic_role(text: str) -> tuple[str, float]:
    """mock/规则路径下对无标签候选段的低置信推断（source=inferred，全部可人工纠正）。

    Phase 4 调整：中等长度的陈述句按"回答"处理（低置信 0.45），避免整段被判 unknown
    导致无标签文本无法进入提取；短句仍按"其他"处理。
    """
    t = (text or "").strip()
    if not t:
        return "unknown", 0.2
    if len(t) <= 200 and (_QUESTION_END.search(t) or t.startswith(_QUESTION_WORDS)):
        return "interviewer", 0.6
    if len(t) >= 20:
        return "self", 0.45
    if len(t) <= 12:
        return "other", 0.45
    return "self", 0.4


def ordered_labels(turns: list[dict]) -> list[str]:
    out: list[str] = []
    for t in turns:
        if t["label"] not in out:
            out.append(t["label"])
    return out


def turn_text_block(turns: list[dict], role_of: dict[str, str]) -> str:
    """把轮次拼成带角色标签的对话文本，供 LLM 抽取（保留原始句读与口误）。"""
    parts: list[str] = []
    for t in turns:
        role = role_of.get(t["label"], "unknown")
        tag = {"interviewer": "面试官", "self": "候选人", "other": "其他", "unknown": "未知"}.get(role, role)
        parts.append(f"[{tag}] {t['text']}")
    return "\n".join(parts)


def speaker_meta(turns: list[dict], role_map: dict[str, str], fmt: str) -> list[dict]:
    """生成 Speaker 列表（InterviewRecord.speaker_map 的 JSON 内容）。

    role_map: label -> role（用户最终确认/纠正后的结果）
    source  : label 来自真实标签（source=label）或推断/启发（source=inferred）
    """
    speakers: list[dict] = []
    for i, label in enumerate(ordered_labels(turns)):
        role = role_map.get(label, "unknown")
        base = alias_role(label)
        if base and role == "unknown":
            role = base
        # source：标签即角色别名 → label（直读，可信）；其余（用户指定/启发/AI）→ inferred
        source = "label" if base else "inferred"
        conf = 0.9 if source == "label" else (0.6 if role in ("interviewer", "self") else 0.5)
        speakers.append({"id": f"sp_{i}", "label": label, "role": role,
                         "source": source, "confidence": conf,
                         "color": "#3b82f6" if role == "interviewer" else "#10b981"})
    return speakers
