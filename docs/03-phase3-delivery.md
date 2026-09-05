# AI_Review 第三阶段交付说明 —— 最小闭环骨架（可运行）

> 版本：v1.0 ｜ 阶段：第三阶段（工程骨架 + 数据层 + 三步管线 + 可跑页面雏形）
> 前置确认：方案 2（Python 最简栈）；无 LLM Key，先以 规则 + mock 打通闭环。

## 1. 交付范围与关键偏差

### 1.1 技术栈执行偏差（框架：FastAPI → Flask）
- docs/02 方案二原定 **FastAPI + uvicorn**；但本机环境离线（PyPI/镜像全部不可达，无法联网安装
  fastapi/uvicorn），而系统已内置 **Flask 3.1 + Jinja2 + python-dotenv**。
- 决定以 **Flask** 落地：同属"方案二 Python 最简栈"，服务端 Jinja2 渲染 + 原生 JS + SQLite 完全不变，
  模板零改动；后续如需切回 FastAPI，仅替换 `app/main.py` 路由层（页面/API 逻辑一一对应）。
- 影响：`requirements.txt` 改为 `flask`；启动命令 `python run.py`。

### 1.2 数据模型落地的两处细化（相对 docs/01 §5.3）
1. `qa_items` 增加 `optimization` 工作区列：单题复盘页"优化建议"先写在本场问答上，
   **纳入面经库时聚合进 `entries.optimization`（仅当条目为空时写入，不覆盖既有沉淀）**；
   绝不写入 extract 字段（红线 R2 不变）。
2. Tag/统计/设置等 P0 外能力仅建表（tags）或未建（统计页 P1），不在本阶段 UI 暴露。

## 2. 已实现功能（对照原型 P0）

| 原型 | 实现 |
|------|------|
| F01 导入 | /import 三步向导：元信息 + 粘贴/上传形态（粘贴 + 载入示例）+ 标签嗅探（bracket/colon/ab/speaker/none） |
| F02 角色识别 | 有标签=标签直读；无标签=规则模式低置信启发推断（source=inferred，UI 可逐说话人改角色）；预留 DeepSeek LLM 推断分支（有 Key 自动启用） |
| F03/F04 切分+忠实提取 | rule/mock：按轮次确定性切分，文本=轮次原文，source_ref 为原文字符区间；DeepSeek 路径经归一化逐字对齐回填区间，对齐失败置 pending |
| F05 问答列表 | /interviews/{id}：列表 + 待确认高亮 + 单条确认/全部确认 + 入库/整场入库 + 导出 |
| F06 单题复盘 | /interviews/{id}/qa/{qid}：忠实原文只读双栏；优化建议/批注独立；问题与每条回答可"标记修正"（产生 corrected 副本，extract_text 保留） |
| F07 知识点归类 | 关键词建议 + 导入即建 Category(norm 去重) + 复盘页可改类别 |
| F08 纳入面经库 | merge：同类别同问法 → 频次+1/出处追加/变体并入；不同问法 → 同类别下新条目；跨场复用既有条目（R3） |
| F09 本地持久化 | SQLite 单文件 data/app.db + 原文 data/raw/{id}.txt |
| F10 导出 | 本场报告 / 面经库快照：预览页 + 下载 .md（忠实原文与优化建议分节） |

## 3. 验证结果（离线，无 API Key）

- `python scripts/smoke_e2e.py`：**28/28 断言通过**，覆盖：
  - analyze 识别 `[面试官]/[我]` → interviewer/self；
  - 规则提取 5 问答（含口误卡壳原文、source_ref 区间）；
  - **R2**：单题修正后 `extract_text` 保持原样，仅产生 corrected 副本与独立优化建议/批注；
  - **R3**：整场入库 → 5 问法条目/4 知识点；"缓存"同类去重聚合 2 次；第二场导入后再入库 → 累计 4 次、出处 2 场；
  - 面经库/导出预览/两处 .md 下载均 200 且分节正确。
- 真实服务启动：`python run.py` → `GET /`(200)、`/static/style.css`(200)、404 处理、`POST /api/analyze`(200) 均验证通过。

## 4. 如何运行

```bash
python -m pip install -r requirements.txt   # 有网环境一次性安装
python run.py                               # http://127.0.0.1:8000
# 可选 AI：复制 .env.example 为 .env 填 DEEPSEEK_API_KEY → 无标签文本自动 AI 推断角色+AI 抽取
```

## 5. 已知边界（本阶段有意未做）

- 无标签文本在 规则/mock 模式仅低置信启发推断（产品要求的"AI 内容推断"依赖配置 LLM Key）；
- 统计页（P1-F12）、设置页、标签管理、预设题型模板（P1-F14）未实现；
- 面经库为"知识点类别分组 + 问法条目"两级展示，类别卡片式 UI 深化留待 P1；
- 单题"删除/拆分/合并相邻问答"等校对操作未做全量按钮（数据层已支持 status/归属修正）。

## 6. 下一阶段建议（待确认）

1. **LLM 接通验证**：配置 DeepSeek Key 后对无标签转写做真实 AI 推断 + 抽取联调（llm.py 已就绪）；
2. 校对增强：合并/拆分、批量改类别、删除误识别；
3. 统计页与"待完善知识点"（批注密集）提示；
4. 面经库"知识点卡片 + 展开出处/各场批注"深化与检索。
