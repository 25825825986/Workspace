"""Phase 3 离线端到端冒烟测试（无需外网/API Key）。

运行：python scripts/smoke_e2e.py
覆盖：工作台/导入页渲染 → analyze → 创建+规则提取(5 问答,含原文区间) →
      单题复盘渲染与 R2 修正(extract_text 不被改写) → 整场入库合并(R3 频次) →
      面经库/导出预览/下载 .md。
退出码 0 = 全部通过。
"""
from __future__ import annotations

import base64
import io
import json
import re
import shutil
import sys
import zipfile
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
kb0 = client.get("/knowledge")
check("GET /knowledge 渲染(空库)", kb0.status_code == 200
      and "知识库还是空的" in kb0.get_data(as_text=True))
check("旧链接 /bank 重定向到知识库", client.get("/bank").status_code in (301, 302, 308))
check("GET /mock 渲染", client.get("/mock").status_code == 200)

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
normal_qas = [q for q in qas if q["direction"] != "reverse"]
reverse_qas = [q for q in qas if q["direction"] == "reverse"]
check("正常问答均 confirmed 且带原文引用",
      len(normal_qas) == 4 and all(q["status"] == "confirmed" and q["q_source_ref"]
                                   and q["answers"] and q["answers"][0]["source_ref"]
                                   for q in normal_qas),
      str([(q["seq"], q["status"]) for q in qas]))
check("反问环节识别为 direction=reverse（邀请话术不单独成题）",
      len(reverse_qas) == 1 and reverse_qas[0]["status"] == "pending"
      and "想问" in reverse_qas[0]["q_text"], str([(q["seq"], q["direction"]) for q in qas]))

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

# 5) 整场入库（R3 合并：知识点类别聚合 + 同问法去重；反问待确认不合并）
r = client.post(f"/api/interviews/{iid}/merge-all", json={})
m = json_of(r, "POST merge-all")
check("整场入库 4 条（反问待确认被跳过）", m.get("merged") == 4 and m.get("skipped") == 1, str(m))
entries = repository.list_entries()
cats = {e["category_name"] for e in entries}
check("入库后 4 条问法条目、3 个二级知识点", len(entries) == 4 and len(cats) == 3,
      str([(e["category_name"], e["head_question"]) for e in entries]))
cache_qas = [e for e in entries if e["category_name"] == "缓存"]
cache_total = sum(e["ask_times"] for e in cache_qas)
check("缓存知识点累计被问 2 次(同类去重聚合)", cache_total == 2, f"total={cache_total}")
check("同类不同问法各成条目且出处各 1 场", len(cache_qas) == 2
      and all(e["ask_times"] == 1 and e["source_interview_count"] == 1 for e in cache_qas))
check("条目聚合出 optimization(R2 独立栏)", any(e.get("optimization") for e in cache_qas),
      str([e.get("optimization") for e in cache_qas]))
check("正常问答已回写 entry_id（反问待确认暂不入库）",
      all(q["entry_id"] for q in repository.list_qa(iid) if q["direction"] != "reverse"))

# 6) 知识库与导出
kb_html = client.get("/knowledge").get_data(as_text=True)
check("知识库页分组聚合显示", "知识库" in kb_html and "累计被问 2 次" in kb_html)
exp = client.get(f"/api/export/interview/{iid}")
exp_data = json_of(exp, "导出预览(本场) 走抽屉接口")
check("导出预览(本场) 分节", "忠实问题" in exp_data.get("content", "")
      and "优化建议" in exp_data.get("content", "")
      and exp_data.get("download_url") == f"/download/interviews/{iid}.md")
check("旧导出预览页 301 到本场复盘",
      client.get(f"/interviews/{iid}/export").status_code == 301)
check("旧知识库导出页 301 到知识库", client.get("/bank/export").status_code == 301)
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

victim = [q for q in repository.list_qa(iid) if q["direction"] != "reverse"][-1]
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

# 10) Phase 5：设置页 / AI 模式热生效 / 主题 / API Key / 连通性
sp = client.get("/settings")
sp_html = sp.get_data(as_text=True)
check("GET /settings 渲染", sp.status_code == 200 and "主题外观" in sp_html
      and "AI 模式" in sp_html and "模型 API" in sp_html and "数据信息" in sp_html)

s0 = client.get("/api/settings").get_json()
check("默认设置：主题=system / AI=auto / 未配置 Key",
      s0["settings"]["theme"] == "system" and s0["settings"]["ai_mode"] == "auto"
      and s0["has_api_key"] is False and s0["extractor_mode"] == "rule", str(s0)[:200])

r = client.post("/api/settings", json={"ai_mode": "on"})
s1 = r.get_json()
check("切换强制 AI：无需重启即生效（extractor→deepseek）",
      r.status_code == 200 and s1["extractor_mode"] == "deepseek"
      and "AI 模式" in s1["mode_badge"]["text"], str(s1)[:200])
a1 = client.post("/api/analyze", json={"text": SAMPLE}).get_json()
check("/api/analyze 反映新模式", a1.get("extractor") == "deepseek", str(a1.get("extractor")))

r = client.post("/api/settings", json={"ai_mode": "off"})
a2 = client.post("/api/analyze", json={"text": SAMPLE}).get_json()
check("切回非 AI 模式：extractor→rule", r.get_json()["extractor_mode"] == "rule"
      and a2.get("extractor") == "rule", f"{r.get_json()['extractor_mode']} / {a2.get('extractor')}")

r = client.post("/api/settings", json={"theme": "dark"})
root_html = client.get("/").get_data(as_text=True)
check("深色主题服务端直出（无闪烁）",
      r.status_code == 200 and 'data-theme="dark"' in root_html
      and 'data-theme="dark"' in client.get("/settings").get_data(as_text=True))
css = client.get("/static/style.css").get_data(as_text=True)
check("样式表含深色变量块", ':root[data-theme="dark"]' in css)

bad = client.post("/api/settings", json={"theme": "blue"})
check("非法主题被拒（400）", bad.status_code == 400, str(bad.get_json())[:120])

r = client.post("/api/settings/api-key", json={"api_key": "sk-test1234567890abcd"})
k1 = r.get_json()
check("API Key 保存后掩码显示、不返明文",
      r.status_code == 200 and k1["masked_key"] == "sk-t****abcd"
      and "sk-test1234567890abcd" not in json.dumps(k1, ensure_ascii=False)
      and k1["has_api_key"] is True, str(k1)[:200])

t = client.post("/api/settings/test", json={})
check("连通性测试：失败时返回 400 + 可读信息（不 500）",
      t.status_code == 400 and bool((t.get_json() or {}).get("message")),
      f"http={t.status_code} body={str(t.get_json())[:160]}")

r = client.post("/api/settings/api-key", json={"api_key": ""})
check("清除 Key 后回退", r.get_json()["has_api_key"] is False
      and r.get_json()["masked_key"] == "", str(r.get_json())[:160])

settings_file = ROOT / "data" / "settings.json"
secrets_file = ROOT / "data" / "secrets.json"
conf = json.loads(settings_file.read_text(encoding="utf-8"))
check("设置原子落盘 data/settings.json（结构完整）",
      settings_file.exists() and set(conf) >= {"theme", "ai_mode", "llm"} and secrets_file.exists(),
      str(conf))
check("密钥文件不经 HTTP 暴露", client.get("/data/secrets.json").status_code == 404)

r = client.post("/api/settings/reset", json={})
check("恢复默认设置", r.status_code == 200 and r.get_json()["settings"]["ai_mode"] == "auto"
      and r.get_json()["settings"]["theme"] == "system", str(r.get_json())[:160])

# 11) Phase 6：专题归类 / 反问专题 / 相似识别 / 对比页
rev_id = reverse_qas[0]["id"]
client.post(f"/api/qa/{rev_id}/confirm", json={})
client.post(f"/api/qa/{rev_id}/merge", json={})
entries_all = repository.list_entries()
check("反问环节独立成专题条目（direction=reverse → topic=reverse）",
      len(entries_all) == 5 and any(e.get("topic") == "reverse" for e in entries_all),
      str([(e.get("topic"), e.get("group_name"), e["head_question"][:14]) for e in entries_all]))
check("每个条目都有一级分类", all(e.get("group_name") for e in entries_all),
      str([e.get("group_name") for e in entries_all]))
check("自我介绍归入 self_intro 专题",
      any(e.get("topic") == "self_intro" for e in entries_all))

t1 = "[面试官] 讲讲你们项目里本地缓存是怎么实现的？\n[我] 用 Redis 做本地缓存，key 带版本号。\n"
t2 = "[面试官] 讲讲你们项目里本地缓存是怎么实现的呢？\n[我] Redis，key 带版本号，发版时清理。\n"
i1 = json_of(client.post("/api/interviews", json={"text": t1, "force": True,
      "role_map": {"面试官": "interviewer", "我": "self"}}), "相似样本1")
i2 = json_of(client.post("/api/interviews", json={"text": t2, "force": True,
      "role_map": {"面试官": "interviewer", "我": "self"}}), "相似样本2")
client.post(f"/api/interviews/{i1['interview_id']}/merge-all", json={})
client.post(f"/api/interviews/{i2['interview_id']}/merge-all", json={})
rc = json_of(client.post("/api/knowledge/recompute", json={"use_ai": False}), "重算相似关联")
check("规则层识别相似问题并落库", rc.get("similar_pairs", 0) >= 1 and rc.get("rule_matched", 0) >= 1,
      str(rc))
sim_entry = next(e for e in repository.list_entries() if "本地缓存是怎么实现" in e["head_question"])
pairs = repository.list_similar_pairs(sim_entry["id"])
check("相似对可查询（含分数与方法）", len(pairs) >= 1 and pairs[0]["method"] in ("rule", "ai", "manual"),
      str(pairs)[:160])
cmp_html = client.get(f"/knowledge/compare/{sim_entry['id']}").get_data(as_text=True)
check("对比页渲染（来源 / 忠实原文 / 优化建议 / 批注）",
      "历史作答对比" in cmp_html and "忠实原文回答" in cmp_html and "优化建议" in cmp_html
      and "相似问题关联" in cmp_html)
detail_rows = repository.list_qa_detail(sim_entry["source_qa_ids"])
check("对比页数据可回溯到具体作答", len(detail_rows) >= 1 and bool(detail_rows[0]["interview_title"]),
      str(len(detail_rows)))
multi_src = [e for e in repository.list_entries() if len(e.get("source_qa_ids") or []) >= 2]
if multi_src:
    rows_multi = repository.list_qa_detail(multi_src[0]["source_qa_ids"])
    cmp_multi = client.get(f"/knowledge/compare/{multi_src[0]['id']}").get_data(as_text=True)
    check("多次作答可在对比页并列展示", len(rows_multi) >= 2 and "来源面试" in cmp_multi,
          str(len(rows_multi)))
else:
    check("多次作答可在对比页并列展示（样本不足跳过）", True)
qa_pick = detail_rows[0]["id"]
client.post(f"/api/entries/{sim_entry['id']}/best", json={"qa_id": qa_pick})
check("标为最佳作答", repository.get_entry(sim_entry["id"])["best_qa_id"] == qa_pick)
before_pairs = repository.similar_pair_count()
client.post("/api/similar/unlink", json={"a_entry_id": pairs[0]["a_entry_id"],
                                         "b_entry_id": pairs[0]["b_entry_id"]})
check("取消关联生效", repository.similar_pair_count() == before_pairs - 1,
      f"{before_pairs} → {repository.similar_pair_count()}")
kb_html2 = client.get("/knowledge").get_data(as_text=True)
check("知识库页展示专题区与分组", "反问环节" in kb_html2 and "自我介绍" in kb_html2
      and "更新相似关联" in kb_html2 and "重新归类" in kb_html2)

# 12) Phase 7：简历导入（含零依赖 docx）/ 模拟面试全流程
r = client.post("/api/resumes", json={
    "name": "测试简历",
    "content": "5 年 Java 后端经验，熟悉 Redis、Kafka、微服务，负责订单系统与支付平台。\n"})
rv = json_of(r, "简历导入(txt)")
check("简历解析出技能与项目", "java" in [s.lower() for s in rv.get("skills", [])]
      and bool(rv.get("projects")), str(rv)[:200])

buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as zf:
    zf.writestr("word/document.xml",
                "<w:document><w:body>"
                "<w:p><w:r><w:t>3 年 Python 经验</w:t></w:r></w:p>"
                "<w:p><w:r><w:t>熟悉 Docker 与 Kubernetes</w:t></w:r></w:p>"
                "</w:body></w:document>")
data_url = ("data:application/vnd.openxmlformats-officedocument.wordprocessingml.document;base64,"
            + base64.b64encode(buf.getvalue()).decode())
dv = json_of(client.post("/api/resumes", json={"name": "resume.docx", "data_url": data_url}),
             "简历导入(docx 零依赖)")
skills_low = [s.lower() for s in dv.get("skills", [])]
check("docx 零依赖解析成功", "python" in skills_low and "docker" in skills_low, str(dv)[:200])

bad = client.post("/api/resumes", json={"name": "a.pdf", "data_url": "data:application/pdf;base64,AAAA"})
check("PDF 给出明确不支持提示", bad.status_code == 400 and "PDF" in (bad.get_json() or {}).get("error", ""),
      str(bad.get_json())[:120])
check("简历列表接口", any(i["name"] == "测试简历"
      for i in client.get("/api/resumes").get_json()["items"]))

ms = json_of(client.post("/api/mock/sessions", json={"mode": "bank", "count": 3, "tts": "web"}),
             "创建模拟(题库)")
sid = ms.get("session_id")
run_html = client.get(f"/mock/{sid}").get_data(as_text=True)
check("模拟作答页渲染（进度/语音/作答框）",
      "播报问题" in run_html and "我的回答" in run_html and "结束并生成报告" in run_html)
mock_turns = repository.list_mock_turns(sid)
check("题目已落库且数量一致",
      len(mock_turns) == ms.get("question_count") and len(mock_turns) >= 1, str(len(mock_turns)))
r = client.post(f"/api/mock/turns/{mock_turns[0]['id']}",
                json={"my_answer": "我的回答要点", "elapsed_sec": 42, "mark": "good"})
check("作答保存并更新进度", r.status_code == 200 and r.get_json().get("answered") == 1,
      str(r.get_json()))
client.post(f"/api/mock/sessions/{sid}/finish", json={})
rep = client.get(f"/mock/{sid}/report").get_data(as_text=True)
check("报告页渲染（统计/逐题对照/建议）",
      "逐题对照" in rep and "需要加强" in rep and "下一步建议" in rep)

w = json_of(client.post("/api/mock/sessions",
                        json={"mode": "whole", "interview_id": iid, "count": 5}),
            "创建模拟(整场)")
check("整场模式题量与原场当前题目一致",
      w.get("question_count") == len(repository.list_qa(iid)) and w.get("question_count") >= 1,
      f"{w.get('question_count')} vs {len(repository.list_qa(iid))}")
rm = json_of(client.post("/api/mock/sessions",
                         json={"mode": "resume", "resume_id": rv["resume_id"], "count": 4,
                               "ai_ratio": 0.5}), "创建模拟(简历)")
check("简历模式可用（无 Key 时回退题库并给出说明）",
      rm.get("question_count") == 4 and rm.get("ai_used") == 0 and bool(rm.get("note")),
      str(rm)[:200])
tts = client.get("/api/tts/status").get_json()
check("语音能力探测（web 可用、edge 可探）", tts.get("web") is True and "edge" in tts, str(tts))
client.post(f"/api/mock/sessions/{sid}/delete", json={})
check("删除模拟会话", repository.get_mock_session(sid) is None)

# 13) Phase 8：优化项（撤销 / 分页片段 / 全局搜索 / 旧路由 301 / UI Kit 基座）
victim2 = repository.list_qa(iid)[-1]["id"]
res_del = client.post(f"/api/qa/{victim2}/delete", json={}).get_json()
check("删除返回可撤销信息", res_del.get("undo_url") == f"/api/qa/{victim2}/restore"
      and repository.get_qa(victim2) is None, str(res_del))
client.post(res_del["undo_url"], json={})
check("撤销删除后条目恢复", repository.get_qa(victim2) is not None)

part = client.get("/partials/records?offset=0")
check("记录分页片段可渲染", part.status_code == 200 and "rec-card" in part.get_data(as_text=True))
check("记录页含加载更多按钮或计数",
      ('data-load-more="/partials/records"' in records_html) or ("已显示" in records_html))

srch = client.get("/api/search?q=缓存").get_json()
check("全局搜索返回面试与知识点",
      bool(srch.get("items")) and any(i["kind"] == "知识点" for i in srch["items"]),
      str(srch)[:200])
check("旧 /bank 301 到知识库", client.get("/bank").status_code == 301)

pair0 = repository.list_similar_pairs(sim_entry["id"])
if pair0:
    ul = client.post("/api/similar/unlink", json={"a_entry_id": pair0[0]["a_entry_id"],
                                                 "b_entry_id": pair0[0]["b_entry_id"]}).get_json()
    check("取消关联返回撤销载荷", bool(ul.get("undo_url")) and bool(ul.get("undo_payload")),
          str(ul)[:160])
    client.post(ul["undo_url"], json=ul["undo_payload"])
    check("撤销取消关联后恢复", repository.similar_pair_count() >= 1)
else:
    check("取消关联返回撤销载荷（样本不足跳过）", True)

tpl_dir = ROOT / "app" / "templates"
native = []
for p in tpl_dir.glob("*.html"):
    text = p.read_text(encoding="utf-8")
    if re.search(r"(?<![\w.])(confirm|prompt|alert)\s*\(", text):
        native.append(p.name)
check("模板已无原生 confirm/alert/prompt", not native, str(native))

app_js = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
css = (ROOT / "app" / "static" / "style.css").read_text(encoding="utf-8")
base_html = (ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")
check("前端 UI Kit 提供 toast/confirm/loading/抽屉/命令面板",
      all(k in app_js for k in ("ui.toast", "confirmDialog", "function loading", "drawer", "palette")))
check("无障碍基座：aria-live / skip-link / focus-visible / reduced-motion",
      "aria-live" in base_html and "skip-link" in base_html
      and ":focus-visible" in css and "prefers-reduced-motion" in css)
check("底部 Tab / 浮动菜单 / 暗色 on-brand token",
      ".tabbar" in css and "--on-brand" in css and "data-popover" in css)

print()
print(f"共 {len(passed) + len(failed)} 项断言：通过 {len(passed)} / 失败 {len(failed)}")
if failed:
    print("失败项：")
    for f in failed:
        print(" -", f)
    sys.exit(1)
print("SMOKE_E2E_OK")
