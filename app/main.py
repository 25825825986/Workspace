"""AI_Review Web 入口（Flask + Jinja2，方案二最简栈）。

页面：工作台 / 导入向导 / 问答列表 / 单题复盘 / 面经库 / 导出预览
API ：/api/analyze、/api/interviews、/api/qa/*、merge/confirm 等

> 备注：设计文档 docs/02 原写 FastAPI；因本机离线环境内置 Flask 3.1（无 fastapi/uvicorn
> 且无法联网安装），第三阶段按 Flask 落地（同属方案二 Python 最简栈，模板与逻辑不变，
> 迁回 FastAPI 仅需替换路由层）。详见 docs/03。
"""
from __future__ import annotations

import re
from urllib.parse import quote

from flask import (Flask, Response, abort, jsonify, redirect, render_template, request)

from . import config, exporter, knowledge, repository, service, settings_store
from .config import (APP_VERSION, DB_PATH, EXPORT_DIR, ensure_dirs, extractor_label,
                     extractor_mode)
from .db import init_db
from .migrations import run_migrations
from .pipeline import llm as llm_mod
from .pipeline import mock_gen, resume as resume_mod, similar
from .pipeline.llm import LLMError
from .service import ROLE_LABEL, DuplicateImportError, UsageError

app = Flask(__name__)
app.json.ensure_ascii = False  # API 返回原生 UTF-8（中文可读）

INTERVIEW_TYPES = {"technical": "技术面", "behavior": "行为面", "hr": "HR面",
                   "mixed": "综合面", "mock": "模拟面"}
STATUS_LABEL = {"auto": "机器初提", "pending": "待确认", "confirmed": "已确认",
                "corrected": "已修正"}

# 首次启动：准备目录 + 建表 + 幂等增量迁移（Phase 4）
ensure_dirs()
init_db()
run_migrations()


@app.context_processor
def _inject_globals():
    settings = settings_store.load()
    return {"extractor_label": extractor_label(), "extractor_mode": extractor_mode(),
            "theme": settings.get("theme", "system"),
            "mode_badge": config.mode_badge(),
            "ai_mode": config.ai_mode(),
            "has_api_key": config.has_llm(),
            "app_version": APP_VERSION,
            "interview_types": INTERVIEW_TYPES, "status_label": STATUS_LABEL}


@app.template_filter("duration")
def _fmt_duration(minutes):
    """58 → 58 分钟；90 → 1 小时 30 分（Phase 4 时长展示）。"""
    if not minutes:
        return ""
    m = int(minutes)
    h, mm = divmod(m, 60)
    if h and mm:
        return f"{h} 小时 {mm} 分"
    if h:
        return f"{h} 小时"
    return f"{mm} 分钟"


@app.template_filter("dt")
def _fmt_dt(value):
    """2024-05-20T14:30 → 2024-05-20 14:30。"""
    if not value:
        return ""
    return str(value).replace("T", " ")[:16]


@app.errorhandler(404)
def _not_found(_e):
    return render_template("404.html"), 404


def _speaker_map_of(iv: dict) -> dict:
    return {sp["id"]: ROLE_LABEL.get(sp["role"], sp.get("label", sp["id"]))
            for sp in (iv or {}).get("speaker_map", [])}


# ---------- 页面 ----------

@app.route("/")
def page_index():
    """面试记录页（FR-02）：卡片式列表 + 搜索/快筛/排序 + 统计条。"""
    search = request.args.get("q") or ""
    sort = request.args.get("sort") or "recent"
    only = request.args.get("only") or ""
    if sort not in ("recent", "interview_at", "qa_count", "pending"):
        sort = "recent"
    if only not in ("pending", "merged", "not_merged"):
        only = ""
    return render_template("index.html",
                           interviews=repository.list_interviews(search=search, sort=sort, only=only),
                           stats=repository.dashboard_stats(),
                           search=search, sort=sort, only=only)


@app.route("/import")
def page_import():
    return render_template("import.html")


@app.route("/interviews/<iid>")
def page_review_list(iid: str):
    iv = repository.get_interview(iid)
    if not iv:
        abort(404)
    qas = repository.list_qa(iid)
    pending = sum(1 for q in qas if q["status"] in ("auto", "pending"))
    return render_template("review_list.html", iv=iv, qas=qas, pending=pending,
                           speaker=_speaker_map_of(iv),
                           categories=repository.list_categories(),
                           fmt_label=service.FORMAT_LABEL.get(iv.get("transcript_format", "none")))


@app.route("/interviews/<iid>/qa/<qid>")
def page_review_detail(iid: str, qid: str):
    iv = repository.get_interview(iid)
    qa = repository.get_qa(qid)
    if not iv or not qa or qa["interview_id"] != iid:
        abort(404)
    all_qas = repository.list_qa(iid)
    pos = next((i for i, q in enumerate(all_qas) if q["id"] == qid), 0)
    prev_q = all_qas[pos - 1] if pos > 0 else None
    next_q = all_qas[pos + 1] if pos + 1 < len(all_qas) else None
    return render_template("review_detail.html", iv=iv, qa=qa, all_qas=all_qas,
                           pos=pos, prev=prev_q, next=next_q,
                           speaker=_speaker_map_of(iv),
                           categories=repository.list_categories())


@app.route("/bank")
def page_bank():
    """兼容旧链接：知识库已迁到 /knowledge。"""
    return redirect("/knowledge")


@app.route("/knowledge")
def page_knowledge():
    """知识库（Phase 6）：三专题置顶 + 一级分类分组 + 频次 + 相似关联入口 + 检索。"""
    search = (request.args.get("q") or "").strip()
    entries = repository.list_entries()
    # 懒补：历史条目缺分类时按规则即时归类（幂等，只补一次）
    for e in entries:
        if not e.get("group_name"):
            topic, group = knowledge.classify(e.get("head_question", ""),
                                              e.get("category_name", ""),
                                              e.get("category_type", ""))
            repository.set_entry_classification(e["id"], topic, group)
            e["topic"], e["group_name"] = topic, group
    if search:
        low = search.lower()
        entries = [e for e in entries
                   if low in (e.get("head_question") or "").lower()
                   or low in (e.get("category_name") or "").lower()
                   or any(low in (v or "").lower() for v in (e.get("question_variants") or []))]

    pair_map: dict[str, list[dict]] = {}
    for p in repository.list_all_similar_pairs():
        pair_map.setdefault(p["a_entry_id"], []).append(p)
        pair_map.setdefault(p["b_entry_id"], []).append(p)

    def decorate(e: dict) -> dict:
        pairs = pair_map.get(e["id"], [])
        suspected = [p for p in pairs if (p.get("reason") or "").startswith("疑似")]
        e["similar_count"] = len(pairs)
        e["similar_suspected"] = len(suspected)
        e["similar_confirmed"] = len(pairs) - len(suspected)
        e["similar_ids"] = [p["b_entry_id"] if p["a_entry_id"] == e["id"] else p["a_entry_id"]
                            for p in pairs]
        return e

    topics = []
    for key, label, desc in knowledge.TOPICS:
        items = [decorate(e) for e in entries if (e.get("topic") or "") == key]
        if items:
            topics.append({"key": key, "label": label, "desc": desc, "entries": items,
                           "total_ask": sum(i["ask_times"] for i in items)})
    groups = []
    for name in knowledge.GROUPS:
        items = [decorate(e) for e in entries if (e.get("group_name") or "其他") == name]
        if items:
            groups.append({"name": name, "entries": items,
                           "total_ask": sum(i["ask_times"] for i in items)})
    return render_template("knowledge.html", topics=topics, groups=groups,
                           stats=repository.entry_stats(), search=search,
                           similar_pairs=repository.similar_pair_count(),
                           ai_available=config.extractor_mode() == "deepseek")


@app.route("/knowledge/compare/<entry_id>")
def page_knowledge_compare(entry_id: str):
    """相似问题完整对比（Phase 6）：同一考点的多次作答并列。"""
    entry = repository.get_entry(entry_id)
    if not entry:
        abort(404)
    neighbors = similar.neighbors(entry_id)
    # 本条目历史作答明细（来源面试、忠实原文、优化建议、批注）
    rows = repository.list_qa_detail(entry["source_qa_ids"])
    for r in rows:
        r["is_best"] = (entry.get("best_qa_id") == r["id"])
    return render_template("compare.html", entry=entry, neighbors=neighbors, rows=rows,
                           topics=knowledge.TOPIC_LABELS)


@app.route("/interviews/<iid>/export")
def page_export_interview(iid: str):
    iv = repository.get_interview(iid)
    if not iv:
        abort(404)
    md = exporter.interview_to_md(iid)
    exporter.save_export(f"面试复盘-{iv['title']}-{iid}.md", md)
    return render_template("export.html", md=md, kind="interview", iid=iid, iv=iv)


@app.route("/bank/export")
def page_export_bank():
    md = exporter.bank_to_md()
    exporter.save_export("面经库快照.md", md)
    return render_template("export.html", md=md, kind="bank")


# ---------- 导出下载 ----------

# Bug 修复（见 docs/bug_fix.md）：Content-Disposition 必须可被 latin-1 编码，
# 中文文件名不能直接进响应头（Werkzeug ≥3.1 序列化时抛错，导致连接挂起 0 字节）。
# 方案：RFC 6266 —— filename= 放 ASCII 回退名；filename*=UTF-8'' 放百分号编码的真实名。

_ILLEGAL_FN_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_NON_ASCII = re.compile(r"[^\x20-\x7e]+")
_MAX_FN_LEN = 150


def _sanitize_filename(name: str, fallback: str) -> str:
    """清理为合法下载文件名：换行/控制/非法字符替换，去头尾点与空格，截断过长。"""
    name = (name or "").strip().replace("\r", " ").replace("\n", " ")
    name = _ILLEGAL_FN_CHARS.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    name = name[:_MAX_FN_LEN].strip(" .")
    return name or fallback


def _content_disposition(utf8_name: str, ascii_fallback: str) -> str:
    """生成 latin-1 安全的 Content-Disposition（RFC 6266 + RFC 5987）。

    - 文件名本身全 ASCII → filename= 直接用（保持友好可读）；
    - 含非 ASCII（中文等）→ filename= 用 ASCII 回退名，真实名走 filename*=UTF-8''。
    """
    real = _sanitize_filename(utf8_name, ascii_fallback)
    try:
        real.encode("ascii")
        filename_part = real
    except UnicodeEncodeError:
        filename_part = ascii_fallback
    return f'attachment; filename="{filename_part}"; filename*=UTF-8\'\'{quote(real, safe="")}'


def _download_md(md: str, utf8_name: str, ascii_fallback: str) -> Response:
    # Werkzeug 对 text/* 且未带 charset 的 mimetype 会自动补 charset=utf-8，
    # 因此这里只传 mimetype，避免出现重复的 charset 参数。
    return Response(md, mimetype="text/markdown",
                    headers={"Content-Disposition": _content_disposition(utf8_name, ascii_fallback)})


@app.route("/download/interviews/<iid>.md")
def download_interview_md(iid: str):
    iv = repository.get_interview(iid)
    if not iv:
        abort(404)  # 顺带修复：不存在的 id 不再触发 500
    md = exporter.interview_to_md(iid)
    exporter.save_export(f"面试复盘-{iv['title']}-{iid}.md", md)
    return _download_md(md, f"面试复盘-{iv['title']}.md", f"review_{iid}.md")


@app.route("/download/bank.md")
def download_bank_md():
    md = exporter.bank_to_md()
    exporter.save_export("面经库快照.md", md)
    return _download_md(md, "面经库快照.md", "bank_snapshot.md")


# ---------- API ----------

@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(service.analyze((data or {}).get("text") or ""))
    except UsageError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/title-preview", methods=["POST"])
def api_title_preview():
    """FR-01：导入页「按规则重算标题」。"""
    return jsonify(service.title_preview(request.get_json(silent=True) or {}))


@app.route("/api/interviews", methods=["POST"])
def api_create_interview():
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(service.run_import(data))
    except DuplicateImportError as e:
        # 409：由前端弹出确认，用户可选「仍然导入」(force=True)
        return jsonify({"error": str(e), "duplicate": True, "existing": e.existing}), 409
    except UsageError as e:
        return jsonify({"error": str(e)}), 400
    except LLMError as e:
        return jsonify({"error": f"AI 解析失败：{e}（可改用非 AI 模式，或检查设置中的 API 配置）"}), 400
    except Exception as e:  # 其它未预期错误
        return jsonify({"error": f"解析失败：{e}"}), 500


def _update_qa_common(qid: str, data: dict):
    qa = repository.get_qa(qid)
    if not qa:
        raise UsageError("问答条目不存在")
    fields: dict = {}
    if "annotation" in data:
        fields["annotation"] = (data.get("annotation") or "").strip()
    if "optimization" in data:
        fields["optimization"] = (data.get("optimization") or "").strip()
    if "status" in data:
        st = str(data["status"]).strip()
        if st not in ("auto", "pending", "confirmed", "corrected"):
            raise UsageError("非法的状态值")
        fields["status"] = st
    if "category_id" in data:
        fields["category_id"] = data.get("category_id") or None
    # 单条回答修正（红线 R2：只产生 corrected 副本，原始提取保留）
    fix_idx = data.get("fix_answer_idx")
    if fix_idx is not None:
        answers = list(qa.get("answers") or [])
        idx = int(fix_idx)
        if not (0 <= idx < len(answers)):
            raise UsageError("回答序号越界")
        text = (data.get("fix_answer_text") or "").strip()
        if text:
            answers[idx]["corrected_text"] = text
            answers[idx]["is_corrected"] = True
        else:
            answers[idx]["corrected_text"] = None
            answers[idx]["is_corrected"] = False
        fields["answers"] = answers
        fields["status"] = "corrected" if text else "confirmed"
    # 问题修正（同语义）
    fix_q = data.get("fix_question_text")
    if fix_q is not None:
        q_text = (fix_q or "").strip()
        if q_text:
            fields["q_corrected_text"] = q_text
            fields["q_is_corrected"] = True
        else:
            fields["q_corrected_text"] = None
            fields["q_is_corrected"] = False
        fields["status"] = "corrected" if q_text else "confirmed"
    repository.update_qa(qid, fields)


@app.route("/api/qa/<qid>/update", methods=["POST"])
def api_update_qa(qid: str):
    try:
        _update_qa_common(qid, request.get_json(silent=True) or {})
        return jsonify({"ok": True})
    except UsageError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/qa/<qid>/confirm", methods=["POST"])
def api_confirm_qa(qid: str):
    qa = repository.get_qa(qid)
    if not qa:
        return jsonify({"error": "问答条目不存在"}), 400
    repository.update_qa(qid, {"status": "confirmed"})
    return jsonify({"ok": True})


@app.route("/api/interviews/<iid>/confirm-all", methods=["POST"])
def api_confirm_all(iid: str):
    for q in repository.list_qa(iid):
        if q["status"] in ("auto", "pending"):
            repository.update_qa(q["id"], {"status": "confirmed"})
    return jsonify({"ok": True})


# ---------- 校对操作（Phase 4：删除 / 合并下一条 / 拆分） ----------

@app.route("/api/qa/<qid>/delete", methods=["POST"])
def api_delete_qa(qid: str):
    qa = repository.get_qa(qid)
    if not qa:
        return jsonify({"error": "问答条目不存在"}), 400
    repository.soft_delete_qa(qid)
    repository.renumber_seqs(qa["interview_id"])
    return jsonify({"ok": True, "deleted": qid})


@app.route("/api/qa/<qid>/merge-next", methods=["POST"])
def api_merge_next_qa(qid: str):
    try:
        return jsonify({"ok": True, **repository.merge_with_next(qid)})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/qa/<qid>/split", methods=["POST"])
def api_split_qa(qid: str):
    data = request.get_json(silent=True) or {}
    try:
        index = int(data.get("answer_index", 1))
    except (TypeError, ValueError):
        return jsonify({"error": "拆分点必须是数字"}), 400
    try:
        return jsonify({"ok": True, **repository.split_qa(qid, index)})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


def _merge(iid: str, qid: str | None = None) -> dict:
    from .pipeline import postprocess
    return postprocess.merge_into_bank(iid, qid)


@app.route("/api/qa/<qid>/merge", methods=["POST"])
def api_merge_qa(qid: str):
    qa = repository.get_qa(qid)
    if not qa:
        return jsonify({"error": "问答条目不存在"}), 400
    try:
        return jsonify({"ok": True, **_merge(qa["interview_id"], qid)})
    except Exception as e:
        return jsonify({"error": f"入库失败：{e}"}), 400


@app.route("/api/interviews/<iid>/merge-all", methods=["POST"])
def api_merge_all(iid: str):
    try:
        return jsonify({"ok": True, **_merge(iid)})
    except Exception as e:
        return jsonify({"error": f"入库失败：{e}"}), 400


@app.route("/api/interviews/<iid>/data")
def api_interview_data(iid: str):
    iv = repository.get_interview(iid)
    if not iv:
        return jsonify({"error": "不存在"}), 404
    return jsonify({"interview": iv, "qas": repository.list_qa(iid),
                    "categories": repository.list_categories()})


# ---------- 设置（Phase 5 / FR-05） ----------

def _settings_payload() -> dict:
    settings = settings_store.load()
    return {"settings": settings, "masked_key": settings_store.masked_key(),
            "has_api_key": config.has_llm(), "ai_mode": config.ai_mode(),
            "extractor_mode": config.extractor_mode(), "extractor_label": config.extractor_label(),
            "mode_badge": config.mode_badge(),
            "base_url_effective": config.llm_base_url(), "model_effective": config.llm_model(),
            "data_dir": settings_store.data_dir()}


@app.route("/settings")
def page_settings():
    return render_template("settings.html", **_settings_payload(),
                           data_stats=repository.dashboard_stats(),
                           entry_stats=repository.entry_stats(),
                           export_count=len(list(EXPORT_DIR.glob("*.md"))) if EXPORT_DIR.exists() else 0,
                           db_size_kb=round(DB_PATH.stat().st_size / 1024, 1) if DB_PATH.exists() else 0,
                           themes=settings_store.THEMES, ai_modes=settings_store.AI_MODES)


@app.route("/api/settings")
def api_get_settings():
    return jsonify(_settings_payload())


@app.route("/api/settings", methods=["POST"])
def api_save_settings():
    data = request.get_json(silent=True) or {}
    patch: dict = {}
    llm_patch: dict = {}
    if "theme" in data:
        theme = str(data["theme"] or "").strip()
        if theme not in settings_store.THEMES:
            return jsonify({"error": "主题取值非法（light/dark/system）"}), 400
        patch["theme"] = theme
    if "ai_mode" in data:
        mode = str(data["ai_mode"] or "").strip()
        if mode not in settings_store.AI_MODES:
            return jsonify({"error": "AI 模式取值非法（auto/on/off）"}), 400
        patch["ai_mode"] = mode
    if "base_url" in data:
        llm_patch["base_url"] = str(data["base_url"] or "").strip()
    if "model" in data:
        llm_patch["model"] = str(data["model"] or "").strip()
    if llm_patch:
        patch["llm"] = llm_patch
    if not patch:
        return jsonify({"error": "没有需要保存的设置"}), 400
    settings_store.save(patch)          # 立即生效：后续请求按新设置解析提取器
    return jsonify({"ok": True, **_settings_payload()})


@app.route("/api/settings/api-key", methods=["POST"])
def api_save_api_key():
    data = request.get_json(silent=True) or {}
    if "api_key" not in data:
        return jsonify({"error": "缺少 api_key 字段"}), 400
    settings_store.set_api_key(str(data.get("api_key") or ""))
    return jsonify({"ok": True, "masked_key": settings_store.masked_key(),
                    "has_api_key": config.has_llm(),
                    "extractor_mode": config.extractor_mode(),
                    "mode_badge": config.mode_badge()})


@app.route("/api/settings/test", methods=["POST"])
def api_test_llm():
    """「测试连接」：用表单里的临时值或已保存值做一次最小请求。"""
    data = request.get_json(silent=True) or {}
    result = llm_mod.test_connection(
        api_key=data.get("api_key") or None,
        base_url=data.get("base_url") or None,
        model=data.get("model") or None,
    )
    return jsonify(result), (200 if result.get("ok") else 400)


@app.route("/api/settings/reset", methods=["POST"])
def api_reset_settings():
    settings_store.reset()
    return jsonify({"ok": True, **_settings_payload()})


# ---------- 知识库 / 相似关联（Phase 6） ----------

@app.route("/api/knowledge/recompute", methods=["POST"])
def api_knowledge_recompute():
    """重算相似关联；AI 模式下对灰区做批量 LLM 判定。"""
    data = request.get_json(silent=True) or {}
    use_ai = data.get("use_ai")
    use_ai = None if use_ai is None else bool(use_ai)
    result = similar.recompute(use_ai=use_ai)
    return jsonify({"ok": True, "similar_pairs": repository.similar_pair_count(), **result})


@app.route("/api/knowledge/reclassify", methods=["POST"])
def api_knowledge_reclassify():
    """按最新规则重算所有条目的专题/一级分类。"""
    count = 0
    for e in repository.list_entries():
        topic, group = knowledge.classify(e.get("head_question", ""), e.get("category_name", ""),
                                          e.get("category_type", ""))
        repository.set_entry_classification(e["id"], topic, group)
        count += 1
    return jsonify({"ok": True, "entries": count})


@app.route("/api/entries/<entry_id>/best", methods=["POST"])
def api_entry_best(entry_id: str):
    data = request.get_json(silent=True) or {}
    qa_id = (data.get("qa_id") or "").strip() or None
    if not repository.get_entry(entry_id):
        return jsonify({"error": "知识点条目不存在"}), 404
    repository.set_entry_best_qa(entry_id, qa_id)
    return jsonify({"ok": True, "best_qa_id": qa_id})


@app.route("/api/entries/<entry_id>/update", methods=["POST"])
def api_entry_update(entry_id: str):
    data = request.get_json(silent=True) or {}
    if not repository.get_entry(entry_id):
        return jsonify({"error": "知识点条目不存在"}), 404
    fields: dict = {}
    if "optimization" in data:
        fields["optimization"] = (data.get("optimization") or "").strip()
    if "group_name" in data:
        group = str(data.get("group_name") or "").strip()
        if group not in knowledge.GROUPS:
            return jsonify({"error": "一级分类取值非法"}), 400
        fields["group_name"] = group
    if "topic" in data:
        topic = str(data.get("topic") or "").strip()
        if topic and topic not in knowledge.TOPIC_KEYS:
            return jsonify({"error": "专题取值非法"}), 400
        fields["topic"] = topic or None
    if not fields:
        return jsonify({"error": "没有可更新的字段"}), 400
    repository.update_entry(entry_id, fields)
    return jsonify({"ok": True})


@app.route("/api/similar/confirm", methods=["POST"])
def api_similar_confirm():
    data = request.get_json(silent=True) or {}
    a = (data.get("a_entry_id") or "").strip()
    b = (data.get("b_entry_id") or "").strip()
    score = float(data.get("score") or 0.8)
    if not a or not b or a == b:
        return jsonify({"error": "需要两个不同的条目 id"}), 400
    repository.upsert_similar_pair(a, b, max(score, 0.8), "manual", "人工确认同一问题")
    return jsonify({"ok": True})


@app.route("/api/similar/unlink", methods=["POST"])
def api_similar_unlink():
    data = request.get_json(silent=True) or {}
    a = (data.get("a_entry_id") or "").strip()
    b = (data.get("b_entry_id") or "").strip()
    if not a or not b:
        return jsonify({"error": "缺少条目 id"}), 400
    repository.delete_similar_pair(a, b)
    return jsonify({"ok": True})


@app.route("/api/knowledge/search", methods=["GET", "POST"])
def api_knowledge_search():
    """相似候选搜索：给定文本，返回最相似的已有条目（供"手动关联"用）。"""
    text = (request.args.get("q") or "").strip()
    if not text:
        text = str((request.get_json(silent=True) or {}).get("q") or "").strip()
    if not text:
        return jsonify({"error": "请输入要检索的问题文本"}), 400
    scored = []
    for e in repository.list_entries():
        s = similar.rule_score(text, e.get("head_question", ""))
        for v in (e.get("question_variants") or []):
            s = max(s, similar.rule_score(text, v))
        if s >= 0.3:
            scored.append({"entry_id": e["id"], "head_question": e["head_question"],
                           "group_name": e.get("group_name"), "score": round(s, 3)})
    scored.sort(key=lambda x: -x["score"])
    return jsonify({"items": scored[:8]})


# ---------- 简历（Phase 7） ----------

@app.route("/api/resumes", methods=["POST"])
def api_create_resume():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "我的简历").strip()
    try:
        if data.get("data_url"):
            raw = resume_mod.decode_data_url(data["data_url"])
            text = resume_mod.extract_text(name, raw)
        else:
            text = (data.get("content") or "").strip()
            if not text:
                return jsonify({"error": "请上传文件（.txt/.md/.docx）或粘贴简历文本"}), 400
    except resume_mod.ResumeError as e:
        return jsonify({"error": str(e)}), 400
    kw = resume_mod.parse_keywords(text)
    rid = repository.create_resume(name, text, kw["skills"], kw["projects"])
    return jsonify({"ok": True, "resume_id": rid, "name": name,
                    "skills": kw["skills"], "projects": kw["projects"], "years": kw["years"],
                    "chars": len(text)})


@app.route("/api/resumes")
def api_list_resumes():
    items = []
    for r in repository.list_resumes():
        items.append({"id": r["id"], "name": r["name"], "skills": r["skills"],
                      "projects": r["projects"], "chars": len(r["content_text"] or ""),
                      "updated_at": r["updated_at"]})
    return jsonify({"items": items})


@app.route("/api/resumes/<rid>", methods=["POST"])
def api_update_resume(rid: str):
    if not repository.get_resume(rid):
        return jsonify({"error": "简历不存在"}), 404
    data = request.get_json(silent=True) or {}
    skills = data.get("skills")
    projects = data.get("projects")
    if isinstance(skills, str):
        skills = [s.strip() for s in skills.replace("，", ",").split(",") if s.strip()]
    if isinstance(projects, str):
        projects = [s.strip() for s in projects.replace("，", ",").split(",") if s.strip()]
    repository.update_resume(rid, name=(data.get("name") or None), skills=skills, projects=projects)
    return jsonify({"ok": True})


@app.route("/api/resumes/<rid>/delete", methods=["POST"])
def api_delete_resume(rid: str):
    repository.delete_resume(rid)
    return jsonify({"ok": True})


# ---------- 模拟面试（Phase 7） ----------

@app.route("/mock")
def page_mock_setup():
    return render_template("mock_setup.html",
                           interviews=repository.list_interviews(),
                           resumes=repository.list_resumes(),
                           sessions=repository.list_mock_sessions()[:10],
                           bank_stats=repository.entry_stats(),
                           ai_available=config.extractor_mode() == "deepseek",
                           tts_providers=_tts_providers())


@app.route("/api/mock/sessions", methods=["POST"])
def api_create_mock_session():
    data = request.get_json(silent=True) or {}
    mode = str(data.get("mode") or "bank")
    if mode not in ("whole", "bank", "resume"):
        return jsonify({"error": "模拟模式取值非法"}), 400
    interview_id = (data.get("interview_id") or "").strip() or None
    resume_id = (data.get("resume_id") or "").strip() or None
    try:
        generated = mock_gen.build_questions(
            mode=mode, interview_id=interview_id, resume_id=resume_id,
            count=int(data.get("count") or 8), shuffle=bool(data.get("shuffle")),
            include_follow_up=bool(data.get("include_follow_up")),
            ai_ratio=float(data.get("ai_ratio") or 0.4),
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    questions = generated["questions"]
    if not questions:
        return jsonify({"error": "没有生成任何题目，请检查题库/面试记录或简历内容"}), 400

    if mode == "whole" and interview_id:
        iv = repository.get_interview(interview_id)
        title = f"整场模拟 · {iv['title'] if iv else interview_id}"
    elif mode == "resume" and resume_id:
        rv = repository.get_resume(resume_id)
        title = f"简历模拟 · {rv['name'] if rv else '简历'}"
    else:
        title = "题库随机模拟"

    cfg = {"count": len(questions), "shuffle": bool(data.get("shuffle")),
           "include_follow_up": bool(data.get("include_follow_up")),
           "ai_ratio": float(data.get("ai_ratio") or 0.4),
           "tts": str(data.get("tts") or "web"),
           "note": generated.get("note") or ""}
    sid = repository.create_mock_session(title, mode, cfg, interview_id, resume_id)
    for i, q in enumerate(questions, start=1):
        q["session_id"] = sid
        q["seq"] = i
        repository.insert_mock_turn(q)
    repository.update_mock_session(sid, question_count=len(questions))
    return jsonify({"ok": True, "session_id": sid, "title": title,
                    "question_count": len(questions), "ai_used": generated["ai_used"],
                    "bank_used": generated["bank_used"], "note": generated.get("note") or ""})


@app.route("/mock/<sid>")
def page_mock_run(sid: str):
    session = repository.get_mock_session(sid)
    if not session:
        abort(404)
    turns = repository.list_mock_turns(sid)
    index = request.args.get("i")
    idx = int(index) - 1 if (index or "").isdigit() else 0
    idx = max(0, min(idx, max(len(turns) - 1, 0)))
    current = turns[idx] if turns else None
    answered = sum(1 for t in turns if (t.get("my_answer") or "").strip() or t.get("mark"))
    return render_template("mock_run.html", session=session, turns=turns, current=current,
                           idx=idx, answered=answered,
                           edge_tts_available=_edge_tts_available())


@app.route("/api/mock/turns/<tid>", methods=["POST"])
def api_save_mock_turn(tid: str):
    turn = repository.get_mock_turn(tid)
    if not turn:
        return jsonify({"error": "题目不存在"}), 404
    data = request.get_json(silent=True) or {}
    fields: dict = {}
    if "my_answer" in data:
        fields["my_answer"] = str(data.get("my_answer") or "")
    if "elapsed_sec" in data:
        try:
            fields["elapsed_sec"] = int(data.get("elapsed_sec") or 0)
        except (TypeError, ValueError):
            fields["elapsed_sec"] = None
    if "mark" in data:
        mark = str(data.get("mark") or "").strip() or None
        if mark not in (None, "good", "unsure", "blank"):
            return jsonify({"error": "标记取值非法"}), 400
        fields["mark"] = mark
    repository.update_mock_turn(tid, fields)
    session_id = turn["session_id"]
    turns = repository.list_mock_turns(session_id)
    answered = sum(1 for t in turns if (t.get("my_answer") or "").strip() or t.get("mark"))
    repository.update_mock_session(session_id, answered_count=answered)
    return jsonify({"ok": True, "answered": answered, "total": len(turns)})


@app.route("/api/mock/sessions/<sid>/finish", methods=["POST"])
def api_finish_mock_session(sid: str):
    session = repository.get_mock_session(sid)
    if not session:
        return jsonify({"error": "模拟会话不存在"}), 404
    from .util import now as _now
    turns = repository.list_mock_turns(sid)
    answered = sum(1 for t in turns if (t.get("my_answer") or "").strip() or t.get("mark"))
    repository.update_mock_session(sid, status="finished", answered_count=answered,
                                   finished_at=_now())
    return jsonify({"ok": True, "answered": answered, "total": len(turns)})


@app.route("/api/mock/sessions/<sid>/delete", methods=["POST"])
def api_delete_mock_session(sid: str):
    repository.delete_mock_session(sid)
    return jsonify({"ok": True})


@app.route("/mock/<sid>/report")
def page_mock_report(sid: str):
    session = repository.get_mock_session(sid)
    if not session:
        abort(404)
    turns = repository.list_mock_turns(sid)
    entries = {e["id"]: e for e in repository.list_entries()}
    total_sec = sum(int(t.get("elapsed_sec") or 0) for t in turns)
    answered = [t for t in turns if (t.get("my_answer") or "").strip()]
    weak = [t for t in turns if t.get("mark") in ("unsure", "blank") or not (t.get("my_answer") or "").strip()]
    by_group: dict[str, int] = {}
    for t in turns:
        e = entries.get(t.get("reference_entry_id") or "")
        group = (e or {}).get("group_name") or "未归类"
        by_group[group] = by_group.get(group, 0) + 1
    return render_template("mock_report.html", session=session, turns=turns, entries=entries,
                           total_sec=total_sec, answered_count=len(answered), weak=weak,
                           by_group=sorted(by_group.items(), key=lambda x: -x[1]))


def _tts_providers() -> dict:
    """可用的语音方案探测：浏览器内置（始终可用）/ edge-tts / piper（开源本地）。"""
    import importlib.util
    import shutil
    return {
        "web": True,
        "none": True,
        "edge": importlib.util.find_spec("edge_tts") is not None,
        "piper": shutil.which("piper") is not None,
    }


def _edge_tts_available() -> bool:
    return bool(_tts_providers().get("edge"))


@app.route("/api/tts/status")
def api_tts_status():
    return jsonify(_tts_providers())
