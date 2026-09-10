# AI_Review 第七阶段交付说明 —— 模拟面试（整场 / 简历 / AI 出题 / 语音 / 报告）

> 版本：v1.0 ｜ 阶段：Phase 7（已完成）｜ 对应需求：`docs/04-requirements-spec.md` FR-04
> 验证：冒烟测试 **94/94 通过**（含本阶段 13 项断言）+ 界面截图/DOM/像素审计通过（见 `docs/10-verification-report.md`）

---

## 1. 交付内容

| 需求 | 交付 |
|------|------|
| FR-04.1 整场模拟 | 选择任意面试记录 → 按其题目顺序（可打乱）逐题重问，题量与原场一致 |
| FR-04.1 简历 + 题库 + AI | 导入简历解析技能/项目 → **AI 生成题 + 题库随机题**按比例混合（默认 40% AI） |
| FR-04.2 简历导入 | `.txt / .md / .docx`；**docx 零依赖解析**（zip + `word/document.xml`）；PDF 明确不支持并给出转存建议；解析出的技能/项目关键词**可人工修改** |
| FR-04.3 流程与报告 | 出题 → 逐题作答（计时/标记/参考要点折叠）→ 结束生成报告（统计、考点分布、需加强题目、逐题对照、下一步建议）；会话与逐题记录落库，可中断续答 |
| FR-04.4 面试官语音包 | TTS Provider 抽象：**浏览器语音（默认，零依赖）** / 静音 / edge-tts（在线）/ **Piper（开源本地）**；未安装方案自动回退并提示，纯文本始终可用 |
| 会话管理 | 历史模拟列表（继续/报告/删除），进度与用时统计 |

## 2. 关键实现

### 2.1 出题引擎（`app/pipeline/mock_gen.py`）
- `whole`：从面试记录取题（含忠实原文作参考作答、优化建议作参考要点）；
- `bank`：按 **被问频次加权随机**（越常考的题越容易被抽到）；
- `resume`：AI 生成 `round(count × ai_ratio)` 题（工具调用 `generate_questions`，输出问题 + 考察点 + 难度），其余用题库补齐；**AI 不可用时自动全部回退题库并返回说明**，不阻断流程；
- 可选追问：对带知识库出处的题追加 1 条深挖题（优先用历史变体问法）。

### 2.2 简历解析（`app/pipeline/resume.py`）
- `.txt/.md` 直接解码（UTF-8 → GBK 兜底）；`.docx` 用 `zipfile` 读取 `word/document.xml` 再抽 `<w:t>` 文本（**不依赖 python-docx**，离线可用）；
- 关键词抽取：技能词表命中 + 项目名正则（"…系统/平台/服务/项目"）+ 经验年限正则；
- 解析结果可在界面上二次编辑后保存（Q7 优化项）。

### 2.3 语音方案
- 前端 `speechSynthesis`（`zh-CN`，用户手势触发，规避自动播放限制）；
- 服务端探测：`/api/tts/status` 返回 `{web, none, edge, piper}`；edge 用 `importlib.util.find_spec("edge_tts")`，piper 用 `shutil.which("piper")`；
- 选择未安装/无网络的方案时给出明确提示并回退浏览器语音，不影响作答。

### 2.4 作答与报告
- 逐题页：题号/来源/追问标记、计时器、作答框、标记（掌握/不确定/不会）、参考要点默认折叠、Ctrl/⌘+Enter 保存并下一题、题号导航条（已答高亮）；
- 报告页：题目数/已答/总时长/需加强数 → 考点分布 → 需加强题目（可跳知识库对比）→ 逐题对照（我的回答 vs 参考要点）→ 下一步建议。

## 3. 变更文件

| 文件 | 变更 |
|------|------|
| `app/pipeline/mock_gen.py` | **新增**：三种出题来源、AI 生成、追问、降级逻辑 |
| `app/pipeline/resume.py` | **新增**：简历文本抽取（含零依赖 docx）、关键词解析、编码兜底 |
| `app/db.py` | 新增 `resumes / mock_sessions / mock_turns` 三张表与索引 |
| `app/repository.py` | 简历与会话/逐题 CRUD（含进度统计、级联删除） |
| `app/main.py` | `/mock`、`/mock/<sid>`、`/mock/<sid>/report`；`/api/resumes*`、`/api/mock/sessions*`、`/api/mock/turns/<tid>`、`/api/tts/status`；TTS 能力探测 |
| `app/templates/mock_setup.html` | **新增**：模式卡片、整场/简历选择、简历上传与解析编辑、参数与语音、历史模拟 |
| `app/templates/mock_run.html` | **新增**：逐题作答、计时、标记、语音播报、参考要点、导航条 |
| `app/templates/mock_report.html` | **新增**：统计、考点分布、需加强题目、逐题对照、下一步建议 |
| `app/templates/base.html` | 导航加入「模拟面试」 |
| `scripts/smoke_e2e.py` | 补 13 项断言（简历 txt/docx/PDF、三种出题模式、作答与进度、报告、TTS 探测、删除会话） |

## 4. 验证结果（本阶段新增断言）

- 简历：txt 解析出技能与项目；**docx 零依赖解析**（自造 docx 内容 → 正确抽出 Python/Docker）；PDF 返回 400 + 明确提示；列表接口正常。
- 模拟：题库模式创建 3 题并落库 → 作答保存后进度更新 → 结束 → 报告页渲染（统计/逐题对照/建议）；整场模式题量与原场当前题目一致；简历模式在无 Key 时 `ai_used=0` 且返回降级说明；TTS 探测返回 `web=True`；删除会话后查不到。

## 5. 已知边界

- 不采集录音（按 SRS 范围，仅消费转写文本）；
- AI 生成题需配置 Key，否则仅题库随机题（界面已提示）；
- edge-tts / Piper 需自行安装（本机均未安装，已走浏览器语音）；
- 模拟作答为文本记录（口头作答后的要点），不做语音识别评分。
