"""AI_Review Web 入口（Flask + Jinja2，方案二最简栈）。

页面：工作台 / 导入向导 / 问答列表 / 单题复盘 / 面经库 / 导出预览
API ：/api/analyze、/api/interviews、/api/qa/*、merge/confirm 等

> 备注：设计文档 docs/02 原写 FastAPI；因本机离线环境内置 Flask 3.1（无 fastapi/uvicorn
> 且无法联网安装），第三阶段按 Flask 落地（同属方案二 Python 最简栈，模板与逻辑不变，
> 迁回 FastAPI 仅需替换路由层）。详见 docs/03。
"""
from __future__ import annotations

import json

from flask import (Flask, Response, abort, jsonify, redirect, render_template,
                   request, url_for)

from . import exporter, repository, service
from .config import ensure_dirs, extractor_label, extractor_mode
from .db import init_db
from .service import ROLE_LABEL, UsageError

app = Flask(__name__)
app.json.ensure_ascii = False  # API 返回原生 UTF-8（中文可读）

INTERVIEW_TYPES = {"technical": "技术面", "behavior": "行为面", "hr": "HR面",
                   "mixed": "综合面", "mock": "模拟面"}
STATUS_LABEL = {"auto": "机器初提", "pending": "待确认", "confirmed": "已确认",
                "corrected": "已修正"}

# 首次启动准备目录与数据库（幂等）
ensure_dirs()
init_db()


@app.context_processor
def _inject_globals():
    return {"extractor_label": extractor_label(), "extractor_mode": extractor_mode(),
            "interview_types": INTERVIEW_TYPES, "status_label": STATUS_LABEL}


@app.errorhandler(404)
def _not_found(_e):
    return render_template("404.html"), 404


def _speaker_map_of(iv: dict) -> dict:
    return {sp["id"]: ROLE_LABEL.get(sp["role"], sp.get("label", sp["id"]))
            for sp in (iv or {}).get("speaker_map", [])}


# ---------- 页面 ----------

@app.route("/")
def page_index():
    return render_template("index.html", interviews=repository.list_interviews(),
                           stats=repository.entry_stats())


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
    entries = repository.list_entries()
    groups: list[dict] = []
    seen: dict[str, int] = {}
    for e in entries:
        cat = e.get("category_name") or "未分类"
        if cat not in seen:
            seen[cat] = len(groups)
            groups.append({"category": cat, "entries": [], "total_ask": 0,
                           "source_ids": [], "entry_count": 0})
        g = groups[seen[cat]]
        g["entries"].append(e)
        g["total_ask"] += e["ask_times"]
        g["source_ids"].extend(e["source_qa_ids"])
        g["entry_count"] += 1
    for g in groups:
        g["interviews"] = repository.count_source_interviews(g["source_ids"])
        g.pop("source_ids", None)
    return render_template("bank.html", groups=groups, stats=repository.entry_stats(),
                           categories=repository.list_categories())


@app.route("/interviews/<iid>/export")
def page_export_interview(iid: str):
    iv = repository.get_interview(iid)
    if not iv:
        abort(404)
    return render_template("export.html", md=exporter.interview_to_md(iid),
                           kind="interview", iid=iid, iv=iv)


@app.route("/bank/export")
def page_export_bank():
    return render_template("export.html", md=exporter.bank_to_md(), kind="bank")


# ---------- 导出下载 ----------

@app.route("/download/interviews/<iid>.md")
def download_interview_md(iid: str):
    iv = repository.get_interview(iid)
    md = exporter.interview_to_md(iid)
    fname = f"面试复盘-{(iv['title'] if iv else iid)}.md".replace("/", "_")
    return Response(md, mimetype="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@app.route("/download/bank.md")
def download_bank_md():
    md = exporter.bank_to_md()
    return Response(md, mimetype="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="面经库快照.md"'})


# ---------- API ----------

@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(service.analyze((data or {}).get("text") or ""))
    except UsageError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/interviews", methods=["POST"])
def api_create_interview():
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(service.run_import(data))
    except UsageError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:  # LLM 等不可预期错误
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
