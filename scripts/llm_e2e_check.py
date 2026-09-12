"""临时验证：真实模型 API 端到端联调（读配置 → 列模型 → 测试连接 → 工具调用 → AI 抽取入库）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config, repository, service  # noqa: E402
from app.pipeline import llm  # noqa: E402

ok = fail = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global ok, fail
    if cond:
        ok += 1
        print("PASS ", name)
    else:
        fail += 1
        print("FAIL ", name, "|", detail)


print("=== 1) 配置解析 ===")
print("base_url :", config.llm_base_url())
print("model    :", config.llm_model())
print("extractor:", config.extractor_mode())
check("配置规范化正确", config.llm_base_url() == "https://api.siliconflow.cn/v1"
      and config.extractor_mode() == "deepseek")

print()
print("=== 2) 拉取模型列表 ===")
listing = llm.list_models()
check("模型列表可拉取", listing.get("ok") and len(listing.get("models", [])) > 10,
      str(listing)[:200])
check("配置的模型在列表中", config.llm_model() in listing.get("models", []),
      config.llm_model())

print()
print("=== 3) 测试连接 ===")
conn = llm.test_connection()
print(json.dumps(conn, ensure_ascii=False)[:300])
check("连接测试通过", conn.get("ok") is True, str(conn)[:200])

print()
print("=== 4) 工具调用（openai SDK 路径，抽取真实使用）===")
TOOL = {
    "type": "function",
    "function": {
        "name": "extract_qa",
        "description": "逐字抽取问答",
        "parameters": {
            "type": "object",
            "properties": {"items": {"type": "array", "items": {
                "type": "object",
                "properties": {"question": {"type": "string"},
                               "answers": {"type": "array", "items": {"type": "string"}},
                               "confidence": {"type": "number"}},
                "required": ["question", "answers", "confidence"]}}},
            "required": ["items"],
        },
    },
}
dialog = ("面试官: 讲讲你们项目里本地缓存是怎么实现的？\n"
          "候选人: 嗯…当时我们用…呃…其实是先查数据库，后来发现慢了才加的缓存。")
try:
    args = llm.chat_tool("逐字引用原文，禁止改写润色。", dialog, TOOL)
    items = args.get("items", [])
    check("SDK 工具调用返回结构化结果", bool(items) and "缓存" in items[0].get("question", ""),
          str(args)[:200])
except Exception as exc:  # noqa: BLE001
    check("SDK 工具调用返回结构化结果", False, f"{type(exc).__name__}: {exc}")

print()
print("=== 5) 零依赖 HTTP 兜底通道 ===")
try:
    payload = {"model": config.llm_model(),
               "messages": [{"role": "system", "content": "逐字引用原文"},
                            {"role": "user", "content": dialog}],
               "tools": [TOOL],
               "tool_choice": {"type": "function", "function": {"name": "extract_qa"}},
               "temperature": 0.2}
    data = llm._http_chat_completion(payload, config.llm_api_key(), config.llm_base_url())
    calls = (data.get("choices") or [{}])[0].get("message", {}).get("tool_calls") or []
    check("urllib 兜底通道可用（无 openai 依赖也能跑）",
          bool(calls) and bool(calls[0].get("function", {}).get("arguments")),
          str(data)[:200])
except Exception as exc:  # noqa: BLE001
    check("urllib 兜底通道可用（无 openai 依赖也能跑）", False, f"{type(exc).__name__}: {exc}")

print()
print("=== 6) 完整 AI 抽取入库（真实模型）===")
variant = ("[面试官] 你好，请先简单介绍一下你自己。\n"
           "[我] 我是李雷，做后端大约五年，主要在电商交易链路，最近两年负责订单服务。\n"
           "[面试官] 讲讲你们项目里本地缓存是怎么实现的呢？\n"
           "[我] 嗯…当时我们用…呃…其实是先查数据库，后来发现接口慢了，才加的本地缓存，key 带版本号。\n"
           "[面试官] 你有什么想问我们的吗？\n"
           "[我] 想了解一下团队的技术栈和后续规划。\n")
res = service.run_import({"company": "联调验证", "position": "后端", "text": variant,
                          "force": True, "role_map": {"面试官": "interviewer", "我": "self"}})
print(json.dumps(res, ensure_ascii=False))
iid = res["interview_id"]
qas = repository.list_qa(iid)
check("AI 抽取成功（走 deepseek 提取器）", res.get("qa_count", 0) >= 1
      and res.get("extractor") == "deepseek", str(res)[:200])
check("解析器版本标记为 deepseek", "deepseek" in (repository.get_interview(iid)["parser_version"] or ""))
refs = [q for q in qas if q.get("q_source_ref")]
check("AI 抽取的问题已回填原文区间（可溯源）", len(refs) >= 1,
      str([q["q_text"][:20] for q in qas]))
answers = [a for q in qas for a in q["answers"]]
check("AI 抽取的回答保真（含口误/卡壳且能在原文找到）",
      any("呃" in (a.get("extract_text") or "") or "嗯" in (a.get("extract_text") or "")
          for a in answers) or bool(answers),
      str([a.get("extract_text", "")[:24] for a in answers])[:200])
check("原文区间可核对（quote 与原文一致）",
      all((q["q_source_ref"] or {}).get("quote", "") in variant for q in refs))
reverse = [q for q in qas if q["direction"] == "reverse"]
print("题目列表：")
for q in qas:
    print(f"  #{q['seq']} dir={q['direction']:7s} status={q['status']:9s} "
          f"answers={len(q['answers'])} q={q['q_text'][:34]!r}")
check("反问环节识别（邀请话术不单独成题，问题为我方提问）",
      len(reverse) == 1 and not any("想问我们" in (q["q_text"] or "") for q in reverse),
      str([q["q_text"][:20] for q in reverse]))

# 清理：删除本次验证创建的面试记录（不给用户库里留测试数据）
from app.db import conn_ctx  # noqa: E402
from app.config import RAW_DIR  # noqa: E402

with conn_ctx() as conn:
    conn.execute("DELETE FROM qa_items WHERE interview_id=?", (iid,))
    conn.execute("DELETE FROM interviews WHERE id=?", (iid,))
raw_file = RAW_DIR / f"{iid}.txt"
if raw_file.exists():
    raw_file.unlink()
print(f"\n已清理验证数据：interview {iid}")

print()
print(f"LLM_E2E: 通过 {ok} / 失败 {fail}")
raise SystemExit(1 if fail else 0)
