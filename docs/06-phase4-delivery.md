# AI_Review 第四阶段交付说明 —— 导入字段 / 面试记录 / 答案隐藏 / UI 规范 / P0 缺陷修复

> 版本：v1.0 ｜ 阶段：Phase 4（已完成，待验收）｜ 对应需求：`docs/04-requirements-spec.md` v1.0（FR-01、FR-02、GLOBAL-UI）
> 验证：`scripts/smoke_e2e.py` **46/46 通过** + 真实服务端到端检查 **15 项全部通过** + `node --check app/static/app.js` 通过

---

## 1. 交付范围

| 需求 | 交付内容 |
|------|----------|
| FR-01 导入面试 | 新增 **面试地点 / 面试时间 / 期望薪资 / 时长** 四个字段（全部可空）；**标题 5 级降级生成 + 手动输入 + 「按规则重算」按钮**；时长输入容错；重复导入检测（409 + 前端确认后可强制导入） |
| FR-02 面试记录 | 记录页重构为**卡片式**（搜索 / 快筛 / 排序 / 统计条 / 空状态）；详情页**逐题「隐藏答案 / 显示答案」**+ **全部隐藏（复习模式）/ 全部显示**；隐藏状态本机记忆且**首屏无闪现** |
| GLOBAL-UI v1 | 全站 **Design Token 化**（颜色/间距/圆角/字号/阴影全部走 CSS 变量）、组件规范（按钮/卡片/标签/表格/表单/空状态/提示条）、导航信息架构（面试记录 / 导入面试 / 知识库）、响应式（≥1024 双栏、≤640 单列） |
| P0 缺陷修复 | ①连续追问丢题 ②0 问答静默成功 ③无标签文本被逐行切碎 ④缺少删/合并/拆分校对能力 ⑤重复导入无检测 ⑥LLM 异常返回 500 而非友好提示 |

## 2. 关键实现说明

### 2.1 标题生成规则（FR-01.2）
按优先级取第一个可用组合（`service.build_title`）：
1. `公司-岗位-YYYY-MM-DD HH:mm`（填了公司+岗位+时间）
2. `公司-岗位-YYYY-MM-DD`（有公司+岗位，未填时间）
3. `公司-面试-YYYY-MM-DD`（只有公司）
4. `面试-YYYY-MM-DD HH:mm`（只填了时间）
5. `未命名面试-YYYYMMDD-HHmm`（全空）

前端行为：输入公司/岗位/时间时，若标题仍是"自动态"则自动刷新；用户手动编辑后打上 `auto=0`，不再被自动覆盖；可点「按规则重算」显式回到自动标题。

### 2.2 时长容错（Q1 优化）
`58`、`58分钟`、`1h30m`、`1.5小时`、`1小时30分`（含全角数字）→ 统一存为分钟；超范围（>1440）或无法识别时**不阻断导入**，按留空处理并在响应里返回 `warnings`，导入页与结果提示都会展示。

### 2.3 答案隐藏（FR-02.3 + Q9 优化）
- 每题右侧 `隐藏答案 / 显示答案`，无刷新切换；隐藏态用占位条替换（不泄漏字数与首字）。
- 顶部「全部隐藏答案 / 全部显示答案」= 复习模式；状态写入 `localStorage`（键 `ai_review_hide_all:<iid>`、`ai_review_hidden_ids:<iid>`）。
- **无闪现**：`review_list.html` 在 `<head>` 内联脚本中读取状态并给 `<html>` 加 `hide-answers-all`，CSS 在首帧即隐藏答案，避免刷新瞬间露出答案。
- 复习模式下单题「显示答案」用 `is-shown` 覆盖全局隐藏，不破坏全局状态。

### 2.4 校对能力（P0-4）
新增三个接口与按钮：`删除`（软删除 `deleted=1`，保留审计，列表与统计自动排除）、`合并下一条`、`拆分`（prompt 输入拆分点）。
红线 R2 处理：**合并不会改写机器提取的 `q_text`**——合并结果写入 `q_corrected_text` 并置 `q_is_corrected=1`，界面显示"已合并/修正"并保留"原始提取"行；被合并的第二条软删除但数据留库可审计。

### 2.5 无标签文本处理链（P0-3）
`逐行切段 → 启发式角色 → 按角色归并（尊重问句边界）→ 规则提取 → 置信度封顶 0.7（标为待确认）`。
- 归并时以**原文连续切片**为合并文本，仍是逐字原文（R2）。
- 无标签路径提取出的问答一律 `pending`，避免"推断看起来像确定"（R1）。

### 2.6 连续追问不再丢题（P0-1）
- `_merge_same_label` / `merge_by_role` 增加"问句边界"判断：上一行以问号结尾，或新一行本身是问句（问号结尾/疑问引导词开头）→ **不合并**，各自成为独立轮次。
- `RuleExtractor` 保留没有回答的问题（置信度降为 0.5 → 待确认），记录页显示"无回答 N"。

## 3. 变更文件清单

| 文件 | 变更 |
|------|------|
| `app/db.py` | `interviews` 增 `location / interview_at / expected_salary / duration_minutes / transcript_hash`；`qa_items` 增 `direction / deleted`；新增索引 |
| `app/migrations.py` | **新增**：幂等迁移（`PRAGMA table_info` 检测 + `ALTER TABLE ADD COLUMN`），启动时自动执行 |
| `app/repository.py` | 新字段读写、`find_interview_by_hash`、`list_interviews(search/sort/only)`、`dashboard_stats`、软删除过滤、`soft_delete_qa / merge_with_next / split_qa / renumber_seqs`、出处分块统计（规避 SQLite 参数上限） |
| `app/service.py` | 标题生成与预览、时长解析、重复导入检测（`DuplicateImportError`）、无标签角色归并与置信度封顶、0 问答明确报错、新字段落库 |
| `app/pipeline/preprocess.py` | 无标签逐行切段、`merge_by_role`、问句边界不合并、启发式角色调整 |
| `app/pipeline/extract.py` | 保留无回答的问题（不再静默丢弃） |
| `app/pipeline/postprocess.py` | 无回答 → 待确认；条目带 `direction` |
| `app/exporter.py` | `save_export()`：导出内容落盘 `data/export/`（原 `EXPORT_DIR` 不再闲置） |
| `app/main.py` | 记录页搜索/排序/快筛、`/api/title-preview`、重复导入 409、LLM 错误 → 400、校对三接口、导出落盘、`duration/dt` 模板过滤器 |
| `app/templates/` | `base.html`（导航高亮 + 首屏状态注入 + head 块）、`index.html`（记录页重写）、`import.html`（新字段/标题/重复确认）、`review_list.html`（答案隐藏 + 校对）、`review_detail.html`（元信息 + 反问标识） |
| `app/static/style.css` | 全量 Token 化重写（含答案隐藏样式、响应式） |
| `app/static/app.js` | `postJSON` + 声明式 `data-post/data-confirm` |
| `scripts/smoke_e2e.py` | 28 → **46** 项断言（新字段、标题规则、时长容错、重复导入、无标签、连续追问、校对、搜索、导出落盘） |

## 4. 验证结果

**离线冒烟（Flask test client，46 项）**
- 全字段落库与展示、`1小时30分 → 90`、标题 `示例公司-后端工程师-2024-05-20 14:30`；
- 标题规则 ①③⑤ 分支、重复导入 409 + `force=True` 可继续；
- 无标签文本可提取且标 `pending`；角色不全时明确 400；
- **连续追问 = 2 条问答 + 1 条无回答**（修复前会丢成 1 条）；
- 合并/拆分/删除（软删除）行为与 R2 约束；记录页搜索空状态；导出落盘。

**真实服务（`python run.py` + HTTP，15 项）**
- `GET /`、`GET /import`、`GET /interviews/<id>`、`GET /bank`、单题复盘页均 200，且页面含新字段与隐藏/校对控件；
- 创建→标题自动生成、时长解析、重复导入 409、合并/删除接口、`.md` 下载（响应头 `filename*=UTF-8''`）、导出文件落盘、标题重算接口全部通过。

**前端脚本**：`node --check app/static/app.js` 通过。

## 5. 本次未做（保留至后续阶段）

- 深色主题与设置页（Phase 5，已确认口径 Q8：`settings.json` 原子写 + 优先级反转）；
- 知识库三专题/相似关联/对比页（Phase 6）；
- 模拟面试（Phase 7）；
- P1 遗留：列表分页（当前全量渲染，个人量级够用）、问答手动补录、`raw_transcript` 双份存储收敛、统计页。

## 6. 复测方法

```bash
python scripts/smoke_e2e.py          # 46/46
python run.py                        # 手工：/ → 导入 → 详情页逐题隐藏答案 → 校对按钮 → 导出
```
