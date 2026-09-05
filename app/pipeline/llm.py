"""LLM 接入（OpenAI 兼容协议，默认 DeepSeek）。

结构化落地方式（见 docs/02 §2）：
- 首选 function calling（tool schema 强约束）；
- 输出由上层做强校验 + 失败重试 1 次；
- 未配置 Key 时本模块不参与（由 extractor_mode 回退 rule/mock）。
"""
from __future__ import annotations

import json
from typing import Any

from ..config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL


class LLMError(RuntimeError):
    """LLM 调用/解析失败（上层可捕获并向用户给出友好提示）。"""


# 红线 Prompt（每次抽取注入，R1/R2）
EXTRACT_SYSTEM_PROMPT = (
    "你是 AI_Review 面试复盘系统的结构化抽取器。从面试对话中提取「面试官问题 → 候选人回答」。\n"
    "严格遵守规则：\n"
    "1) 问题只取面试官（interviewer）所说，回答只取候选人（self/候选人）所说；\n"
    "2) 问题与回答必须【逐字引用原文】，保留口误、卡壳（嗯、呃、其实…）、语气词与标点，"
    "禁止改写、润色、补全、纠错、转述；\n"
    "3) 若某处原文残缺到无法逐字引用，请在 note 中说明并把 confidence 置低；\n"
    "4) 只做提取，不做改进建议——优化建议不属于本工具输出。"
)

# 角色推断工具（无说话人标签时使用；有标签时禁止使用，不得修改标签）
TOOL_INFER_ROLES: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "infer_roles",
        "description": "对无标签面试文本的每个候选发言段推断说话人角色",
        "parameters": {
            "type": "object",
            "properties": {
                "segments": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "seg_id": {"type": "string", "description": "候选段 ID，原样返回"},
                            "role": {"type": "string",
                                     "enum": ["interviewer", "self", "other", "unknown"],
                                     "description": "interviewer=面试官/提问方, self=候选人/回答方"},
                            "confidence": {"type": "number", "description": "0-1"},
                            "reason": {"type": "string", "description": "一句话依据（句式/用词/语义）"},
                        },
                        "required": ["seg_id", "role", "confidence"],
                    }
                }
            },
            "required": ["segments"],
        },
    },
}

# 问答抽取工具（与 docs/02 §2 的工具 schema 一致）
TOOL_EXTRACT: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "extract_qa",
        "description": "从面试对话块中抽取问答对，逐字引用原文，禁止改写润色",
        "parameters": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "seq": {"type": "integer", "description": "块内顺序"},
                            "question": {"type": "string", "description": "逐字引用的问题原文"},
                            "answers": {"type": "array", "items": {"type": "string"},
                                        "description": "逐字引用的回答原文（候选人的连续发言，可多段）"},
                            "category": {"type": "string",
                                         "description": "建议知识点，如 本地缓存/JVM内存/自我介绍"},
                            "confidence": {"type": "number", "description": "0-1"},
                            "note": {"type": "string",
                                     "description": "低置信或异常说明，可空"},
                        },
                        "required": ["seq", "question", "answers", "confidence"],
                    }
                }
            },
            "required": ["items"],
        },
    },
}


def _client():
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover
        raise LLMError("未安装 openai 依赖，无法调用 LLM") from exc
    if not LLM_API_KEY:
        raise LLMError("未配置 DEEPSEEK_API_KEY（请复制 .env.example 为 .env 并填写）")
    kwargs: dict[str, Any] = {"api_key": LLM_API_KEY}
    if LLM_BASE_URL:
        kwargs["base_url"] = LLM_BASE_URL
    return OpenAI(**kwargs)


def chat_tool(system: str, user: str, tool: dict) -> dict:
    """function calling 一次调用，失败自动重试 1 次。返回解析后的参数对象。"""
    client = _client()
    last_error: Exception | None = None
    for _attempt in (1, 2):
        try:
            resp = client.chat.completions.create(
                model=LLM_MODEL,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
                tools=[{"type": "function", "function": tool["function"]}],
                tool_choice={"type": "function", "function": {"name": tool["function"]["name"]}},
                temperature=0.2,
            )
            msg = resp.choices[0].message
            if msg.tool_calls and msg.tool_calls[0].function.arguments:
                return json.loads(msg.tool_calls[0].function.arguments)
            raise LLMError("模型未返回工具调用，请重试")
        except LLMError:
            raise
        except Exception as exc:  # 网络/限流等：重试一次
            last_error = exc
    raise LLMError(f"LLM 调用失败（已重试）: {last_error}")
