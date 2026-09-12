"""LLM 接入（OpenAI 兼容协议，默认 DeepSeek）。

结构化落地方式（见 docs/02 §2）：
- 首选 function calling（tool schema 强约束）；
- 输出由上层做强校验 + 失败重试 1 次；
- 未配置 Key 时本模块不参与（由 extractor_mode 回退 rule/mock）。
"""
from __future__ import annotations

import json
from typing import Any

from .. import config


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
                            "direction": {
                                "type": "string", "enum": ["normal", "reverse"],
                                "description": "normal=面试官提问；reverse=候选人向面试官提问（反问环节）",
                            },
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


def _client(api_key: str | None = None, base_url: str | None = None):
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover
        raise LLMError("未安装 openai 依赖，无法调用 LLM") from exc
    key = (api_key or config.llm_api_key()).strip()
    if not key:
        raise LLMError("未配置 API Key（可在「设置 → 模型 API」中填写，或写入 .env）")
    kwargs: dict[str, Any] = {"api_key": key}
    url = (base_url or config.llm_base_url()).strip()
    if url:
        kwargs["base_url"] = url
    return OpenAI(**kwargs)


# ---------- 零依赖 HTTP 传输（openai SDK 不可用时兜底） ----------

def _urllib_request(url: str, payload: dict | None, key: str, timeout: int = 60):
    """用标准库发一个 OpenAI 兼容请求，返回 (status, text)。"""
    import urllib.error
    import urllib.request

    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "ignore")
    except Exception as exc:  # noqa: BLE001
        return -1, f"{type(exc).__name__}: {exc}"


def _http_chat_completion(payload: dict, key: str, url: str) -> dict:
    """不依赖 openai SDK 的 chat.completions 调用（返回响应 JSON）。"""
    status, text = _urllib_request(url.rstrip("/") + "/chat/completions", payload, key)
    if status != 200:
        raise LLMError(_explain_http_error(status, text, url))
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError(f"响应不是合法 JSON：{text[:200]}") from exc


def _explain_http_error(status: int, text: str, base_url: str) -> str:
    """把 HTTP 错误翻译成可操作的提示（本轮真实排障经验固化）。"""
    snippet = (text or "").strip()[:200]
    if status == 404:
        return (f"接口返回 404（{base_url}）：地址多半写多了路径——Base URL 只填到版本段"
                "（如 https://api.siliconflow.cn/v1），模型名要单独填在「模型名」里")
    if status in (400, 422) and "model" in snippet.lower():
        return (f"接口返回 {status}：模型 ID 可能不正确（部分平台要求带厂商前缀，"
                f"如 deepseek-ai/DeepSeek-V3）。原始信息：{snippet}")
    if status == 401:
        return f"接口返回 401：API Key 无效或已过期。原始信息：{snippet}"
    if status == 403:
        return f"接口返回 403：Key 无权限或余额不足。原始信息：{snippet}"
    if status == 429:
        return f"接口返回 429：触发限流，请稍后重试。原始信息：{snippet}"
    if status == -1:
        return f"网络不可达：{snippet}"
    return f"接口返回 {status}：{snippet}"


def list_models(api_key: str | None = None, base_url: str | None = None) -> dict:
    """拉取可用模型列表（零依赖），供设置页选择，避免手抄错模型 ID。"""
    key = (api_key if api_key is not None else config.llm_api_key()).strip()
    if not key:
        return {"ok": False, "message": "未配置 API Key", "models": []}
    url = (base_url or config.llm_base_url()).rstrip("/")
    status, text = _urllib_request(url + "/models", None, key, timeout=30)
    if status != 200:
        return {"ok": False, "message": _explain_http_error(status, text, url), "models": []}
    try:
        data = json.loads(text)
        ids = sorted({str(m.get("id", "")) for m in data.get("data", []) if m.get("id")})
    except (json.JSONDecodeError, AttributeError) as exc:
        return {"ok": False, "message": f"模型列表解析失败：{exc}", "models": []}
    return {"ok": True, "message": f"共 {len(ids)} 个模型", "models": ids, "base_url": url}


def chat_tool(system: str, user: str, tool: dict) -> dict:
    """function calling 一次调用，失败自动重试 1 次。返回解析后的参数对象。

    openai SDK 可用时走 SDK；不可用时自动降级为标准库 HTTP（零依赖）。
    """
    key = config.llm_api_key().strip()
    if not key:
        raise LLMError("未配置 API Key（可在「设置 → 模型 API」中填写，或写入 .env）")
    url = config.llm_base_url()
    payload = {
        "model": config.llm_model(),
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "tools": [{"type": "function", "function": tool["function"]}],
        "tool_choice": {"type": "function", "function": {"name": tool["function"]["name"]}},
        "temperature": 0.2,
    }
    try:
        import openai  # noqa: F401
        use_sdk = True
    except ImportError:
        use_sdk = False

    last_error: Exception | None = None
    for _attempt in (1, 2):
        try:
            if use_sdk:
                client = _client(api_key=key, base_url=url)
                resp = client.chat.completions.create(**payload)
                msg = resp.choices[0].message
                calls = getattr(msg, "tool_calls", None)
                if calls and calls[0].function.arguments:
                    return json.loads(calls[0].function.arguments)
                raise LLMError("模型未返回工具调用，请重试")
            data = _http_chat_completion(payload, key, url)
            calls = (data.get("choices") or [{}])[0].get("message", {}).get("tool_calls") or []
            if calls and calls[0].get("function", {}).get("arguments"):
                return json.loads(calls[0]["function"]["arguments"])
            raise LLMError("模型未返回工具调用，请重试")
        except LLMError:
            raise
        except Exception as exc:  # 网络/限流等：重试一次
            last_error = exc
    raise LLMError(f"LLM 调用失败（已重试）: {last_error}")


def test_connection(api_key: str | None = None, base_url: str | None = None,
                    model: str | None = None) -> dict:
    """设置页「测试连接」：最小请求验证 Key/地址/模型是否可用。

    返回 {ok, message, elapsed_ms?, model?}——任何异常都转成可读信息，不抛给路由。
    """
    import time

    key = (api_key if api_key is not None else config.llm_api_key()).strip()
    if not key:
        return {"ok": False, "message": "未配置 API Key：请先填写并保存，或改用非 AI 模式"}
    if base_url is None:
        info = config.llm_base_url_info()
    else:
        normalized, note = config.normalize_base_url(base_url)
        info = {"url": normalized, "raw": base_url, "note": note}
    use_model = (model or config.llm_model()).strip() or config.llm_model()

    result: dict[str, Any] = {"base_url": info["url"], "raw_base_url": info["raw"],
                              "model": use_model}
    if info.get("note"):
        result["note"] = info["note"]

    # 第一步：查模型列表，先确认“模型 ID 是否存在”（比直接对话更容易定位问题）
    listing = list_models(api_key=key, base_url=info["url"])
    if listing.get("ok") and listing.get("models") and use_model not in listing["models"]:
        similar = [m for m in listing["models"]
                   if use_model.split("/")[-1].lower() in m.lower()][:5]
        hint = ("模型 ID 不在平台模型列表中；"
                + (f"可用相近模型：{', '.join(similar)}" if similar else "请点「拉取可用模型」选择"))
        return {**result, "ok": False, "message": f"连接失败：{hint}",
                "models": similar or listing["models"][:20]}

    # 第二步：发一次最小对话，验证 Key 与网络
    payload = {"model": use_model, "messages": [{"role": "user", "content": "ping"}],
               "max_tokens": 4, "temperature": 0}
    started = time.perf_counter()
    status, text = _urllib_request(info["url"].rstrip("/") + "/chat/completions",
                                   payload, key, timeout=45)
    elapsed = int((time.perf_counter() - started) * 1000)
    if status != 200:
        return {**result, "ok": False,
                "message": _explain_http_error(status, text, info["url"])}
    reply = ""
    try:
        data = json.loads(text)
        reply = (data["choices"][0]["message"].get("content") or "").strip()[:40]
    except (json.JSONDecodeError, KeyError, IndexError, AttributeError):
        reply = ""
    msg = f"连接成功（{elapsed} ms）"
    if info.get("note"):
        msg += f" · {info['note']}"
    return {**result, "ok": True, "message": msg, "elapsed_ms": elapsed, "reply": reply}
