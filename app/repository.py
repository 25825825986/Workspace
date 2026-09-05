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
               (id,title,company,position,interview_type,date,raw_transcript,
                transcript_format,status,parser_version,speaker_map,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (iid, data["title"], data.get("company"), data.get("position"),
             data.get("interview_type", "mixed"), data.get("date"),
             data.get("raw_transcript", ""), data.get("transcript_format", "none"),
             data.get("status", "draft"), data.get("parser_version"),
             dumps(data.get("speaker_map", [])), ts, ts),
        )
        conn.commit()
    return iid


def update_interview(iid: str, **fields: Any) -> None:
    allowed = {"title", "company", "position", "interview_type", "date", "status",
               "transcript_format", "parser_version", "speaker_map", "raw_transcript"}
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


def list_interviews() -> list[dict]:
    with conn_ctx() as conn:
        rows = conn.execute(
            """SELECT i.*,
                      (SELECT COUNT(*) FROM qa_items q WHERE q.interview_id=i.id) AS qa_count,
                      (SELECT COUNT(*) FROM qa_items q WHERE q.interview_id=i.id
                        AND q.status IN ('pending','auto')) AS pending_count,
                      (SELECT COUNT(*) FROM qa_items q WHERE q.interview_id=i.id AND q.entry_id IS NOT NULL) AS merged_count
               FROM interviews i ORDER BY i.updated_at DESC""").fetchall()
    return _rows_to_dicts(rows)


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
    with conn_ctx() as conn:
        rows = conn.execute(
            """SELECT q.*, c.name AS category_name, c.type AS category_type
               FROM qa_items q LEFT JOIN categories c ON c.id = q.category_id
               WHERE q.interview_id=? ORDER BY q.seq ASC""",
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
               WHERE q.id=?""", (qid,)).fetchone()
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
    ids = list(source_qa_ids)
    if not ids:
        return 0
    marks = ",".join("?" for _ in ids)
    with conn_ctx() as conn:
        row = conn.execute(
            f"SELECT COUNT(DISTINCT interview_id) AS n FROM qa_items WHERE id IN ({marks})",
            ids).fetchone()
    return int(row["n"]) if row else 0


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
