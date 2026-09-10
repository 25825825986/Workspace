"""业务编排：导入向导的"分析预览"与"创建+解析"全流程。

Phase 4 变更：
- FR-01：新增 地点/面试时间/期望薪资/时长 字段；标题 5 级降级生成 + 手动覆盖；
- 重复导入检测（原文指纹）；无标签文本按段落合并后按角色归并；
- 提取不到任何问答时给出明确错误（不再静默创建空记录）。
"""
from __future__ import annotations

import datetime
import hashlib
import re

from . import repository
from . import config
from .config import RAW_DIR, ensure_dirs
from .pipeline import extract as extract_mod
from .pipeline import postprocess, preprocess
from .util import clean_role, now, uid

ROLE_LABEL = {"interviewer": "面试官", "self": "我(候选人)", "other": "其他", "unknown": "待定"}
FORMAT_LABEL = {"bracket": "中括号标签 [面试官]", "colon": "冒号标签 面试官：",
                "ab": "字母标签 A/B", "speaker": "Speaker 0/1", "none": "无标签"}

_DURATION_HELP = "可填 58 / 58分钟 / 1小时30分 / 1.5小时"


class UsageError(RuntimeError):
    """输入/流程错误，转为 HTTP 400 并显示友好信息。"""


class DuplicateImportError(UsageError):
    """疑似重复导入：同一份转写原文已存在（Phase 4）。"""

    def __init__(self, existing: dict):
        self.existing = existing or {}
        title = self.existing.get("title") or "（无标题）"
        super().__init__(f"疑似重复导入：已存在《{title}》"
                         f"（{self.existing.get('created_at') or '时间未知'}）")


# ---------- 元信息解析（FR-01） ----------

def transcript_hash(text: str) -> str:
    return hashlib.sha256((text or "").strip().encode("utf-8")).hexdigest()[:16]


def parse_duration_minutes(raw) -> tuple[int | None, str | None]:
    """把"时长"输入解析为分钟，返回 (分钟, 警告信息)。空输入 → (None, None)。

    支持：58 / 58分钟 / 1h30m / 1.5小时 / 1小时30分（全角数字自动归一）。
    """
    if raw is None:
        return None, None
    s = str(raw).strip()
    if not s:
        return None, None
    s = s.translate(str.maketrans("０１２３４５６７８９．", "0123456789.")).replace(" ", "").lower()
    if re.fullmatch(r"\d+(\.\d+)?", s):
        minutes = round(float(s))
    else:
        m = re.fullmatch(
            r"(?:(\d+(?:\.\d+)?)(?:小时|个小时|h|hr|hrs|hour|hours))?"
            r"(?:(\d+(?:\.\d+)?)(?:分钟|分|min|mins|minute|minutes|m))?", s)
        if not m or (m.group(1) is None and m.group(2) is None):
            return None, f"无法识别时长「{raw}」，已按留空处理（{_DURATION_HELP}）"
        minutes = round(float(m.group(1) or 0) * 60 + float(m.group(2) or 0))
    if minutes <= 0 or minutes > 24 * 60:
        return None, f"时长「{raw}」超出合理范围（1–1440 分钟），已按留空处理"
    return minutes, None


def parse_interview_at(raw: str | None) -> datetime.datetime:
    """解析面试时间为 datetime；空值 → 当前时间（FR-01 默认值）。"""
    s = (raw or "").strip()
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(s, fmt)
        except ValueError:
            continue
    return datetime.datetime.now()


def build_title(company: str | None, position: str | None,
                interview_at: str | None) -> str:
    """标题 5 级降级生成（FR-01.2）；用户手动输入时以手动值为准。"""
    company = (company or "").strip()
    position = (position or "").strip()
    has_time = bool((interview_at or "").strip())
    dt = parse_interview_at(interview_at)
    ymd = dt.strftime("%Y-%m-%d")
    hm = dt.strftime("%H:%M")
    if company and position:
        return f"{company}-{position}-{ymd} {hm}" if has_time else f"{company}-{position}-{ymd}"
    if company:
        return f"{company}-面试-{ymd}"
    if has_time:
        return f"面试-{ymd} {hm}"
    return f"未命名面试-{dt.strftime('%Y%m%d-%H%M')}"


def title_preview(payload: dict) -> dict:
    """给导入页"重算标题"按钮用。"""
    return {"title": build_title(payload.get("company"), payload.get("position"),
                                 payload.get("interview_at"))}


# ---------- 分析预览 ----------

def _default_role_map(text: str, fmt: str) -> dict[str, dict]:
    """给每个说话人标签一个"默认角色 + 来源 + 置信度"（不做超出规则的猜测）。"""
    turns = preprocess.parse_turns(text, fmt)
    out: dict[str, dict] = {}
    for label in preprocess.ordered_labels(turns):
        base = preprocess.alias_role(label)
        if base:
            out[label] = {"role": base, "source": "label", "confidence": 0.9}
        elif fmt == "none":
            joined = "\n".join(t["text"] for t in turns if t["label"] == label)
            role, conf = preprocess.heuristic_role(joined)
            out[label] = {"role": role, "source": "inferred", "confidence": conf}
        else:
            out[label] = {"role": "unknown", "source": "label", "confidence": 0.5}
    return out


def analyze(text: str) -> dict:
    """导入向导第②步：格式嗅探 + 说话人默认角色预览（供人工确认/纠正，R1）。"""
    if not text or not text.strip():
        raise UsageError("转写文本不能为空")
    fmt = preprocess.detect_format(text)
    turns = preprocess.parse_turns(text, fmt)
    if not turns:
        raise UsageError("未能从文本中切分出任何内容，请检查转写文本格式")
    defaults = _default_role_map(text, fmt)
    speakers = []
    for label, info in defaults.items():
        sample = ""
        for t in turns:
            if t["label"] == label:
                sample = t["text"]
                break
        speakers.append({"label": label, "role": info["role"], "source": info["source"],
                         "confidence": info["confidence"],
                         "sample": (sample[:80] + "…") if len(sample) > 80 else sample})
    # 重复导入提示（不阻断分析，只在创建时拦截）
    dup = repository.find_interview_by_hash(transcript_hash(text))
    return {"format": fmt, "format_label": FORMAT_LABEL.get(fmt, fmt),
            "chars": len(text), "turns": len(turns),
            "extractor": config.extractor_mode(), "extractor_label": config.extractor_label(),
            "duplicate": dup, "speakers": speakers}


# ---------- 导入执行 ----------

def run_import(payload: dict) -> dict:
    """创建面试记录并执行：预处理 → 提取 → 组装入库。

    payload：
      text（必需）；role_map: {label: role}；
      company/position/location/interview_at/expected_salary/duration/title/interview_type；
      force=True 时忽略重复导入提示。
    """
    text = (payload.get("text") or "").strip()
    if not text:
        raise UsageError("转写文本不能为空")
    ensure_dirs()

    # 0) 重复导入检测（原文指纹）
    text_hash = transcript_hash(text)
    if not payload.get("force"):
        existing = repository.find_interview_by_hash(text_hash)
        if existing:
            raise DuplicateImportError(existing)

    fmt = preprocess.detect_format(text)
    turns = preprocess.parse_turns(text, fmt)
    if not turns:
        raise UsageError("未能从文本中切分出任何内容")

    # 1) 角色确定：用户纠正 > 标签别名 > 无标签启发式（低置信）
    user_map = {str(k).strip(): clean_role(v)
                for k, v in (payload.get("role_map") or {}).items()}
    defaults = _default_role_map(text, fmt)
    final_roles: dict[str, str] = {}
    for label in preprocess.ordered_labels(turns):
        role = user_map.get(label) or defaults[label]["role"]
        if role is None:
            role = "unknown"
        final_roles[label] = role

    interviewer_ok = any(r == "interviewer" for r in final_roles.values())
    self_ok = any(r == "self" for r in final_roles.values())
    if not (interviewer_ok and self_ok):
        raise UsageError(
            "尚未同时确定「面试官」与「我(候选人)」两个角色。"
            "无标签文本请在向导中逐段指定，或配置 DEEPSEEK_API_KEY 后由 AI 推断。")

    # 2) LLM 路径对无标签文本的增强角色推断
    mode = config.extractor_mode()
    extractor = extract_mod.make_extractor(mode)
    if mode == "deepseek" and fmt == "none" and extractor.name == "deepseek":
        inferred = extractor.infer_roles(turns)  # {turn_idx: role}
        for t in turns:
            if final_roles.get(t["label"]) == "unknown":
                role = inferred.get(str(t["idx"]))
                if role in ("interviewer", "self", "other"):
                    final_roles[t["label"]] = role

    role_of = {label: (r if r in ("interviewer", "self", "other") else "unknown")
               for label, r in final_roles.items()}

    # 2.5) 无标签文本：按自然段 + 角色归并，保证问答切分可用（Phase 4）
    if fmt == "none":
        turns = preprocess.merge_by_role(turns, role_of, text)

    # 3) 提取（规则/mock/DeepSeek）
    items = extractor.extract(turns, role_of)
    if not items:
        raise UsageError("未能提取到任何问答：请确认文本中包含面试官提问与我的回答，"
                         "并检查说话人角色是否指定正确。")
    # 无标签文本的角色来自低置信推断 → 统一降为待确认，避免"看起来确定"（R1）
    if fmt == "none":
        for it in items:
            it["confidence"] = min(float(it.get("confidence") or 0.5), 0.7)

    # 4) 组装 + 知识点落库 + 写入 qa_items
    speakers = preprocess.speaker_meta(turns, role_of, fmt)
    speaker_of_label = {s["label"]: s["id"] for s in speakers}
    records = postprocess.assemble(items, turns, role_of, speaker_of_label)

    warnings: list[str] = []
    duration_minutes, duration_warn = parse_duration_minutes(payload.get("duration"))
    if duration_warn:
        warnings.append(duration_warn)

    company = (payload.get("company") or "").strip()
    position = (payload.get("position") or "").strip()
    location = (payload.get("location") or "").strip()
    expected_salary = (payload.get("expected_salary") or "").strip()
    dt = parse_interview_at(payload.get("interview_at"))
    interview_at = dt.strftime("%Y-%m-%dT%H:%M")
    date = dt.strftime("%Y-%m-%d")
    title = (payload.get("title") or "").strip() or \
        build_title(company, position, payload.get("interview_at"))

    ts = now()
    iid = uid()
    (RAW_DIR / f"{iid}.txt").write_text(text, encoding="utf-8")
    repository.create_interview({
        "id": iid, "title": title, "company": company or None,
        "position": position or None, "location": location or None,
        "interview_at": interview_at, "expected_salary": expected_salary or None,
        "duration_minutes": duration_minutes, "transcript_hash": text_hash,
        "interview_type": payload.get("interview_type") or "mixed",
        "date": date, "raw_transcript": text, "transcript_format": fmt,
        "status": "review", "parser_version": f"{extractor.name}@{ts}",
        "speaker_map": speakers, "created_at": ts, "updated_at": ts,
    })

    db_records = []
    for r in records:
        cat = repository.get_or_create_category(r.pop("category_suggestion"))
        r.update({"category_id": cat["id"]})
        db_records.append(r)
    repository.replace_interview_qa(iid, db_records)

    pending = sum(1 for r in db_records if r["status"] == "pending")
    blank = sum(1 for r in db_records if not r["answers"])
    return {"interview_id": iid, "qa_count": len(db_records), "pending_count": pending,
            "blank_answer_count": blank, "format": fmt, "extractor": mode,
            "title": title, "duration_minutes": duration_minutes, "warnings": warnings}
