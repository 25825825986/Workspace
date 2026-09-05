"""通用工具：ID / 时间 / JSON / R3 归一化键。"""
from __future__ import annotations

import datetime
import json
import re
import uuid
from typing import Any

_ID_SAN = re.compile(r"[^0-9a-zA-Z_-]")


def uid() -> str:
    return uuid.uuid4().hex[:12]


def now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def loads(s: str | None, default: Any = None) -> Any:
    if s is None or s == "":
        return default
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return default


def norm_key(text: str | None) -> str:
    """R3 归一化去重键：小写 + 去除空白与常见中英标点。

    用于知识点（Category.norm）与问题文本的去重合并比较。
    """
    if not text:
        return ""
    s = re.sub(r"[\s,，。.．？?？、；;：:!！~～·•\-—_/\\|（）()【】\[\]《》〈〉<>「」『』“”\"'‘’…*#@&]+", "", text.lower())
    return s


def clean_role(value: str | None) -> str | None:
    """把 UI 传入的角色值清洗为合法枚举，None 表示未知。"""
    if not value:
        return None
    v = value.strip().lower()
    aliases = {
        "interviewer": "interviewer", "面试官": "interviewer", "面试官1": "interviewer",
        "hr": "interviewer", "hr面": "interviewer", "面试者问": "interviewer",
        "self": "self", "我": "self", "候选人": "self", "应聘者": "self", "求职者": "self",
        "candidate": "self", "面试者": "self", "interviewee": "self",
        "other": "other", "旁听": "other", "其他": "other",
    }
    return aliases.get(v)
