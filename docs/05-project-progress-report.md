# AI_Review 项目进度报告与变更清单

> 版本：v1.0 ｜ 状态：**待审批** ｜ 对应需求：`docs/04-requirements-spec.md` v1.0
> 结论：**Phase 1–3 已完成并通过验证**；本轮 5 项新需求中，**0 项已完成、2 项部分具备基础（FR-01/FR-02）、3 项未开始（FR-03/FR-04/FR-05）**。

---

## 1. 结论速览

| 模块 | 需求编号 | 当前状态 | 完成度（估） |
|------|----------|----------|--------------|
| 原型 / 技术方案 / Bug 修复 | — | ✅ 已完成 | 100% |
| 导入面试（元信息 + 转写） | FR-01 | 🟡 部分具备（字段不全、标题不可手改） | 55% |
| 面试记录（列表 / 查看 / 答案隐藏） | FR-02 | 🟡 部分具备（有列表与详情，无隐藏答案开关） | 45% |
| 知识库（专题 / 分类 / 频次 / 相似 / 对比） | FR-03 | 🟠 基础版（有分类聚合与频次，无专题/相似/对比） | 30% |
| 模拟面试（整场 / 简历 / AI 出题 / 语音） | FR-04 | ⛔ 未开始 | 0% |
| 设置（主题 / API / AI 模式） | FR-05 | ⛔ 未开始（仅 `.env` 配置 + 后端模式开关） | 10% |
| 全局页面美观统一 | GLOBAL-UI | 🟠 基础样式可用，未做统一规范与主题 | 35% |

---

## 2. 已完成交付（Phase 1–3 + Bug 修复）

| 阶段 | 交付物 | 验证证据 |
|------|--------|----------|
| Phase 1 | `docs/01-product-prototype-design.md` v0.2（三红线、功能优先级、页面、数据模型） | 用户确认（含 3 处修订） |
| Phase 2 | `docs/02-tech-stack-design.md`（方案二：Python 最简栈；Flask 偏差记录于 docs/03） | 用户确认走方案 2 |
| Phase 3 | 可运行骨架：导入向导 → 规则/mock/DeepSeek 抽取 → 问答列表 → 单题复盘 → 面经库 → Markdown 导出；SQLite 数据层；三步管线 | `scripts/smoke_e2e.py` 28/28 通过；真实服务 curl 验证 |
| Bug 修复 | 下载接口挂起（中文文件名 → Content-Disposition latin-1）修复 + 文档 `docs/bug_fix.md` | 修复前后 curl 对比；边界用例脚本；回归 28/28 |
| 现有代码规模 | 17 条路由、8 个模板、5 张表（interviews/categories/qa_items/entries/tags）、3 层管线 | git 提交：`7490360` 初版、`8f5b7f8` 下载 bug 修复 |

**未提交改动提醒**：`app/exporter.py` 有 1 行未提交修改（`ROLE_LABEL.get(...) or sp.get("label") or sid`，健壮性改进，非我所改）。建议随下次提交一并入库。

---

## 3. 现状快照（用于差距比对）

**路由（17）**：`/`、`/import`、`/interviews/<iid>`、`/interviews/<iid>/qa/<qid>`、`/bank`、`/interviews/<iid>/export`、`/bank/export`、`/download/interviews/<iid>.md`、`/download/bank.md`、`/api/analyze`、`/api/interviews`、`/api/qa/<qid>/update`、`/api/qa/<qid>/confirm`、`/api/interviews/<iid>/confirm-all`、`/api/qa/<qid>/merge`、`/api/interviews/<iid>/merge-all`、`/api/interviews/<iid>/data`

**模板（8）**：`base / index / import / review_list / review_detail / bank / export / 404`

**`interviews` 现有字段**：`id, title, company, position, interview_type, date, raw_transcript, transcript_format, status, parser_version, speaker_map, created_at, updated_at`
→ **缺少**：`location`、`interview_at`（精确时间）、`expected_salary`、`duration_minutes`

**`qa_items` 现有字段**：`id, interview_id, seq, parent_id, status, q_text, q_speaker_id, q_source_ref, q_confidence, q_is_corrected, q_corrected_text, answers, annotation, optimization, category_id, entry_id, tag_ids, confidence, created_at, updated_at`
→ **缺少**：`direction`（反问环节角色反转）

**已有可复用能力**：说话人标签直读 + 角色确认、低置信推断、忠实原文区间引用（R2）、知识点 norm 去重与频次聚合（R3）、跨场出处追溯、单题修正通道、Markdown 导出、LLM Provider 抽象（rule/mock/deepseek + function calling）、25 条知识点关键词表。

**尚无的能力**：答案隐藏/复习模式、三个专题（自我介绍/个人发展/反问）、相似问题识别（现仅字符串完全归一匹配）、对比页、模拟面试、简历导入、TTS、设置页与主题切换、深色主题、服务端设置持久化。

---

## 4. 需求–现状差距矩阵

| 需求点 | 现状 | 差距 | 改动类型 | 主要涉及 |
|--------|------|------|----------|----------|
| FR-01 新增地点/时间/薪资/时长 | 无这些字段 | 4 个字段 + 表单 + 展示 | 数据+页面 | `db.py`、`migrations.py`(新)、`service.py`、`import.html`、`index.html`、`review_list.html` |
| FR-01 标题自动生成/手动输入 | 服务端自动生成，前端无输入框 | 生成规则升级 + 前端输入 + 重算按钮 | 逻辑+页面 | `service.py`、`import.html` |
| FR-02 面试记录页（卡片式检索） | 有表格型"历史复盘" | 重构为卡片 + 搜索/筛选/排序 + 统计条 | 页面 | `index.html`、`main.py` |
| FR-02 每题隐藏/显示答案 | 无 | 每题开关 + 批量 + 状态记忆 + 复习模式 | 页面+JS | `review_list.html`、`static/app.js`、`static/style.css` |
| FR-03 三专题单独总结 | 仅普通分类行 | 专题区 + 归类规则 + 专题级汇总 | 逻辑+页面 | `repository.py`、`extract.py`、`bank.html`(改版) |
| FR-03 关键词分类（项目/技术栈/团队协作…） | 25 条关键词，无"团队协作""技术栈"大类 | 关键词扩充 + 两级分类视图 | 逻辑+页面 | `extract.py`、`repository.py`、`bank.html` |
| FR-03 询问频次 | 已有 `ask_times` / 类别聚合 | 专题级与一级分类级汇总展示 | 页面 | `main.py`、`bank.html` |
| FR-03 相似问题识别关联 | 仅 norm 完全相等 | 相似度算法 + `similar_pairs` 表 + 人工确认 | 新增模块 | `pipeline/similar.py`(新)、`repository.py`、`db.py` |
| FR-03 点击查看完整对比 | 无 | 对比页（多版本作答并列） | 新增页面 | `compare.html`(新)、`main.py` |
| FR-03 反问环节（角色反转） | 无 `direction` 概念 | 抽取 + 建模 + 专题 | 数据+逻辑 | `qa_items.direction`、`extract.py`、`postprocess.py` |
| FR-04 整场模拟 | 无 | 会话表 + 出题 + 作答 + 报告 | 新增模块 | `mock_*.py/html`(新)、`db.py` |
| FR-04 简历导入 + AI 出题 | 无 | 简历解析 + 题库随机 + AI 生成 | 新增模块 | `resumes` 表、`pipeline/mock_gen.py`(新) |
| FR-04 语音包 | 无 | TTS Provider（Web Speech / Piper / edge-tts） | 新增模块 | `static/app.js`、`app/tts.py`(新)、设置页 |
| FR-05 主题切换 | 单浅色硬编码 | CSS 变量化 + 深色主题 + 持久化 | 全局改造 | `static/style.css`、`base.html`、`main.py` |
| FR-05 模型 API 配置页 | 仅 `.env` | 设置页 + 持久化 + 连通性测试 | 新增页面 | `settings.json`、`main.py`、`settings.html`(新) |
| FR-05 AI/非 AI 模式 | 后端 `EXTRACTOR` 已有 | UI 开关 + 全局行为联动 + 徽标 | 逻辑+页面 | `config.py`、`service.py`、所有页面顶栏 |
| GLOBAL-UI 简洁美观 | 基础样式，未统一 | 设计 token 落地 + 组件规范 + 响应式 | 全局改造 | `style.css`、全部 8 个模板 |

---

## 5. 详细变更清单（按文件）

### 5.1 数据层
- `app/db.py`：`interviews` 增 4 列；`qa_items` 增 `direction`；新增 5 张表（`similar_pairs`、`settings` 或 JSON、`resumes`、`mock_sessions`、`mock_turns`）。
- `app/migrations.py`（**新增**）：`PRAGMA table_info` 检测 + `ALTER TABLE ADD COLUMN` 幂等迁移；启动时执行并写 `schema_version`。
- 迁移风险：**低**（当前 `data/` 为空）；仍保留迁移能力以满足后续增量升级。

### 5.2 业务与仓储
- `app/repository.py`：新增 `update_interview_fields`、`list_interviews_filtered(search/sort)`、`similar_pairs` CRUD、`settings` 读写、`resumes`、`mock_sessions/turns` CRUD；扩展 `list_entries` 支持专题/一级分类聚合与频次汇总。
- `app/service.py`：导入时透传新字段 + 标题生成规则（含"重算"接口）；模式（AI/非 AI）读取改为"设置优先、`.env` 兜底"。
- `app/pipeline/extract.py`：关键词表扩充（团队协作、项目、个人发展、反问）；新增 `direction` 判定（仅当"我提问、面试官回答"标签明确时置 `reverse`，不猜测）。
- `app/pipeline/similar.py`（**新增**）：规则相似度（归一化 + difflib 比率 + 中文 2-gram Jaccard + 技术名词加权）；AI 灰区判定（AI 模式）；结果写 `similar_pairs`，带 `method` 与 `score`。
- `app/exporter.py`：导出头部补全新字段（地点/时间/时长/期望薪资）；吸收现有未提交小改。
- `app/main.py`：新增页面路由 `/settings`、`/knowledge`（或改造 `/bank`）、`/knowledge/compare/<entry_id>`、`/mock`、`/mock/<sid>`、`/mock/<sid>/report`、`/resumes`；新增 API：设置读写/测试连接/模式切换、相似计算与确认、模拟会话推进与保存、简历上传解析。

### 5.3 页面（模板）
| 模板 | 动作 | 要点 |
|------|------|------|
| `base.html` | 改造 | 6 项导航、顶栏模式徽标、主题脚本首屏注入、轻提示组件 |
| `index.html` | 改造 | 卡片式面试记录 + 搜索/筛选/排序 + 统计条 |
| `import.html` | 改造 | 新字段表单（含日期时间、时长数字校验）+ 标题输入与"重算" |
| `review_list.html` | 改造 | 元信息头 + 每题"隐藏/显示答案"开关 + 批量复习模式 |
| `review_detail.html` | 微调 | 视觉统一；反问条目展示方向标识 |
| `bank.html` | 改造 | 三专题区 + 一级分类分组 + 频次 + "相似 N 条"入口 |
| `compare.html` | **新增** | 相似问答并列对比（原文/建议/批注/来源/时间线） |
| `settings.html` | **新增** | 主题 / 模型 API / 模式切换 / 数据管理 |
| `mock_setup.html`、`mock_run.html`、`mock_report.html` | **新增** | 模拟配置、逐题作答（含语音）、模拟报告 |
| `export.html`、`404.html` | 微调 | 视觉统一 |

### 5.4 前端静态资源
- `static/style.css`：全量 token 化（颜色/间距/圆角/阴影/字号），新增 `[data-theme="dark"]`，组件规范化（按钮/卡片/表格/标签/空状态/骨架屏）。
- `static/app.js`：答案隐藏状态管理（localStorage）、主题切换、通用 fetch 封装与错误提示、TTS 播放封装。

### 5.5 测试
- `scripts/smoke_e2e.py`：追加断言——新字段落库与展示、标题生成/覆盖、答案隐藏接口/状态（前端逻辑以手工+JS 断言）、专题归类、相似关联、设置读写与模式切换、模拟会话流程。
- 新增 `scripts/smoke_similar.py`（相似度阈值回归）与手工验收清单（写入交付文档）。

---

## 6. 分期实施计划（建议）

| 阶段 | 范围 | 交付物 | 预估工作量* | 你的验收方式 |
|------|------|--------|-------------|--------------|
| **Phase 4** | FR-01 全字段 + 标题规则；FR-02 记录页重构 + 答案隐藏；GLOBAL-UI v1（浅色规范落地） | 可用的导入/记录体验 + 视觉统一 | ≈ 4–6 工时 | 导入示例 → 记录页看到新字段 → 逐题隐藏/显示 |
| **Phase 5** | FR-05 设置页（主题深浅/跟随系统、模型 API、AI↔非 AI 模式）+ 深色主题全页适配 | 设置页 + 双主题 | ≈ 3–4 工时 | 切主题不闪白；填 Key 后测试连接成功；切非 AI 模式功能降级 |
| **Phase 6** | FR-03 知识库升级（三专题 + 两级分类 + 频次 + 相似识别 + 对比页） | 知识库 v2 + 对比页 | ≈ 5–7 工时 | 专题正确归类；相似题自动关联；对比页并列多次作答 |
| **Phase 7** | FR-04 模拟面试（整场/简历/AI 出题 + 语音包 + 报告） | 模拟面试模块 | ≈ 6–10 工时 | 整场模拟可完成；简历导入出题；语音可开关降级 |

\* 工时为"实现 + 自测 + 文档"的相对估算，不含你验收与需求变更时间。建议**每阶段结束停下等你确认**（沿用现有节奏）。

**排序理由**：Phase 4 先补齐数据字段与记录体验（改动面小、收益直接）；Phase 5 的 CSS 变量化会影响所有页面，**越早做越省返工**，故排在知识库/模拟之前；Phase 6 依赖 Phase 5 的组件规范；Phase 7 最大且含外部依赖（TTS/简历解析），放最后。

---

## 7. 风险与依赖

| 风险 | 影响 | 应对 |
|------|------|------|
| 本机/沙箱**无外网** | 无法 pip 安装 TTS（piper/edge-tts）、PDF 解析、embedding 依赖 | 优先零依赖方案：Web Speech API、`.txt/.md/.docx`（python-docx 本机已有）；其余列为可选增强 |
| 相似度阈值不易一次调准 | 误关联/漏关联 | 规则阈值可配置 + 灰区交 AI + 人工确认/取消；提供回归脚本 |
| 深色主题改造面广 | 漏改导致对比度不足 | 全部颜色 token 化 + 逐页走查清单；禁止模板内硬编码色值 |
| AI 模式调用成本/失败 | 抽取或判定中断 | 保留非 AI 兜底；LLM 失败友好提示与重试（现有能力）；灰区判定结果缓存 |
| 模拟面试状态管理复杂（中断/续答） | 体验割裂 | 会话与逐题状态落库，支持随时离开与续答 |
| 新增字段与既有 `date` 重叠 | 数据歧义 | `interview_at` 为准，`date` 保留为派生兼容字段；导入时自动同步 |
| 需求理解偏差（薪资/时长语义等） | 返工 | 见下方待审批决策项，确认后再动手 |

---

## 8. 待你审批的决策项

**需求语义（SRS 第 6 节）**
1. Q1 时长 = 整场面试时长（分钟）？（默认是）
2. Q2 期望薪资 = 自由文本？（默认是）
3. Q5 反问环节采纳"角色反转"建模 `direction=reverse`？（默认采纳）
4. Q6 语音包默认 Web Speech API，Piper/edge-tts 作可选增强？（默认是）
5. Q7 简历支持 `.txt/.md/.docx`，PDF 视依赖？（默认是）

**方案选择**
6. Q3 相似判定 = 规则层 +（AI 模式）灰区 LLM 判定，向量层暂缓？（默认是）
7. Q4 对比页默认列 = 来源/时间 + 忠实原文 + 优化建议 + 批注？（默认是）
8. Q8 设置存储 = `data/settings.json`？（默认是）
9. Q9 答案隐藏状态 = 浏览器本地记忆？（默认是）

**计划确认**
10. 是否同意按 **Phase 4 → 5 → 6 → 7** 顺序推进，且每阶段完成后停下等你确认？
11. Phase 4 是否即为下一步开工范围（FR-01 + FR-02 + 浅色 UI 规范）？

> 回复方式：可直接回"默认方案 + 同意计划"，或逐条给出不同意见。

**审批结果（已收到）**：用户回复"默认方案 + 同意计划"，并接受 §8 之外的 9 条优化口径
（详见 `docs/04-requirements-spec.md` §6 决策记录）。

---

## 8.1 执行进度更新

| 阶段 | 范围 | 状态 | 交付物 |
|------|------|------|--------|
| Phase 4 | FR-01 全字段与标题规则、FR-02 记录页与答案隐藏、浅色 UI 规范落地、P0 缺陷修复（连问丢题 / 0 问答静默 / 无标签切碎 / 校对能力 / 重复导入 / LLM 报错文案） | ✅ **已完成**（46/46 冒烟 + 8 项真实服务校验） | `docs/06-phase4-delivery.md` |
| Phase 5 | 设置页（主题深浅/跟随系统、模型 API、AI↔非 AI 模式）+ 深色主题全页适配 | ⏳ 待开工（等 Phase 4 验收） | — |
| Phase 6 | 知识库升级（三专题 + 两级分类 + 频次 + 相似识别 + 对比页） | ⏳ 未开始 | — |
| Phase 7 | 模拟面试（整场/简历/AI 出题 + 语音包 + 报告） | ⏳ 未开始 | — |

---

## 9. 附录：本轮不做（避免范围蔓延）

- 录音采集/自动转写（仍只消费转写文本）
- 多用户、登录、云同步
- 每题作答时长统计、评分体系（除非你要求，列入 P2）
- 移动端原生 App（仅做响应式网页）
- 面试官语音"克隆"/情感化 TTS（依赖大模型，暂不引入）
