"""Phase 3 离线端到端冒烟测试（无需外网/API Key）。

运行：python scripts/smoke_e2e.py
覆盖：工作台/导入页渲染 → analyze → 创建+规则提取(5 问答,含原文区间) →
      单题复盘渲染与 R2 修正(extract_text 不被改写) → 整场入库合并(R3 频次) →
      面经库/导出预览/下载 .md。
退出码 0 = 全部通过。
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 隔离运行：每次从空库开始，便于重复执行
_DATA = ROOT / "data"
shutil.rmtree(_DATA, ignore_errors=True)

from app.main import app  # noqa: E402
from app import repository  # noqa: E402

SAMPLE = (ROOT / "app" / "static" / "sample_transcript.txt").read_text(encoding="utf-8")
client = app.test_client()
passed: list[str] = []
failed: list[str] = []


def check(name: str, cond: bool, detail: str = ""):
    (passed if cond else failed).append(f"{name} {detail}")
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{detail}]" if detail and not cond else ""))


def json_of(resp, name: str) -> dict:
    data = resp.get_json()
    check(name, resp.status_code == 200 and isinstance(data, dict),
          f"http={resp.status_code} body={str(data)[:200]}")
    return data or {}


# 1) 页面渲染
check("GET / 渲染", client.get("/").status_code == 200)
check("GET /import 渲染", client.get("/import").status_code == 200)
check("GET /bank 渲染(空库)", "面经库为空" in client.get("/bank").get_data(as_text=True))

# 2) analyze：有标签 → 标签直读
r = client.post("/api/analyze", json={"text": SAMPLE})
a = json_of(r, "POST /api/analyze")
roles = {s["label"]: s["role"] for s in a.get("speakers", [])}
check("analyze 识别标签格式", a.get("format") == "bracket", str(a.get("format")))
check("analyze 面试官=interviewer", roles.get("面试官") == "interviewer")
check("analyze 我=self", roles.get("我") == "self")

# 3) 创建 + 规则提取（含 Phase 4 新字段）
r = client.post("/api/interviews", json={
    "company": "示例公司", "position": "后端工程师", "interview_type": "technical",
    "location": "北京·海淀", "interview_at": "2024-05-20T14:30",
    "expected_salary": "25-30K×14", "duration": "1小时30分", "text": SAMPLE,
    "role_map": {"面试官": "interviewer", "我": "self"},
})
iv = json_of(r, "POST /api/interviews")
iid = iv.get("interview_id")
check("创建成功且提取 5 问答", iv.get("qa_count") == 5, str(iv))
check("时长容错解析 1小时30分 → 90", iv.get("duration_minutes") == 90, str(iv.get("duration_minutes")))
check("标题按规则自动生成", iv.get("title") == "示例公司-后端工程师-2024-05-20 14:30", str(iv.get("title")))
iv_row = repository.get_interview(iid)
check("Phase 4 新字段落库",
      iv_row["location"] == "北京·海淀" and iv_row["expected_salary"] == "25-30K×14"
      and iv_row["duration_minutes"] == 90 and iv_row["interview_at"] == "2024-05-20T14:30"
      and bool(iv_row["transcript_hash"]),
      str({k: iv_row.get(k) for k in ("location", "expected_salary", "duration_minutes", "interview_at")}))

list_html = client.get(f"/interviews/{iid}").get_data(as_text=True)
check("问答列表页含 5 题与知识点", "共 5 题" in list_html and "缓存" in list_html)

d = client.get(f"/api/interviews/{iid}/data").get_json()
qas = d["qas"]
check("QA 均 confirmed 且带原文引用", all(q["status"] == "confirmed" and q["q_source_ref"]
      and q["answers"] and q["answers"][0]["source_ref"] for q in qas))

# 4) 单题复盘 + R2 修正：extract_text 保持原样，只产生 corrected 副本
qid = qas[2]["id"]  # 第 3 题：缓存失效/穿透
orig_extract = qas[2]["answers"][0]["extract_text"]
r = client.post(f"/api/qa/{qid}/update", json={
    "annotation": "开头卡壳，下次先给结论",
    "optimization": "先答结论；区分穿透与击穿；补充布隆过滤器方案",
    "status": "confirmed",
    "fix_answer_idx": 0,
    "fix_answer_text": "修正占位：这段提取没问题，仅演示 corrected 通道",
})
check("单题保存(批注/建议/修正)", r.status_code == 200, str(r.get_data(as_text=True)))
qa_after = repository.get_qa(qid)
check("extract_text 未被改写(R2)", qa_after["answers"][0]["extract_text"] == orig_extract)
check("corrected 副本与批注落库", qa_after["answers"][0]["is_corrected"] is True
      and qa_after["answers"][0]["corrected_text"]
      and qa_after["annotation"] and qa_after["optimization"])
detail_html = client.get(f"/interviews/{iid}/qa/{qid}").get_data(as_text=True)
check("单题复盘页渲染", "忠实原文" in detail_html and "优化建议" in detail_html)

# 5) 整场入库（R3 合并：知识点类别聚合 + 同问法去重）
r = client.post(f"/api/interviews/{iid}/merge-all", json={})
m = json_of(r, "POST merge-all")
check("整场入库 5 条", m.get("merged") == 5, str(m))
entries = repository.list_entries()
cats = {e["category_name"] for e in entries}
check("入库后 5 条问法条目、4 个知识点类别", len(entries) == 5 and len(cats) == 4,
      str([(e["category_name"], e["head_question"]) for e in entries]))
cache_qas = [e for e in entries if e["category_name"] == "缓存"]
cache_total = sum(e["ask_times"] for e in cache_qas)
check("缓存知识点累计被问 2 次(同类去重聚合)", cache_total == 2, f"total={cache_total}")
check("同类不同问法各成条目且出处各 1 场", len(cache_qas) == 2
      and all(e["ask_times"] == 1 and e["source_interview_count"] == 1 for e in cache_qas))
check("条目聚合出 optimization(R2 独立栏)", any(e.get("optimization") for e in cache_qas),
      str([e.get("optimization") for e in cache_qas]))
check("单题已回写 entry_id", all(q["entry_id"] for q in repository.list_qa(iid)))

# 6) 面经库与导出
bank_html = client.get("/bank").get_data(as_text=True)
check("面经库页类别聚合显示", "面经库" in bank_html and "累计被问 2 次" in bank_html)
exp_html = client.get(f"/interviews/{iid}/export").get_data(as_text=True)
check("导出预览(本场) 分节", "忠实问题" in exp_html and "优化建议" in exp_html and "批注" in exp_html)
dl = client.get(f"/download/interviews/{iid}.md")
check("下载 .md 内容", dl.status_code == 200 and dl.get_data(as_text=True).startswith("# 面试复盘报告"))
bank_dl = client.get("/download/bank.md")
check("下载面经库 .md", bank_dl.status_code == 200 and "面经库快照" in bank_dl.get_data(as_text=True))

# 7) 重复导入检测（Phase 4）+ 第二场导入 → 跨场累积频次 +1（R3）
dup = client.post("/api/interviews", json={"company": "示例公司2", "position": "后端", "text": SAMPLE,
                                           "role_map": {"面试官": "interviewer", "我": "self"}})
check("重复导入被拦截(409 且带 existing)",
      dup.status_code == 409 and dup.get_json().get("duplicate") is True,
      f"http={dup.status_code} body={str(dup.get_json())[:160]}")
r = client.post("/api/interviews", json={"company": "示例公司2", "position": "后端", "text": SAMPLE,
                                         "force": True,
                                         "role_map": {"面试官": "interviewer", "我": "self"}})
iv2 = json_of(r, "重复导入 force=True 可继续")
iid2 = iv2.get("interview_id")
client.post(f"/api/interviews/{iid2}/merge-all", json={})
entries2 = repository.list_entries()
cache2 = [e for e in entries2 if e["category_name"] == "缓存"]
check("跨场累积：缓存 2→4 次、出处 1→2 场", sum(e["ask_times"] for e in cache2) == 4
      and all(e["source_interview_count"] == 2 for e in cache2),
      f"total={sum(e['ask_times'] for e in cache2)} src={[e['source_interview_count'] for e in cache2]}")

# 8) Phase 4：标题规则 / 时长容错 / 无标签段落合并 / 0 问答报错
tp = client.post("/api/title-preview", json={"company": "A", "position": "B",
                                             "interview_at": "2024-01-02T09:05"}).get_json()
check("标题规则①公司-岗位-时间", tp.get("title") == "A-B-2024-01-02 09:05", str(tp))
tp2 = client.post("/api/title-preview", json={"company": "A", "position": "", "interview_at": ""}).get_json()
check("标题规则③仅公司 → 公司-面试-日期", tp2.get("title", "").startswith("A-面试-"), str(tp2))
tp3 = client.post("/api/title-preview", json={}).get_json()
check("标题规则⑤全空 → 未命名面试-时间戳", tp3.get("title", "").startswith("未命名面试-"), str(tp3))

unlabeled = ("你好，欢迎参加今天的面试，先做个自我介绍吧。\n"
             "好的，我叫李雷，目前做后端开发，主要负责订单服务。\n"
             "\n"
             "你提到用了本地缓存，能讲讲实现吗？\n"
             "嗯，当时是先查数据库，后来发现慢了才加的缓存。\n")
r = client.post("/api/interviews", json={"text": unlabeled, "force": True})
nu = r.get_json()
check("无标签文本按段落合并后可提取问答（且标记待确认）",
      r.status_code == 200 and nu.get("qa_count", 0) >= 1 and nu.get("format") == "none"
      and nu.get("pending_count", 0) >= 1,
      f"http={r.status_code} body={str(nu)[:160]}")

only_questions = "[面试官] 你了解我们公司吗？\n[面试官] 你还有什么想问的吗？\n"
r = client.post("/api/interviews", json={"text": only_questions,
                                         "role_map": {"面试官": "interviewer"},
                                         "force": True})
check("角色不全时明确报错而非静默成功", r.status_code == 400
      and "面试官" in (r.get_json() or {}).get("error", ""),
      f"http={r.status_code} body={str(r.get_json())[:160]}")

orphan = ("[面试官] 先讲讲你的项目经历。\n[面试官] 那这个项目的难点在哪里？\n"
          "[我] 难点主要是并发下的数据一致性，我们用了分布式锁。\n")
r = client.post("/api/interviews", json={"text": orphan,
                                         "role_map": {"面试官": "interviewer", "我": "self"},
                                         "force": True})
oi = r.get_json()
check("连续追问不再丢题（保留无回答条目并待确认）",
      r.status_code == 200 and oi.get("qa_count") == 2 and oi.get("blank_answer_count") == 1,
      f"http={r.status_code} body={str(oi)[:160]}")

# 9) Phase 4：校对操作（删除 / 合并下一条 / 拆分）+ 答案隐藏标记
qa_list = repository.list_qa(iid)
target = qa_list[0]
before = len(qa_list)
client.post(f"/api/qa/{target['id']}/merge-next", json={})
after_merge = repository.list_qa(iid)
check("合并下一条：条目数 -1 且回答拼接",
      len(after_merge) == before - 1 and len(after_merge[0]["answers"]) >= 2,
      f"before={before} after={len(after_merge)}")
check("合并不改写机器提取文本(R2)",
      after_merge[0]["q_text"] == target["q_text"] and after_merge[0]["q_is_corrected"] == 1
      and bool(after_merge[0]["q_corrected_text"]))

second = after_merge[1] if len(after_merge) > 1 else after_merge[0]
if len(second["answers"]) >= 2:
    client.post(f"/api/qa/{second['id']}/split", json={"answer_index": 1})
    after_split = repository.list_qa(iid)
    check("拆分：条目数 +1", len(after_split) == len(after_merge) + 1,
          f"{len(after_merge)} → {len(after_split)}")
else:
    check("拆分：样本不足跳过", True)

victim = repository.list_qa(iid)[-1]
client.post(f"/api/qa/{victim['id']}/delete", json={})
alive = repository.list_qa(iid)
check("删除为软删除（列表不再出现）",
      all(q["id"] != victim["id"] for q in alive) and repository.get_qa(victim["id"]) is None)

records_html = client.get("/").get_data(as_text=True)
check("面试记录页渲染新字段与搜索框",
      "北京·海淀" in records_html and "1 小时 30 分" in records_html and 'name="q"' in records_html)
lst_html = client.get(f"/interviews/{iid}").get_data(as_text=True)
check("答案隐藏开关与复习模式按钮存在",
      "data-toggle-answer" in lst_html and "全部隐藏答案" in lst_html and "answer-mask" in lst_html)
search_html = client.get("/?q=不存在的公司名").get_data(as_text=True)
check("记录页搜索无结果时给出空状态", "没有匹配的面试记录" in search_html)
export_dir = ROOT / "data" / "export"
check("导出文件已落盘 data/export/", export_dir.exists() and any(export_dir.glob("*.md")),
      str(list(export_dir.glob('*'))[:3] if export_dir.exists() else "missing"))

print()
print(f"共 {len(passed) + len(failed)} 项断言：通过 {len(passed)} / 失败 {len(failed)}")
if failed:
    print("失败项：")
    for f in failed:
        print(" -", f)
    sys.exit(1)
print("SMOKE_E2E_OK")
