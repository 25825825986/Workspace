# AI_Review 技术栈设计文档

> 版本：v0.1 ｜ 阶段：第二阶段（技术栈设计）｜ 状态：**待用户确认**
> 依据：`docs/01-product-prototype-design.md` v0.2（已确认原型）
> 约束：优先简单、可本地运行、个人单机使用；不实现录音导入；自评=批注。

---

## 0. 目标与硬约束

- **单机本地运行**：一切数据落在本机目录，不依赖云服务（LLM 调用除外，且可配置）。
- **简单优先**：进程数 ≤ 2、无 Docker/K8s/消息队列/微服务；启动方式尽量一条命令。
- **个人单机数据量级**：面试数百场、问答数千条以内——任何"企业级"设施（集群、向量库、对象存储）都是过度设计。
- **MVP 主闭环必须通**：导入文本 →（有/无标签）角色确定 → AI 提取问答 → 校对 → 入库合并 → 查看/导出。

## 1. 需求 → 技术要点映射

| 原型功能 | 关键技术点 |
|----------|-----------|
| F01 导入 | 文本粘贴/文件上传；格式嗅探（标签正则）；元信息表单 |
| F02 角色识别（有/无标签） | 规则层（标签直读）+ LLM 推断（无标签时）；置信度标记 |
| F03/F04 切分提取（忠实+可溯） | LLM 结构化输出 + 程序按字符区间回填 source_ref |
| F05–F08 校对/复盘/入库 | 关系数据 CRUD + 去重合并逻辑（norm 键） |
| F09 本地持久化 | SQLite 单文件（含原文） |
| F10 导出 | Markdown 模板生成 |
| R1/R2/R3 红线 | Prompt 规则 + 字段隔离 + zod/pydantic 校验 + 区间引用 |

---

## 2. 处理管线设计（LLM 结构化输出的落地方式）

统一为**三步管线**，全部跑在本地服务进程内（同步执行，个人任务无并发压力）：

```
原始文本
  │ ① 预处理(纯程序, 零 LLM)
  │     - 标签嗅探: 正则识别 [面试官]/面试官：/A、B/Speaker 0、1 等
  │     - 有标签 → 按标签聚合成轮次块（speaker, text, char 区间）
  │     - 无标签 → 按段落/启发式(问句?)切分候选轮次，供步骤②标注
  ▼
 ② LLM 结构化抽取(每块 ≤ 6k 字符, 可多块并行/串行)
  │     - 块 A: 角色推断（仅无标签文本）→ [{segment, speaker_role, confidence}]
  │     - 块 B: 问答提取（每块调用一次）→ 输出 QA JSON(见下)
  │     - 统一走 function calling(tool schema) 首选；JSON mode 后备
  │     - 每次输出经 zod 校验，失败自动重试 1 次(修错提示)
  ▼
 ③ 后处理(纯程序)
  │     - 用步骤①记录的 char 区间，把 LLM 返回的文本/段落映射为
  │       Question/Answer 的 source_ref（start/end/quote）→ 可溯源、可高亮
  │     - 相似问题合并、空回答标记 pending、低置信标记 pending
  │     - 入库: QAItem + 知识点 norm 去重合并 + KnowledgeEntry 频次/出处更新
```

**红线的 Prompt 层落实（每次抽取都注入）**：
- R1：有标签文本——"严格按说话人标签归属，禁止推测/修改标签"；无标签文本——"依据句式与语义推断 speaker_role，无法判断输出 unknown"。
- R2："问题与回答必须逐字引用原文（含口误、卡壳、语气词），禁止改写、润色、补全、纠错；若原文残缺可输出占位并标 low_confidence"。
- R3：输出携带 category_suggestion（知识点名），入库时由程序用 norm 去重，不由 LLM 决定是否合并。

**function calling 工具示例（问答提取，示意）**：
```jsonc
{
  "name": "extract_qa",
  "description": "从面试对话块中抽取问答对，逐字引用原文，禁止改写润色",
  "parameters": {
    "type": "object",
    "properties": {
      "items": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "seq":        { "type": "integer", "description": "块内顺序" },
            "question":   { "type": "string",  "description": "逐字引用的问题原文" },
            "answers":    { "type": "array", "items": { "type": "string" },
                            "description": "逐字引用的回答原文，可多段" },
            "category":   { "type": "string",  "description": "建议知识点，如 本地缓存" },
            "confidence": { "type": "number" },
            "note":       { "type": "string",  "description": "为何低置信/异常说明,可空" }
          },
          "required": ["seq", "question", "answers", "confidence"]
        }
      }
    },
    "required": ["items"]
  }
}
```

> 为什么选 function calling 而非裸 JSON prompt：tool schema 是模型侧约束最强的通用机制，DeepSeek/OpenAI/Qwen/Ollama 均兼容；JSON mode 作为后备（部分本地模型不支持 tools 时），再由 zod/pydantic 兜底强校验，形成"schema 约束 + 代码校验 + 重试"三层保障。

---

## 3. 方案一：「推荐栈」——本地单进程 Web 应用（Node + Vue，全 TypeScript）

| 层 | 选型 | 为什么 | 否决了什么 |
|----|------|--------|-----------|
| 前端 | **Vue 3 + TypeScript + Vite + Naive UI** | 校对列表/双栏复盘/原文高亮/合并拆分是高频富交互，必须 SPA；Vue 生态在国内资料多、SFC 上手快；Naive UI 轻量组件齐（弹层/表格/标签）；TS 与后端共享 schema 类型 | 不用 CLI/TUI（无法支撑校对交互）；不用 React（能力等价，选 Vue 出于生态与代码量偏好） |
| 后端/处理管线 | **Node.js 20 LTS + TypeScript + Fastify**；REST API `/api/*`；生产构建后由后端静态托管 `web/dist` | 前后端**同语言同类型**：一份 zod schema 前后端共用，红线字段（extract_text 只读、optimization/annotation 独立）在类型层可防呆；单进程单端口，启动即用 | 不用 Python 后端（引入双语言运行时）；不用 NestJS（重）；不用 Next/Nuxt SSR（本地工具无 SEO/SSR 需求，反而增加心智负担） |
| LLM 接入 | **OpenAI 兼容协议**（base_url + api_key + model 可配置）；默认 DeepSeek `deepseek-chat`（中文与指令遵循好、便宜），可切 Ollama/其他本地模型；SDK 用 `openai`(npm) 或原生 fetch | 一套代码兼容云端/本地多模型；DeepSeek 支持 function calling 且费用可忽略（个人量级） | 不绑定任何厂商专用 SDK/平台；不上 LangChain（本管线只有 3 步，自写更可控） |
| 存储 | **SQLite（better-sqlite3）**，单文件 `data/app.db`；原文另存 `data/raw/<id>.txt`；表结构即 §5 实体映射 | 零运维、事务安全、备份=复制文件；个人量级万行内绰绰有余；字段直接用 JSON 列存 Speaker/question/answers 等嵌套结构 | 不用 Postgres/MySQL（过重）；**不用向量库**：面经检索以结构化过滤（类别/标签/频次）+ 文本 LIKE 为主，条目量小，语义检索收益极低；中文 FTS5 分词差，先不上（P2 如需再评估 sqlite-vec/本地 embedding） |
| 导出 | Markdown 由**模板函数生成**（忠实原文/优化建议/批注分节）；附加"HTML 预览 → 浏览器打印另存 PDF" | Markdown 零依赖、可读可版本管理；浏览器打印即得 PDF，无需额外组件 | 不在 P0 引入 Pandoc/WeasyPrint/md-to-pdf（PDF 质量要求高时 P1 再评估 Pandoc） |
| 运行 | dev：`pnpm dev`（Vite + API 同起）；生产：`pnpm build && pnpm start` → `http://127.0.0.1:3846`（自动开浏览器） | 一条命令可跑 | 不引入 Docker/PM2/nginx |

**推荐栈工程布局（示意）**
```
AI_Review/
├─ web/        # Vue3 + TS SPA（导入向导/列表/复盘/面经库/统计）
├─ server/     # Fastify + better-sqlite3 + 三步管线（预处理/LLM/后处理）
├─ shared/     # zod schema + 类型 + norm 工具（web 与 server 共用）
├─ data/       # app.db / raw/*.txt / export/*.md（运行时生成，gitignore）
└─ package.json# pnpm workspace + 一键脚本
```

---

## 4. 方案二：「最简 MVP 栈」——先跑通最小闭环（Python 单语言）

| 层 | 选型 | 为什么 |
|----|------|--------|
| 前端 | **需要，但极简**：FastAPI + Jinja2 服务端渲染 + 原生 JS + 轻量 CSS（不放打包链） | 校对列表/单题双栏仍需要界面（CLI 不可接受），但 P0 页面形态简单，原生 JS + 少量 fetch 足够；**零 Node 依赖、改模板即生效** |
| 后端/管线 | **Python 3.11 + FastAPI + Uvicorn**；管线写成普通 Python 模块（preprocess → llm → postprocess） | LLM 结构化输出在 Python 侧最顺手（openai SDK + Pydantic 强类型）；全部逻辑一门语言，代码量最小 |
| LLM | `openai` SDK + **Pydantic 模型**约束输出（tool calling / json mode）；模型默认 DeepSeek，可换 Ollama | 与方案一同理，Python 端 pydantic 即 zod |
| 存储 | **SQLite**（stdlib `sqlite3` 轻封装，不引 ORM） | 零第三方依赖；表结构同 §5 实体 |
| 导出 | Python 模板字符串 → `.md`；浏览器打印 HTML → PDF | 零依赖 |
| 运行 | `uvicorn app.main:app` → `http://127.0.0.1:8000` | 一条命令 |

**最简栈工程布局（示意）**
```
AI_Review/
├─ app/
│  ├─ main.py        # FastAPI 路由 + 页面渲染
│  ├─ pipeline/      # preprocess.py / llm_extract.py / postprocess.py
│  ├─ db.py          # sqlite3 初始化 + 存取
│  └─ schemas.py     # pydantic 模型（实体 + LLM 输出）
├─ templates/        # Jinja2（导入/列表/单题/面经库）
├─ static/           # 原生 JS + CSS
└─ data/             # app.db / raw / export
```

---

## 5. 两方案对比与演进建议

| 维度 | 方案一 推荐栈 | 方案二 最简 MVP 栈 |
|------|--------------|--------------------|
| 跑通 MVP 耗时 | 较长（含前端工程化） | **最短**（无构建链） |
| 语言/运行时 | Node（TS 全栈） | Python 单语言 |
| UI 交互上限 | 高（富交互、可长期演进） | 够用（后续重交互需换前端） |
| 类型安全/红线防呆 | 强（zod 前后端共享） | 中（pydantic 后端强，前端弱） |
| 依赖安装 | node + pnpm（需网络装包） | python + pip（包少） |
| 长期维护 | 推荐 | 演进到方案一时前端需重写（API 可保留） |

**我的建议（二选一路线）**：
- **路线 A（直接方案一）**：若你确认长期使用、愿意接受一次前端工程化成本，直接按推荐栈开发，后续迭代不返工。
- **路线 B（先方案二再演进）**：若想最快看到"导入→提取→列表→导出"真实效果以验证产品，先按最简栈交付闭环（1–2 天量级），跑顺后视 UI 需求决定是否迁移方案一（迁移时后端 API 与库表可直接复用）。

两条路线共用同一套**数据模型与 LLM 抽取契约**（§5 / §2 工具 schema），迁移成本被压到最低。

---

## 6. 默认配置（如无异议即生效，均可改）

- LLM 默认：OpenAI 兼容接口 + `deepseek-chat`；`DEEPSEEK_API_KEY` 存本机 `.env`（不进 git）；模型/base_url 可换 Ollama。
- 端口：127.0.0.1 固定本机监听（不暴露局域网）；自动打开浏览器。
- 无 LLM Key 的降级：允许手工录入问答 + 纯规则切分预览（P0 之后提供，不影响主流程设计）。

---

## 7. 待你确认

1. 走 **路线 A（方案一：Node+Vue 推荐栈）**、**路线 B（方案二：Python 最简栈）**，还是"先 B 验证、再视情况迁 A"？
2. LLM 默认 DeepSeek 是否可接受？你手头是否有 API Key（影响联调方式，无 Key 时我可用 mock/本地 Ollama 打通管线）？
3. 数据文件目录按 `AI_Review/data/` 规划（备份=拷目录）是否 OK？

---

*第二阶段交付完毕。确认技术方案后，进入第三阶段（数据层 + 管线骨架实现 / 或按所选栈搭建工程）。*
