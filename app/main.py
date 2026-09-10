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

from flask import (Flask, Response, abort, jsonify, render_template, request)

from . import exporter, repository, service
from .config import ensure_dirs, extractor_label, extractor_mode
from .db import init_db
from .migrations import run_migrations
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
    return {"extractor_label": extractor_label(), "extractor_mode": extractor_mode(),
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
