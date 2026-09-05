"""全局配置与目录约定。

设计依据：docs/02-tech-stack-design.md 方案二（最简 MVP 栈）。
数据全部落在项目内 data/ 目录：备份 = 复制该目录。
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


def _load_dotenv() -> None:
    """极简 .env 加载（不引入额外依赖顺序依赖）。"""
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

LLM_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com").strip()
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat").strip()
EXTRACTOR_MODE = os.getenv("EXTRACTOR", "auto").strip().lower()


def extractor_mode() -> str:
    """决定本场使用的提取器：auto 时无 Key 回退 rule。"""
    mode = EXTRACTOR_MODE
    if mode in ("rule", "mock", "deepseek"):
        return mode
    return "deepseek" if LLM_API_KEY else "rule"


def extractor_label() -> str:
    mode = extractor_mode()
    return {
        "rule": "规则切分",
        "mock": "内置 Mock（规则实现，模拟 LLM 结构）",
        "deepseek": f"DeepSeek({LLM_MODEL})",
    }[mode]


def ensure_dirs() -> None:
    for d in (DATA_DIR, RAW_DIR, EXPORT_DIR):
        d.mkdir(parents=True, exist_ok=True)
