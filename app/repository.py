"""Repository：对 SQLite 的读写封装（每操作自开连接，个人单机够用）。"""
from __future__ import annotations

from typing import Any, Iterable

from .db import conn_ctx
from .util import dumps, loads, norm_key, now, uid

# 允许被 update_qa 更新的列（白名单，防注入/防误改只读字段）
QA_UPDATABLE = {
    "status", "annotation", "optimization", "category_id", "entry_id",
    "q_speaker_id", "q_corrected_text", "q_is_corrected",
    "q_text", "answers", "q_source_ref", "confidence", "parent_id",
    "direction", "deleted",
}


def _rows_to_dicts(rows) -> list[dict]:
    return [dict(r) for r in rows]


# ---------- interviews ----------

def create_interview(data: dict) -> str:
    iid = data.get("id") or uid()
    ts = now()
    with conn_ctx() as conn:
        conn.execute(
            """INSERT INTO interviews
               (id,title,company,position,location,interview_at,expected_salary,
                duration_minutes,transcript_hash,interview_type,date,raw_transcript,
                transcript_format,status,parser_version,speaker_map,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (iid, data["title"], data.get("company"), data.get("position"),
             data.get("location"), data.get("interview_at"), data.get("expected_salary"),
             data.get("duration_minutes"), data.get("transcript_hash"),
             data.get("interview_type", "mixed"), data.get("date"),
             data.get("raw_transcript", ""), data.get("transcript_format", "none"),
             data.get("status", "draft"), data.get("parser_version"),
             dumps(data.get("speaker_map", [])), ts, ts),
        )
        conn.commit()
    return iid


def find_interview_by_hash(transcript_hash: str) -> dict | None:
    """重复导入检测：按原文指纹找既有面试。"""
    if not transcript_hash:
        return None
    with conn_ctx() as conn:
        row = conn.execute(
            """SELECT id, title, company, position, created_at, interview_at
               FROM interviews WHERE transcript_hash=? ORDER BY created_at DESC LIMIT 1""",
            (transcript_hash,)).fetchone()
    return dict(row) if row else None


def update_interview(iid: str, **fields: Any) -> None:
    allowed = {"title", "company", "position", "location", "interview_at",
               "expected_salary", "duration_minutes", "interview_type", "date",
               "status", "transcript_format", "parser_version", "speaker_map",
               "raw_transcript", "transcript_hash"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    sets["updated_at"] = now()
    cols = ", ".join(f"{k}=?" for k in sets)
    with conn_ctx() as conn:
        conn.execute(f"UPDATE interviews SET {cols} WHERE id=?", (*sets.values(), iid))
        conn.commit()


def get_interview(iid: str) -> dict | None:
    with conn_ctx() as conn:
        row = conn.execute("SELECT * FROM interviews WHERE id=?", (iid,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["speaker_map"] = loads(d["speaker_map"], [])
    return d


_SORT_SQL = {
    "recent": "i.updated_at DESC",
    "interview_at": "COALESCE(NULLIF(i.interview_at,''), NULLIF(i.date,''), '') DESC",
    "qa_count": "qa_count DESC, i.updated_at DESC",
    "pending": "pending_count DESC, i.updated_at DESC",
}

_QA_ALIVE = "q.interview_id=i.id AND q.deleted=0"


def list_interviews(search: str | None = None, sort: str = "recent",
                    only: str | None = None) -> list[dict]:
    """面试记录列表：支持关键词搜索、排序与快筛（Phase 4）。"""
    sql = f"""SELECT i.*,
                 (SELECT COUNT(*) FROM qa_items q WHERE {_QA_ALIVE}) AS qa_count,
                 (SELECT COUNT(*) FROM qa_items q WHERE {_QA_ALIVE}
                   AND q.status IN ('pending','auto')) AS pending_count,
                 (SELECT COUNT(*) FROM qa_items q WHERE {_QA_ALIVE}
                   AND q.entry_id IS NOT NULL) AS merged_count,
                 (SELECT COUNT(*) FROM qa_items q WHERE {_QA_ALIVE}
                   AND q.answers='[]') AS blank_answer_count
              FROM interviews i"""
    where: list[str] = []
    params: list[Any] = []
    if search and search.strip():
        like = f"%{search.strip()}%"
        where.append("(i.title LIKE ? OR IFNULL(i.company,'') LIKE ? "
                     "OR IFNULL(i.position,'') LIKE ? OR IFNULL(i.location,'') LIKE ?)")
        params += [like] * 4
    if only == "pending":
        where.append(f"(SELECT COUNT(*) FROM qa_items q WHERE {_QA_ALIVE} "
                     "AND q.status IN ('pending','auto')) > 0")
    elif only == "merged":
        where.append(f"(SELECT COUNT(*) FROM qa_items q WHERE {_QA_ALIVE} "
                     "AND q.entry_id IS NOT NULL) > 0")
    elif only == "not_merged":
        where.append(f"(SELECT COUNT(*) FROM qa_items q WHERE {_QA_ALIVE} "
                     "AND q.entry_id IS NULL) > 0")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY " + _SORT_SQL.get(sort, _SORT_SQL["recent"])
    with conn_ctx() as conn:
        rows = conn.execute(sql, params).fetchall()
    return _rows_to_dicts(rows)


def dashboard_stats() -> dict:
    """面试记录页统计条（Phase 4）。"""
    with conn_ctx() as conn:
        row = conn.execute(
            """SELECT (SELECT COUNT(*) FROM interviews) AS interviews,
                      (SELECT COUNT(*) FROM qa_items WHERE deleted=0) AS qa_total,
                      (SELECT COUNT(*) FROM qa_items WHERE deleted=0
                        AND status IN ('pending','auto')) AS qa_pending,
                      (SELECT COUNT(DISTINCT company) FROM interviews
                        WHERE company IS NOT NULL AND company <> '') AS companies""").fetchone()
    return dict(row) if row else {"interviews": 0, "qa_total": 0, "qa_pending": 0, "companies": 0}


# ---------- qa_items ----------

def _serialize_answers(answers: list) -> str:
    return dumps([{**a, "source_ref": a.get("source_ref")} for a in answers])


def insert_qa(q: dict) -> str:
    qid = q.get("id") or uid()
    ts = now()
    with conn_ctx() as conn:
        conn.execute(
            """INSERT INTO qa_items
               (id,interview_id,seq,parent_id,status,q_text,q_speaker_id,q_source_ref,
                q_confidence,q_is_corrected,q_corrected_text,answers,annotation,optimization,
                category_id,entry_id,tag_ids,confidence,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (qid, q["interview_id"], q.get("seq", 0), q.get("parent_id"),
             q.get("status", "auto"), q.get("q_text", ""), q.get("q_speaker_id"),
             dumps(q.get("q_source_ref")), q.get("q_confidence"),
             1 if q.get("q_is_corrected") else 0, q.get("q_corrected_text"),
             _serialize_answers(q.get("answers", [])), q.get("annotation"),
             q.get("optimization"), q.get("category_id"), q.get("entry_id"),
             dumps(q.get("tag_ids", [])), q.get("confidence"), ts, ts),
        )
        conn.commit()
    return qid


def list_qa(interview_id: str) -> list[dict]:
    """本场问答（按 seq 升序，排除软删除条目）。"""
    with conn_ctx() as conn:
        rows = conn.execute(
            """SELECT q.*, c.name AS category_name, c.type AS category_type
               FROM qa_items q LEFT JOIN categories c ON c.id = q.category_id
               WHERE q.interview_id=? AND q.deleted=0 ORDER BY q.seq ASC""",
            (interview_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["answers"] = loads(d["answers"], [])
        d["q_source_ref"] = loads(d["q_source_ref"])
        d["tag_ids"] = loads(d["tag_ids"], [])
        out.append(d)
    return out


def get_qa(qid: str) -> dict | None:
    with conn_ctx() as conn:
        row = conn.execute(
            """SELECT q.*, c.name AS category_name, c.type AS category_type
               FROM qa_items q LEFT JOIN categories c ON c.id = q.category_id
               WHERE q.id=? AND q.deleted=0""", (qid,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["answers"] = loads(d["answers"], [])
    d["q_source_ref"] = loads(d["q_source_ref"])
    d["tag_ids"] = loads(d["tag_ids"], [])
    return d


def update_qa(qid: str, fields: dict) -> None:
    sets = {k: v for k, v in fields.items() if k in QA_UPDATABLE}
    if not sets:
        return
    # JSON 列做序列化
    if "answers" in sets and not isinstance(sets["answers"], str):
        sets["answers"] = _serialize_answers(sets["answers"])
    if "q_source_ref" in sets and sets["q_source_ref"] is not None and not isinstance(sets["q_source_ref"], str):
        sets["q_source_ref"] = dumps(sets["q_source_ref"])
    if "tag_ids" in sets and not isinstance(sets["tag_ids"], str):
        sets["tag_ids"] = dumps(sets["tag_ids"])
    sets["updated_at"] = now()
    cols = ", ".join(f"{k}=?" for k in sets)
    with conn_ctx() as conn:
        conn.execute(f"UPDATE qa_items SET {cols} WHERE id=?", (*sets.values(), qid))
        conn.commit()


def replace_interview_qa(interview_id: str, items: list[dict]) -> None:
    """先清空本场旧条目再写入（导入解析为一次性产物，避免重复）。"""
    with conn_ctx() as conn:
        conn.execute("DELETE FROM qa_items WHERE interview_id=?", (interview_id,))
        conn.commit()
    for q in items:
        q["interview_id"] = interview_id
        insert_qa(q)


def clear_qa_entry_links(interview_id: str) -> None:
    """整场重新入库前，摘除旧 entry 链接（频次由 merge 重算）。"""
    with conn_ctx() as conn:
        conn.execute(
            """UPDATE qa_items SET entry_id=NULL WHERE interview_id=?
               AND entry_id IS NOT NULL""", (interview_id,))
        conn.commit()


# ---------- 校对操作（Phase 4：删除 / 合并 / 拆分） ----------

def next_seq(interview_id: str) -> int:
    with conn_ctx() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(seq),0) AS m FROM qa_items WHERE interview_id=?",
            (interview_id,)).fetchone()
    return int(row["m"]) + 1


def renumber_seqs(interview_id: str) -> None:
    """把本场 seq 重排为连续 1..N（合并/拆分/删除后调用）。"""
    qas = list_qa(interview_id)
    with conn_ctx() as conn:
        for i, q in enumerate(qas, start=1):
            if q["seq"] != i:
                conn.execute("UPDATE qa_items SET seq=? WHERE id=?", (i, q["id"]))
        conn.commit()


def soft_delete_qa(qid: str) -> None:
    """软删除误识别的问答（保留审计，不物理删除，R2 可追溯）。"""
    update_qa(qid, {"deleted": 1})


def merge_with_next(qid: str) -> dict:
    """与下一条合并（校对：被误拆成两条的同一问答）。

    红线 R2 处理：机器提取的问题文本 `q_text` **保持不变**，合并结果写入
    `q_corrected_text`（界面标注"已合并"），原第二条软删除但数据保留可审计。
    回答按顺序拼接；出处区间取两条的覆盖范围并用原文切片校验。
    """
    cur = get_qa(qid)
    if not cur:
        raise ValueError("问答条目不存在")
    qas = list_qa(cur["interview_id"])
    idx = next((i for i, q in enumerate(qas) if q["id"] == qid), None)
    if idx is None or idx + 1 >= len(qas):
        raise ValueError("已是最后一条问答，无法与下一条合并")
    nxt = qas[idx + 1]

    merged_text = f"{cur['q_text']}\n{nxt['q_text']}".strip()
    refs = [r for r in (cur.get("q_source_ref"), nxt.get("q_source_ref")) if r]
    merged_ref = None
    if refs:
        start = min(int(r.get("start", 0)) for r in refs)
        end = max(int(r.get("end", 0)) for r in refs)
        raw = (get_interview(cur["interview_id"]) or {}).get("raw_transcript") or ""
        quote = raw[start:end + 1] if 0 <= start <= end < len(raw) else (refs[0].get("quote") or "")
        merged_ref = {"start": start, "end": end, "quote": quote}

    answers = list(cur.get("answers") or []) + list(nxt.get("answers") or [])
    update_qa(qid, {
        "q_corrected_text": merged_text, "q_is_corrected": 1,
        "q_source_ref": merged_ref, "answers": answers, "status": "corrected",
    })
    soft_delete_qa(nxt["id"])
    renumber_seqs(cur["interview_id"])
    return {"kept": qid, "removed": nxt["id"], "answers": len(answers)}


def split_qa(qid: str, answer_index: int) -> dict:
    """拆分：从第 answer_index 条回答起划归新条目（校对误合并，反问后续答可分开）。"""
    cur = get_qa(qid)
    if not cur:
        raise ValueError("问答条目不存在")
    answers = list(cur.get("answers") or [])
    k = int(answer_index)
    if not (0 < k < len(answers)):
        raise ValueError(f"拆分点需在 1..{max(len(answers) - 1, 0)} 之间（当前该题有 {len(answers)} 条回答）")
    head, tail = answers[:k], answers[k:]
    new_id = insert_qa({
        "interview_id": cur["interview_id"], "seq": cur["seq"] + 1,
        "parent_id": cur["id"], "status": "confirmed",
        "q_text": cur["q_text"], "q_speaker_id": cur.get("q_speaker_id"),
        "q_source_ref": cur.get("q_source_ref"), "q_confidence": cur.get("q_confidence"),
        "q_is_corrected": cur.get("q_is_corrected"), "q_corrected_text": cur.get("q_corrected_text"),
        "answers": tail, "annotation": "", "optimization": "",
        "category_id": cur.get("category_id"), "entry_id": None,
        "direction": cur.get("direction") or "normal", "tag_ids": cur.get("tag_ids") or [],
        "confidence": cur.get("confidence"),
    })
    update_qa(qid, {"answers": head, "status": "confirmed"})
    renumber_seqs(cur["interview_id"])
    return {"kept": qid, "created": new_id, "kept_answers": len(head), "new_answers": len(tail)}


# ---------- categories（Category.norm 去重，R3） ----------

_CAT_TYPE_HINTS = [
    ("java", "technical"), ("jvm", "technical"), ("redis", "technical"),
    ("缓存", "technical"), ("算法", "technical"), ("数据库", "technical"),
    ("sql", "technical"), ("http", "technical"), ("tcp", "technical"),
    ("系统设计", "technical"), ("设计模式", "technical"), ("spring", "technical"),
    ("线程", "technical"), ("并发", "technical"), ("分布式", "technical"),
    ("消息", "technical"), ("内存", "technical"), ("垃圾回收", "technical"),
    ("网络", "technical"), ("linux", "technical"),
    ("自我介绍", "hr"), ("离职", "hr"), ("薪资", "hr"), ("为什么来", "hr"),
    ("职业规划", "hr"), ("优点", "hr"), ("缺点", "hr"),
    ("项目", "behavior"), ("star", "behavior"), ("冲突", "behavior"),
    ("团队", "behavior"), ("压力", "behavior"), ("失败", "behavior"),
    ("困难", "behavior"),
]


def classify_category(name: str) -> str:
    n = (name or "").lower()
    for kw, typ in _CAT_TYPE_HINTS:
        if kw in n:
            return typ
    return "other"


def get_or_create_category(name: str, type_: str | None = None) -> dict:
    """按 norm 幂等创建/获取知识点。"""
    name = (name or "").strip() or "未分类"
    nk = norm_key(name)
    with conn_ctx() as conn:
        row = conn.execute(
            "SELECT * FROM categories WHERE norm=? ORDER BY created_at LIMIT 1",
            (nk,)).fetchone()
        if row:
            return dict(row)
        cat = {
            "id": uid(), "name": name, "norm": nk, "parent_id": None,
            "type": type_ or classify_category(name), "created_at": now(),
            "last_asked_at": None,
        }
        conn.execute(
            """INSERT INTO categories (id,name,norm,parent_id,type,created_at,last_asked_at)
               VALUES (?,?,?,?,?,?,?)""",
            (cat["id"], cat["name"], cat["norm"], cat["parent_id"],
             cat["type"], cat["created_at"], cat["last_asked_at"]))
        conn.commit()
        return cat


def touch_category(cat_id: str) -> None:
    with conn_ctx() as conn:
        conn.execute("UPDATE categories SET last_asked_at=? WHERE id=?", (now(), cat_id))
        conn.commit()


def list_categories() -> list[dict]:
    with conn_ctx() as conn:
        rows = conn.execute(
            """SELECT c.*,
                      (SELECT COUNT(*) FROM qa_items q WHERE q.category_id=c.id) AS qa_count,
                      (SELECT COUNT(*) FROM entries e WHERE e.category_id=c.id) AS entry_count
               FROM categories c ORDER BY c.type, c.name""").fetchall()
    return _rows_to_dicts(rows)


# ---------- entries（KnowledgeEntry：跨场聚合 + 频次，R3） ----------

def get_entry(eid: str) -> dict | None:
    with conn_ctx() as conn:
        row = conn.execute(
            """SELECT e.*, c.name AS category_name, c.type AS category_type
               FROM entries e LEFT JOIN categories c ON c.id=e.category_id
               WHERE e.id=?""", (eid,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["question_variants"] = loads(d["question_variants"], [])
    d["source_qa_ids"] = loads(d["source_qa_ids"], [])
    d["tag_ids"] = loads(d["tag_ids"], [])
    return d


def find_entry_by_question(category_id: str, question_norm: str) -> dict | None:
    """同一知识点下按问题归一化键匹配既有面经条目（含变体）。"""
    if not question_norm:
        return None
    with conn_ctx() as conn:
        rows = conn.execute("SELECT * FROM entries WHERE category_id=?", (category_id,)).fetchall()
    for r in rows:
        d = dict(r)
        if norm_key(d["head_question"]) == question_norm:
            return d
        for v in loads(d["question_variants"], []):
            if norm_key(v) == question_norm:
                return d
    return None


def create_entry(category_id: str, head_question: str) -> dict:
    eid = uid()
    ts = now()
    with conn_ctx() as conn:
        conn.execute(
            """INSERT INTO entries (id,category_id,head_question,question_variants,core_extract,
               optimization,source_qa_ids,ask_times,tag_ids,first_asked_at,last_asked_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (eid, category_id, head_question, dumps([]), None, None,
             dumps([]), 0, dumps([]), None, None, ts))
        conn.commit()
    return get_entry(eid)  # type: ignore[return-value]


def add_qa_to_entry(entry_id: str, qa: dict, source_qa_id: str) -> None:
    """R3 合并：频次 +1、出处追加、变体并入、时间刷新、聚合内容更新。

    红线 R2：仅 entry.core_extract（忠实原文）与 entry.optimization（建议）
    两个并列独立字段被更新，绝不改写 qa.answers 里的 extract_text。
    """
    e = get_entry(entry_id)
    if not e:
        return
    sources = [s for s in e["source_qa_ids"] if s != source_qa_id] + [source_qa_id]
    q_norm = norm_key(qa.get("q_text", ""))
    variants = list(e["question_variants"])
    if q_norm and q_norm != norm_key(e["head_question"]) and not any(
            norm_key(v) == q_norm for v in variants):
        variants.append(qa["q_text"])

    # 聚合展示内容：默认取最近一次确认的忠实回答；optimization 优先保留既有，空则用本场工作区建议
    answers = qa.get("answers") or []
    latest_extract = None
    for a in answers:
        if a.get("extract_text"):
            latest_extract = a["corrected_text"] if a.get("is_corrected") and a.get("corrected_text") else a["extract_text"]
            break
    ts = now()
    with conn_ctx() as conn:
        if latest_extract:
            conn.execute("UPDATE entries SET core_extract=? WHERE id=?", (latest_extract, entry_id))
        new_opt = (qa.get("optimization") or "").strip()
        if new_opt and not (e.get("optimization") or "").strip():
            conn.execute("UPDATE entries SET optimization=? WHERE id=?", (new_opt, entry_id))
        conn.execute(
            """UPDATE entries
               SET source_qa_ids=?, question_variants=?, ask_times=?,
                   first_asked_at=COALESCE(first_asked_at, ?), last_asked_at=?, updated_at=?
               WHERE id=?""",
            (dumps(sources), dumps(variants), len(sources), ts, ts, ts, entry_id))
        conn.commit()
    touch_category(e["category_id"])


def list_entries(category_id: str | None = None) -> list[dict]:
    sql = """SELECT e.*, c.name AS category_name, c.type AS category_type
             FROM entries e LEFT JOIN categories c ON c.id=e.category_id"""
    params: tuple = ()
    if category_id:
        sql += " WHERE e.category_id=?"
        params = (category_id,)
    sql += " ORDER BY e.ask_times DESC, e.last_asked_at DESC"
    with conn_ctx() as conn:
        rows = conn.execute(sql, params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["question_variants"] = loads(d["question_variants"], [])
        d["source_qa_ids"] = loads(d["source_qa_ids"], [])
        d["source_interview_count"] = _count_interviews_of(d["source_qa_ids"])
        out.append(d)
    return out


def _count_interviews_of(source_qa_ids: Iterable[str]) -> int:
    """统计一组出处 QA 覆盖的面试场次（分块查询，规避 SQLite 参数上限）。"""
    ids = list(dict.fromkeys(source_qa_ids))
    if not ids:
        return 0
    found: set[str] = set()
    chunk_size = 800
    with conn_ctx() as conn:
        for i in range(0, len(ids), chunk_size):
            chunk = ids[i:i + chunk_size]
            marks = ",".join("?" for _ in chunk)
            rows = conn.execute(
                f"SELECT DISTINCT interview_id FROM qa_items WHERE id IN ({marks})",
                chunk).fetchall()
            found.update(r["interview_id"] for r in rows)
    return len(found)


def count_source_interviews(source_qa_ids: Iterable[str]) -> int:
    """对一组出处 QA id 统计覆盖的不同面试场次数（用于知识点卡片聚合显示）。"""
    return _count_interviews_of(source_qa_ids)


def update_entry(eid: str, fields: dict) -> None:
    allowed = {"core_extract", "optimization", "head_question", "question_variants",
               "tag_ids", "category_id"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    for k in ("question_variants", "tag_ids"):
        if k in sets and not isinstance(sets[k], str):
            sets[k] = dumps(sets[k])
    sets["updated_at"] = now()
    cols = ", ".join(f"{k}=?" for k in sets)
    with conn_ctx() as conn:
        conn.execute(f"UPDATE entries SET {cols} WHERE id=?", (*sets.values(), eid))
        conn.commit()


def entry_stats() -> dict:
    with conn_ctx() as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS entry_count, COALESCE(SUM(ask_times),0) AS total_ask,
                      COUNT(DISTINCT category_id) AS cat_count FROM entries""").fetchone()
        row2 = conn.execute("SELECT COUNT(*) AS n FROM interviews").fetchone()
    return {"entries": row["entry_count"], "total_ask": row["total_ask"],
            "categories": row["cat_count"], "interviews": row2["n"]}
