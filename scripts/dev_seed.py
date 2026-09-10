"""开发/演示数据填充（可选工具）：python scripts/dev_seed.py

会清空 data/ 后写入：2 场面试（含近似问法用于验证相似关联）、知识库条目、
一份简历、一轮已完成的模拟面试。仅用于本地演示与界面自查。
"""
from __future__ import annotations

import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

shutil.rmtree(ROOT / "data", ignore_errors=True)

from app import repository, service  # noqa: E402
from app.config import ensure_dirs  # noqa: E402
from app.db import init_db  # noqa: E402
from app.migrations import run_migrations  # noqa: E402
from app.pipeline import postprocess, similar  # noqa: E402

ensure_dirs()
init_db()
run_migrations()

SAMPLE = (ROOT / "app" / "static" / "sample_transcript.txt").read_text(encoding="utf-8")
VARIANT = (
    "[面试官] 你好，请先简单介绍一下你自己。\n"
    "[我] 我是李雷，做后端大约五年，主要在电商交易链路，最近两年负责订单服务。\n"
    "[面试官] 讲讲你们项目里本地缓存是怎么实现的呢？\n"
    "[我] 用公司封装的 Redis 客户端做本地缓存，key 带版本号，发版时统一清理。\n"
    "[面试官] 缓存和数据库的一致性你们怎么保证？\n"
    "[我] 我们主要靠过期时间加后台刷新，强一致场景会先更新数据库再删缓存。\n"
    "[面试官] 举个例子说说你做过最有挑战的项目。\n"
    "[我] 订单服务拆分那次，把下单链路从单体里拆出来，用消息队列削峰，大促期间没出事故。\n"
    "[面试官] 你有什么想问我们的吗？\n"
    "[我] 想了解一下团队的技术栈和后续规划。\n"
    "[面试官] 我们后端以 Java 为主，正在做服务治理，欢迎你来。\n"
)

# 1) 两场面试（第二场为近似问法，用于验证相似关联）
first = service.run_import({
    "company": "示例电商", "position": "后端工程师", "location": "北京·海淀",
    "interview_at": "2024-05-20T14:30", "expected_salary": "25-30K×14", "duration": "58",
    "interview_type": "technical", "text": SAMPLE,
    "role_map": {"面试官": "interviewer", "我": "self"},
})
second = service.run_import({
    "company": "示例科技", "position": "后端开发", "location": "线上视频面",
    "interview_at": "2024-06-03T10:00", "expected_salary": "28-35K", "duration": "1小时5分",
    "interview_type": "technical", "text": VARIANT,
    "role_map": {"面试官": "interviewer", "我": "self"},
})
for iv in (first, second):
    iid = iv["interview_id"]
    for q in repository.list_qa(iid):
        if q["direction"] == "reverse" or q["status"] in ("auto", "pending"):
            repository.update_qa(q["id"], {"status": "confirmed"})
    print(iid, postprocess.merge_into_bank(iid))

# 2) 补一条优化建议，让知识库卡片更有内容
cache_entry = next((e for e in repository.list_entries()
                    if "缓存是怎么实现" in (e["head_question"] or "")), None)
if cache_entry:
    repository.update_entry(cache_entry["id"], {
        "optimization": "先给结论（用什么做缓存）→ 再讲触发时机与淘汰策略 → 最后补一个缓存击穿/穿透的实际案例与数据。"})

print("similar:", similar.recompute(use_ai=False))

# 3) 简历
from app.pipeline import mock_gen, resume as resume_mod  # noqa: E402

resume_text = ("李雷 · 后端工程师 · 5 年经验\n"
               "技能：Java、Spring、Redis、Kafka、MySQL、Docker、微服务、高并发\n"
               "项目：订单交易系统（日均 800 万单）、支付清结算平台（资金对账）\n"
               "负责订单系统拆分与性能优化，QPS 从 800 提升到 4000。\n")
kw = resume_mod.parse_keywords(resume_text)
rid = repository.create_resume("李雷-后端-5年.md", resume_text, kw["skills"], kw["projects"])
print("resume:", rid, kw["skills"][:6])

# 4) 一轮已完成的模拟面试（题库模式，带 1 条作答）
gen = mock_gen.build_questions(mode="bank", count=5, include_follow_up=True)
sid = repository.create_mock_session("题库随机模拟", "bank",
                                     {"count": len(gen["questions"]), "tts": "web"})
for i, q in enumerate(gen["questions"], start=1):
    q["session_id"] = sid
    q["seq"] = i
    repository.insert_mock_turn(q)
turns = repository.list_mock_turns(sid)
repository.update_mock_turn(turns[0]["id"], {
    "my_answer": "先给结论：用 Redis 做本地缓存，key 带版本号；再讲触发时机与淘汰策略。",
    "elapsed_sec": 96, "mark": "good"})
repository.update_mock_turn(turns[1]["id"], {"mark": "unsure", "elapsed_sec": 41})
repository.update_mock_session(sid, question_count=len(turns), answered_count=2,
                               status="finished", finished_at="2024-06-04T21:10:00")
print("mock session:", sid)
print("SEED_OK")
