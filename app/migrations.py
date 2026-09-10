"""幂等数据库迁移：为既有库补列（Phase 4 起）。

设计：不改动既有列、不丢数据；启动时对比 `PRAGMA table_info` 与目标定义，
缺失则 `ALTER TABLE ... ADD COLUMN`。可重复执行。
"""
from __future__ import annotations

from .db import conn_ctx, init_db

# 目标增量列（表 → [(列名, 定义)])
TARGET_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "interviews": [
        ("location", "TEXT"),
        ("interview_at", "TEXT"),
        ("expected_salary", "TEXT"),
        ("duration_minutes", "INTEGER"),
        ("transcript_hash", "TEXT"),
    ],
    "qa_items": [
        ("direction", "TEXT NOT NULL DEFAULT 'normal'"),
        ("deleted", "INTEGER NOT NULL DEFAULT 0"),
    ],
    "entries": [
        ("topic", "TEXT"),
        ("group_name", "TEXT"),
        ("best_qa_id", "TEXT"),
    ],
}


def _columns_of(conn, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def run_migrations() -> list[str]:
    """执行迁移，返回本次实际新增的列（形如 `interviews.location`）。"""
    init_db()  # 确保表存在（新库直接按最新 DDL 建表）
    applied: list[str] = []
    with conn_ctx() as conn:
        for table, columns in TARGET_COLUMNS.items():
            have = _columns_of(conn, table)
            if not have:
                continue  # 表不存在：由 init_db 的完整 DDL 负责
            for name, ddl in columns:
                if name not in have:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
                    applied.append(f"{table}.{name}")
    return applied
