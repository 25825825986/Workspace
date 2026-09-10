"""SQLite 数据层：建表 DDL 与连接管理。

表结构与 docs/01-product-prototype-design.md §5.3 的映射说明：
- interviews        ← InterviewRecord（speaker_map 以 JSON 列存 Speaker[]）
- categories        ← Category（norm 去重键；parent_id 二级树）
- qa_items          ← QAItem + 内嵌 Question/Answer[]/批注：
     question 文本/speaker/引用/修正 以 q_* 列存；
     answers 为 JSON 数组 [{id, extract_text, corrected_text, is_corrected,
                             source_ref:{start,end,quote}, speaker_id, duration_sec}]；
     annotation=批注(v0.2)，optimization=单题复盘工作区（入库时聚合进 entry，绝不进 extract_text）；
- entries           ← KnowledgeEntry（跨场聚合：source_qa_ids/频次/出处）
- tags              ← Tag（tag_ids 以 JSON 数组列挂在 qa_items / entries）
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS interviews (
    id                TEXT PRIMARY KEY,
    title             TEXT NOT NULL,
    company           TEXT,
    position          TEXT,
    location          TEXT,                    -- Phase 4：面试地点
    interview_at      TEXT,                    -- Phase 4：面试时间（ISO，精确到分钟）
    expected_salary   TEXT,                    -- Phase 4：期望薪资（自由文本）
    duration_minutes  INTEGER,                 -- Phase 4：整场时长（分钟）
    transcript_hash   TEXT,                    -- Phase 4：原文指纹，用于重复导入检测
    interview_type    TEXT NOT NULL DEFAULT 'mixed',
    date              TEXT,
    raw_transcript    TEXT NOT NULL,
    transcript_format TEXT NOT NULL DEFAULT 'none',
    status            TEXT NOT NULL DEFAULT 'draft',
    parser_version    TEXT,
    speaker_map       TEXT NOT NULL DEFAULT '[]',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_interviews_hash ON interviews(transcript_hash);

CREATE TABLE IF NOT EXISTS categories (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    norm          TEXT NOT NULL,
    parent_id     TEXT,
    type          TEXT NOT NULL DEFAULT 'other',
    created_at    TEXT NOT NULL,
    last_asked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_categories_norm ON categories(norm);

CREATE TABLE IF NOT EXISTS qa_items (
    id              TEXT PRIMARY KEY,
    interview_id    TEXT NOT NULL REFERENCES interviews(id),
    seq             INTEGER NOT NULL,
    parent_id       TEXT,
    status          TEXT NOT NULL DEFAULT 'auto',
    q_text          TEXT NOT NULL,
    q_speaker_id    TEXT,
    q_source_ref    TEXT,
    q_confidence    REAL,
    q_is_corrected  INTEGER NOT NULL DEFAULT 0,
    q_corrected_text TEXT,
    answers         TEXT NOT NULL DEFAULT '[]',
    annotation      TEXT,
    optimization    TEXT,
    category_id     TEXT,
    entry_id        TEXT,
    direction       TEXT NOT NULL DEFAULT 'normal',  -- Phase 4：normal=面试官问 / reverse=我提问（反问环节）
    deleted         INTEGER NOT NULL DEFAULT 0,      -- Phase 4：软删除（校对误识别，保留审计）
    tag_ids         TEXT NOT NULL DEFAULT '[]',
    confidence      REAL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_qa_interview ON qa_items(interview_id);
CREATE INDEX IF NOT EXISTS idx_qa_category  ON qa_items(category_id);
CREATE INDEX IF NOT EXISTS idx_qa_entry     ON qa_items(entry_id);
CREATE INDEX IF NOT EXISTS idx_qa_deleted   ON qa_items(interview_id, deleted);

CREATE TABLE IF NOT EXISTS entries (
    id                TEXT PRIMARY KEY,
    category_id       TEXT NOT NULL,
    head_question     TEXT NOT NULL,
    question_variants TEXT NOT NULL DEFAULT '[]',
    core_extract      TEXT,
    optimization      TEXT,
    source_qa_ids     TEXT NOT NULL DEFAULT '[]',
    ask_times         INTEGER NOT NULL DEFAULT 0,
    tag_ids           TEXT NOT NULL DEFAULT '[]',
    first_asked_at    TEXT,
    last_asked_at     TEXT,
    updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entries_category ON entries(category_id);

CREATE TABLE IF NOT EXISTS tags (
    id    TEXT PRIMARY KEY,
    name  TEXT NOT NULL UNIQUE,
    color TEXT
);
"""


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def conn_ctx():
    """事务化连接：正常提交、异常回滚、始终关闭。"""
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    conn = get_conn()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
