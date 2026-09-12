"""模型 API 配置诊断（不修改任何数据）。

用途：一键查清「为什么 AI 调不通」——配置解析、地址规范化、模型列表校验、最小对话测试。
用法：
    python scripts/llm_diag.py
退出码 0 = 全部正常。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app.pipeline import llm  # noqa: E402


def mask(key: str) -> str:
    if not key:
        return "（未配置）"
    return f"{key[:4]}****{key[-4:]}" if len(key) > 8 else "****"


def main() -> int:
    info = config.llm_base_url_info()
    print("=== 配置解析（应用实际读取到的值）===")
    print("ai_mode        :", config.ai_mode(), "（auto=有 Key 用 AI / on=强制 AI / off=非 AI）")
    print("extractor_mode :", config.extractor_mode())
    print("api_key        :", mask(config.llm_api_key()))
    print("base_url 原始  :", info["raw"])
    print("base_url 生效  :", info["url"])
    if info.get("note"):
        print("地址修正说明  :", info["note"])
    print("model          :", config.llm_model())
    print("has_llm        :", config.has_llm())
    if not config.has_llm():
        print("\n结论：未配置 API Key —— 请在「设置 → 模型 API」填写，或改用非 AI 模式。")
        return 1

    print("\n=== 平台模型列表校验 ===")
    listing = llm.list_models()
    if listing.get("ok"):
        ids = listing.get("models", [])
        print(f"共 {len(ids)} 个模型；配置的模型是否存在：{config.llm_model() in ids}")
        if config.llm_model() not in ids:
            similar = [m for m in ids if config.llm_model().split("/")[-1].lower() in m.lower()][:8]
            print("相近候选：", ", ".join(similar) or "（无）")
    else:
        print("拉取失败：", listing.get("message"))

    print("\n=== 最小对话测试 ===")
    result = llm.test_connection()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
