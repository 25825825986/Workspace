"""业务编排：导入向导的"分析预览"与"创建+解析"全流程。"""
from __future__ import annotations

import datetime

from . import repository
from .config import RAW_DIR, extractor_label, extractor_mode, ensure_dirs
from .pipeline import extract as extract_mod
from .pipeline import postprocess, preprocess
from .util import clean_role, now, uid

ROLE_LABEL = {"interviewer": "面试官", "self": "我(候选人)", "other": "其他", "unknown": "待定"}
FORMAT_LABEL = {"bracket": "中括号标签 [面试官]", "colon": "冒号标签 面试官：",
                "ab": "字母标签 A/B", "speaker": "Speaker 0/1", "none": "无标签"}


class UsageError(RuntimeError):
    """输入/流程错误，转为 HTTP 400 并显示友好信息。"""


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
    return {"format": fmt, "format_label": FORMAT_LABEL.get(fmt, fmt),
            "chars": len(text), "turns": len(turns),
            "extractor": extractor_mode(), "extractor_label": extractor_label(),
            "speakers": speakers}


def run_import(payload: dict) -> dict:
    """创建面试记录并执行：预处理 → 提取 → 组装入库。

    payload 必需：text；role_map: {label: role}（用户在向导中确认/纠正后的结果）。
    可选：company/position/interview_type/date/title。
    """
    text = (payload.get("text") or "").strip()
    if not text:
        raise UsageError("转写文本不能为空")
    ensure_dirs()

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
    mode = extractor_mode()
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

    # 3) 提取（规则/mock/DeepSeek）
    items = extractor.extract(turns, role_of)

    # 4) 组装 + 知识点落库 + 写入 qa_items
    speakers = preprocess.speaker_meta(turns, role_of, fmt)
    speaker_of_label = {s["label"]: s["id"] for s in speakers}
    records = postprocess.assemble(items, turns, role_of, speaker_of_label)

    ts = now()
    company = (payload.get("company") or "").strip()
    position = (payload.get("position") or "").strip()
    date = (payload.get("date") or "").strip() or datetime.date.today().isoformat()
    title = (payload.get("title") or "").strip() or \
        f"{company or '未命名公司'}-{position or '岗位'}-{date}"

    iid = uid()
    (RAW_DIR / f"{iid}.txt").write_text(text, encoding="utf-8")
    iv = {
        "id": iid, "title": title, "company": company or None,
        "position": position or None,
        "interview_type": payload.get("interview_type") or "mixed",
        "date": date, "raw_transcript": text, "transcript_format": fmt,
        "status": "review", "parser_version": f"{extractor.name}@{ts}",
        "speaker_map": speakers, "created_at": ts, "updated_at": ts,
    }
    repository.create_interview(iv)

    db_records = []
    for r in records:
        cat = repository.get_or_create_category(r.pop("category_suggestion"))
        r.update({"category_id": cat["id"]})
        db_records.append(r)
    repository.replace_interview_qa(iid, db_records)

    pending = sum(1 for r in db_records if r["status"] == "pending")
    return {"interview_id": iid, "qa_count": len(db_records), "pending_count": pending,
            "format": fmt, "extractor": mode, "title": title}
