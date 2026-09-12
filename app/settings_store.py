"""应用设置持久化（Phase 5 / Q8）。

- `data/settings.json`：普通设置（主题、AI 模式、模型接入参数）
- `data/secrets.json`：仅 API Key（独立文件，便于权限/备份管理，不随设置一起分享）
- 写入一律「临时文件 + os.replace」原子替换，避免中断写坏配置
- 读取优先级：设置文件（UI） > 环境变量（.env） > 内置默认

> 说明：本模块自行推导 data/ 路径（不 import config），以避免与 config 形成循环依赖。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
# 与 config.DATA_DIR 同一口径：环境变量可覆盖（自动化测试用独立目录，避免误动真实数据与密钥）
DATA_DIR = Path(os.getenv("AI_REVIEW_DATA_DIR") or (BASE_DIR / "data"))
SETTINGS_PATH = DATA_DIR / "settings.json"
SECRETS_PATH = DATA_DIR / "secrets.json"

THEMES = ("light", "dark", "system")
AI_MODES = ("auto", "on", "off")          # 自动（有 Key 用 AI） / 强制 AI / 非 AI（离线）

DEFAULTS: dict[str, Any] = {
    "theme": "system",
    "ai_mode": "auto",
    "llm": {
        "base_url": "",                   # 空 = 用 .env / 内置默认
        "model": "",
    },
}


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _deep_merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


# ---------- 读取 ----------

def load() -> dict:
    """当前设置（已与默认值合并，读取失败时回退默认）。"""
    merged = _deep_merge(DEFAULTS, _read_json(SETTINGS_PATH))
    if merged.get("theme") not in THEMES:
        merged["theme"] = DEFAULTS["theme"]
    if merged.get("ai_mode") not in AI_MODES:
        merged["ai_mode"] = DEFAULTS["ai_mode"]
    merged["llm"] = _deep_merge(DEFAULTS["llm"], merged.get("llm") or {})
    return merged


def get(key: str, default: Any = None) -> Any:
    return load().get(key, default)


def api_key() -> str:
    """API Key：设置文件（UI）优先，其次 .env。"""
    key = str(_read_json(SECRETS_PATH).get("api_key") or "").strip()
    return key or os.getenv("DEEPSEEK_API_KEY", "").strip()


def masked_key() -> str:
    key = api_key()
    if not key:
        return ""
    if len(key) <= 8:
        return key[:2] + "****"
    return f"{key[:4]}****{key[-4:]}"


def data_dir() -> str:
    return str(DATA_DIR)


# ---------- 写入 ----------

def save(patch: dict) -> dict:
    """合并保存设置（原子写），返回最新设置。"""
    current = load()
    merged = _deep_merge(current, patch or {})
    if merged.get("theme") not in THEMES:
        merged["theme"] = DEFAULTS["theme"]
    if merged.get("ai_mode") not in AI_MODES:
        merged["ai_mode"] = DEFAULTS["ai_mode"]
    # 仅持久化已知键，避免脏字段堆积
    payload = {
        "theme": merged["theme"],
        "ai_mode": merged["ai_mode"],
        "llm": {
            "base_url": str(merged["llm"].get("base_url") or "").strip(),
            "model": str(merged["llm"].get("model") or "").strip(),
        },
    }
    _atomic_write(SETTINGS_PATH, payload)
    return load()


def set_api_key(key: str | None) -> None:
    """保存/清除 API Key（空字符串 = 清除）。"""
    key = (key or "").strip()
    _atomic_write(SECRETS_PATH, {"api_key": key} if key else {})


def reset() -> dict:
    """恢复默认设置（不动 API Key）。"""
    _atomic_write(SETTINGS_PATH, {})
    return load()
