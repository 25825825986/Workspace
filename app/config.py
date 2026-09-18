"""全局配置与目录约定。

设计依据：docs/02-tech-stack-design.md 方案二（最简 MVP 栈）。
数据全部落在项目内 data/ 目录：备份 = 复制该目录。

Phase 5：LLM 相关配置改为**函数式读取**（设置页 > .env > 默认），
因此设置页保存后无需重启即生效（llm/extractor 选择每次都按最新设置解析）。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent          # 项目根（含 docs/ data/ app/）
APP_DIR = Path(__file__).resolve().parent
# 数据目录可用环境变量覆盖（测试用独立目录，避免误动真实数据与密钥）
DATA_DIR = Path(os.getenv("AI_REVIEW_DATA_DIR") or (BASE_DIR / "data"))
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
    return llm_base_url_info()["url"]


def llm_base_url_info() -> dict:
    """返回规范化后的 API 根地址与修正说明。

    本次实测遇到的误填：把模型路径抄进地址，例如
    `https://api.siliconflow.cn/v1/deepseek-ai/DeepSeek-V4-Flash`
    → 规范化后应是 `https://api.siliconflow.cn/v1`（否则请求会 404）。
    """
    raw = str((_settings().get("llm") or {}).get("base_url") or "").strip()
    if not raw:
        raw = os.getenv("LLM_BASE_URL", "").strip() or DEFAULT_BASE_URL
    url, note = normalize_base_url(raw)
    return {"url": url, "raw": raw, "note": note}


def normalize_base_url(raw: str) -> tuple[str, str | None]:
    """把用户填写的地址规整为 OpenAI 兼容 API 根地址。

    - 去掉 `/chat/completions`、`/completions`、`/models` 等端点后缀；
    - 若版本段（如 `/v1`）之后还跟着路径（通常是模型 ID），截断到版本段。
    """
    from urllib.parse import urlsplit, urlunsplit

    text = (raw or "").strip().rstrip("/")
    if not text:
        return DEFAULT_BASE_URL, None
    note: str | None = None

    for suffix in ("/chat/completions", "/completions", "/models", "/embeddings"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            note = f"已自动去掉端点后缀 {suffix}"

    parts = urlsplit(text)
    segments = [seg for seg in parts.path.split("/") if seg]
    version_idx = next((i for i, seg in enumerate(segments) if re.fullmatch(r"v\d+", seg)), None)
    if version_idx is not None and version_idx + 1 < len(segments):
        dropped = "/".join(segments[version_idx + 1:])
        segments = segments[: version_idx + 1]
        note = (f"已自动截断到 API 根地址（原地址里多出的 “{dropped}” 通常是模型 ID，"
                "请填在「模型名」中）")
    path = "/" + "/".join(segments) if segments else ""
    return urlunsplit((parts.scheme, parts.netloc, path, "", "")), note


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
    """顶栏模式徽标（FR-05.3）。

    徽标只说明「现在走哪条路」；具体模型名放进 title，不再用「·」把
    模型 ID 拼进顶栏（那是一串只有开发者才读的字符）。
    """
    mode = extractor_mode()
    if mode == "deepseek":
        return {"kind": "ai", "text": "AI 模式", "title": f"解析模型：{llm_model()}"}
    if mode == "mock":
        return {"kind": "mock", "text": "Mock 模式", "title": "本地规则模拟"}
    return {"kind": "off", "text": "非 AI 模式", "title": "纯本地规则，不联网"}


def has_llm() -> bool:
    return bool(llm_api_key())


def ensure_dirs() -> None:
    for d in (DATA_DIR, RAW_DIR, EXPORT_DIR):
        d.mkdir(parents=True, exist_ok=True)
