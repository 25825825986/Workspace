"""全局配置与目录约定。

设计依据：docs/02-tech-stack-design.md 方案二（最简 MVP 栈）。
数据全部落在项目内 data/ 目录：备份 = 复制该目录。

Phase 5：LLM 相关配置改为**函数式读取**（设置页 > .env > 默认），
因此设置页保存后无需重启即生效（llm/extractor 选择每次都按最新设置解析）。
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent          # 项目根（含 docs/ data/ app/）
APP_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"                                # data/raw/<interview_id>.txt 原文存档
EXPORT_DIR = DATA_DIR / "export"                          # data/export/*.md 导出产物
DB_PATH = DATA_DIR / "app.db"                             # SQLite 单文件库
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"

APP_VERSION = "0.5.0"      # Phase 5


def _load_dotenv() -> None:
    """极简 .env 加载（.env 仅作为设置页未配置时的兜底）。"""
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()


def _settings() -> dict:
    from .settings_store import load          # 延迟导入，避免循环依赖
    return load()


def llm_api_key() -> str:
    """API Key：设置页保存的值优先，其次 .env。"""
    from .settings_store import api_key
    return api_key()


def llm_base_url() -> str:
    value = str((_settings().get("llm") or {}).get("base_url") or "").strip()
    return value or os.getenv("LLM_BASE_URL", "").strip() or DEFAULT_BASE_URL


def llm_model() -> str:
    value = str((_settings().get("llm") or {}).get("model") or "").strip()
    return value or os.getenv("LLM_MODEL", "").strip() or DEFAULT_MODEL


def ai_mode() -> str:
    """auto | on | off（设置页口径）。"""
    mode = str(_settings().get("ai_mode") or "").strip().lower()
    if mode in ("auto", "on", "off"):
        return mode
    # 兼容 .env 中的旧口径 EXTRACTOR=rule|mock|deepseek|auto
    env = os.getenv("EXTRACTOR", "auto").strip().lower()
    return {"rule": "off", "mock": "auto", "deepseek": "on", "auto": "auto"}.get(env, "auto")


def extractor_mode() -> str:
    """解析出实际使用的提取器：rule | mock | deepseek。"""
    mode = ai_mode()
    if mode == "off":
        return "rule"
    if mode == "on":
        return "deepseek"
    return "deepseek" if llm_api_key() else "rule"


def extractor_label() -> str:
    mode = extractor_mode()
    return {
        "rule": "规则切分",
        "mock": "内置 Mock（规则实现，模拟 LLM 结构）",
        "deepseek": f"DeepSeek({llm_model()})",
    }[mode]


def mode_badge() -> dict:
    """顶栏模式徽标（FR-05.3）。"""
    mode = extractor_mode()
    if mode == "deepseek":
        return {"kind": "ai", "text": f"AI 模式 · {llm_model()}"}
    if mode == "mock":
        return {"kind": "mock", "text": "Mock 模式（本地规则模拟）"}
    return {"kind": "off", "text": "非 AI 模式（本地规则）"}


def has_llm() -> bool:
    return bool(llm_api_key())


def ensure_dirs() -> None:
    for d in (DATA_DIR, RAW_DIR, EXPORT_DIR):
        d.mkdir(parents=True, exist_ok=True)
