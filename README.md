# AI_Review — AI 面试复盘与知识提取平台

个人本地工具：导入面试转写文本 → 自动切分「面试官问题 / 我的回答」（忠实原文、可溯源）→
单题复盘（优化建议独立一栏）→ 按知识点跨场去重合并进知识库（专题归类 + 相似关联 + 对比）→
**模拟面试演练** → 导出 Markdown。

> 全部阶段已完成（方案二：Python + Flask + Jinja2 + 原生 JS + SQLite）：
> Phase 4 导入字段/记录页/答案隐藏/UI 规范（`docs/06`）· Phase 5 设置页/主题/AI 模式（`docs/07`）·
> Phase 6 知识库升级（`docs/08`）· Phase 7 模拟面试（`docs/09`）· 验证报告（`docs/10`）·
> 设计评审（`docs/11`）· **Phase 8 体验优化：自动保存 / Toast / 无障碍 / 分页 / 撤销 / ⌘K（`docs/12`）**。
> 产品与设计文档见 `docs/`。

## 快速开始

```bash
# 1) 安装依赖（仅首次；Python 3.10+）
python -m pip install -r requirements.txt

# 2) 启动（默认 http://127.0.0.1:8000，仅监听本机）
python run.py
```

浏览器打开 http://127.0.0.1:8000 → 「导入面试」→ 点「载入示例文本」即可在**无任何 API Key**
的情况下跑通完整闭环（规则/mock 提取）。
可选：`python scripts/dev_seed.py` 灌入演示数据（推荐，能立刻看到知识库/模拟面试的真实效果）。

## 主要功能

| 页面                         | 能力                                                                                                                                                             |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 面试记录`/`                | 卡片式列表、关键词搜索、快筛（待确认/未入库）、排序、统计条、**分页「加载更多」**、⌘K 全局搜索                                                                  |
| 导入面试`/import`          | 公司/岗位/地点/时间/期望薪资/时长/类型（均可空）+ 标题自动生成与手动覆盖 + **粘贴后自动识别**说话人角色                                                          |
| 本场复盘`/interviews/<id>` | 逐题「隐藏/显示答案」+ 复习模式（首屏无闪现）、确认/加入知识库、**行内快速编辑**、合并 / 拆分 / 删除（可撤销）、抽屉预览导出                                       |
| 单题复盘                     | 忠实原文（只读 + 原文区间）+ 优化建议（独立栏，**自动保存**）+ 批注 + 标记修正                                                                                   |
| 知识库`/knowledge`         | 三专题（自我介绍/个人发展/反问环节）+ 一级分类（项目/技术栈/系统设计/团队协作/行为/其他）+ 频次 + **相似问题识别与关联** + 对比页（多次作答并列、标为最佳）       |
| 模拟面试`/mock`            | 三种模式（整场复盘 / 题库随机 / 简历+题库+AI）、计时作答、标记、**作答自动保存**、语音播报（浏览器 / edge-tts / Piper）、自动生成模拟报告                        |
| 设置`/settings`            | 主题（浅/深/跟随系统）、大模型 API 与连通性测试、AI↔非 AI 模式切换、数据信息                                                                                     |

## 交互特性（Phase 8）

- **统一反馈**：轻提示 Toast（含「撤销」动作）、自研确认弹层（Esc / 焦点陷阱）、按钮内联 loading、顶部进度条、骨架屏；
- **不丢数据**：作答与复盘批注 800ms 防抖自动保存 + 离开页面脏数据拦截；
- **可撤销**：删除问答、取消相似关联均可在提示条里一键恢复；
- **无障碍**：`aria-live` / `role=progressbar` / `fieldset+legend` / `aria-expanded` / 跳转主内容 / 焦点可见 / `prefers-reduced-motion` 支持；
- **移动端**：≤640px 使用底部 Tab 导航；**⌘K**（Ctrl+K）全局搜索面试记录与知识点。

## 自检脚本

| 脚本 | 作用 |
|------|------|
| `python scripts/smoke_e2e.py` | 后端全链路断言（109 项） |
| `python scripts/ui_kit_check.py` | UI Kit 真实浏览器行为检查（Toast/弹层/抽屉/命令面板） |
| `python scripts/screenshot_ui.py [--dark\|--mobile]` | 生成 30 张界面截图并做 DOM 校验 |
| `python scripts/ui_audit.py` | 像素审计（空白/溢出/截断/留白/对比度/主题） |
| `python scripts/design_audit.py` | 设计量化证据（组件计数、token、无障碍覆盖） |
| `python scripts/dev_seed.py` | 灌入演示数据（会清空 `data/`） |

## 启用 AI 提取（可选）

复制 `.env.example` 为 `.env` 并填入 Key：

```ini
DEEPSEEK_API_KEY=sk-xxx
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
EXTRACTOR=auto        # auto | rule | mock | deepseek
```

`EXTRACTOR=auto`：有 Key → DeepSeek（function calling 结构化抽取）；无 Key → 规则切分。
对**无说话人标签**的文本，AI 会按内容推断角色（低置信段可人工修正）；规则/mock 模式仅做低置信启发式推断。

## 三条设计红线如何落地（详见 docs/01 §0/§5）

- **R1 角色归属**：有标签文本以标签直读 + 导入向导人工确认；无标签才由 AI/启发推断（标注 source=inferred 与置信度），全部可改。
- **R2 提取 ≠ 改写**：问答文本只读展示并带原文字符区间（source_ref，可对照）；「修正」只产生 corrected 副本；优化建议/批注为独立字段，导出分节。
- **R3 统一 schema + 去重频次**：SQLite 单一 schema；知识点按 `norm` 去重，`entries.ask_times` 计频次，`source_qa_ids` 保留跨场出处。

## 目录结构

```
run.py               # 启动：python run.py → http://127.0.0.1:8000
app/
  main.py            # Flask 路由（页面 + API）
  service.py         # 导入编排（角色确定 → 提取 → 组装入库）
  repository.py      # SQLite 读写（interview/qa/category/entry）
  exporter.py        # Markdown 导出（本场报告 / 面经库快照）
  pipeline/
    preprocess.py    # 标签嗅探、轮次切分（带字符区间）
    extract.py       # 提取器：rule / mock / DeepSeek(function calling)
    llm.py           # OpenAI 兼容调用 + 工具 schema（未配置 Key 时不加载）
    postprocess.py   # source_ref 回填、组装、入库合并
  templates/ static/ # Jinja2 页面 + 原生 JS/CSS
scripts/smoke_e2e.py # 离线端到端冒烟测试
data/                # 运行时生成：app.db / raw/*.txt（备份=复制本目录）
docs/                # 产品原型(v0.2)、技术栈设计、阶段交付说明
```

## 数据

一切个人数据在本项目 `data/` 目录（SQLite 单文件 + 原文存档）。**备份 = 复制该目录**。
`data/` 与 `.env` 已被 `.gitignore` 排除。

## 已知边界（Phase 3）

- 统计页/设置页、标签管理、预设题型模板（P1）未实现；
- 单题"优化建议"为工作区字段，纳入面经库时聚合到条目（不进入 extract_text）；
- 无标签文本的规则/mock 推断仅为低置信占位，正式使用建议配置 DeepSeek Key。
