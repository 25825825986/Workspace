# AI_Review 第五阶段交付说明 —— 设置页 / 主题 / AI 模式切换

> 版本：v1.0 ｜ 阶段：Phase 5（已完成，待验收）｜ 对应需求：`docs/04-requirements-spec.md` FR-05 + 决策记录 Q8
> 验证：`scripts/smoke_e2e.py` **60/60 通过** + 真实服务端到端检查 **8/9 通过**（1 项为测试脚本自身断言写错，已单独验证语义）+ `node --check app/static/app.js` 通过

---

## 1. 交付范围

| 需求 | 交付内容 |
|------|----------|
| FR-05.1 主题 | 浅色 / 深色 / **跟随系统** 三档；设置页单选即时预览 + 保存；顶栏「切换主题」按钮可快速浅↔深切换 |
| FR-05.2 模型 API | Base URL / 模型名 / API Key 配置；Key 掩码显示（`sk-a****6789`）、可清除；**「测试连接」**最小请求验证并给出耗时/失败原因 |
| FR-05.3 AI / 非 AI 模式 | 三档：**自动**（有 Key 用 AI，无 Key 回退规则）/ **强制 AI** / **非 AI（离线）**；顶栏模式徽标；导入页在非 AI 模式下给出引导提示 |
| FR-05.4 数据信息 | 设置页展示数据目录、数据库大小、场次/题目/知识库条目/导出文件数、备份说明 |
| Q8 设置持久化 | `data/settings.json`（普通设置，**原子写**）+ `data/secrets.json`（仅 API Key，独立文件）；**优先级：设置页 > `.env` > 内置默认** |

## 2. 关键实现

### 2.1 配置改为"可热生效"（Q8 优化的核心）
Phase 4 之前 `app/config.py` 在 import 时把 `DEEPSEEK_API_KEY / LLM_MODEL / EXTRACTOR` 固化成模块常量，设置页改了也不会生效。Phase 5 重构为**函数式读取**：

```python
config.llm_api_key()   # settings_store.api_key() → .env → ""
config.llm_base_url()  # settings['llm']['base_url'] → .env → https://api.deepseek.com
config.llm_model()     # settings['llm']['model']    → .env → deepseek-chat
config.ai_mode()       # auto | on | off（兼容旧 .env 的 EXTRACTOR=rule/mock/deepseek）
config.extractor_mode()# rule | mock | deepseek（实际使用的提取器）
config.mode_badge()    # 顶栏徽标文案与样式类别
```
所有调用点（`service.run_import`、`pipeline/extract`、`pipeline/llm`、路由上下文）都改为实时解析 → **保存后下一次请求即生效，无需重启**（冒烟测试用"切换模式后立刻调用 `/api/analyze` 观察 extractor 变化"验证）。

### 2.2 设置持久化（`app/settings_store.py`，新增）
- 原子写：`tempfile.mkstemp` → `json.dump` → `fsync` → `os.replace`，异常时清理临时文件；写入中断不会损坏配置。
- 只持久化已知键（`theme / ai_mode / llm.base_url / llm.model`），避免脏字段堆积。
- 读取时与默认值深合并并做取值校验（非法 `theme`/`ai_mode` 自动回落默认）。
- API Key 单独放 `secrets.json`；`masked_key()` 只暴露首 4 位 + 末 4 位；清除 = 写入 `{}`（已验证：设置 → 文件含 `api_key`；清除 → `{}`；`has_api_key=False`）。

### 2.3 主题无闪烁（Q9 与 FR-05.1 的配合）
- 设置为 `light/dark` 时**由服务端直接渲染** `<html data-theme="dark">`，首帧即正确。
- 设置为 `system` 时不写属性，由 `<head>` 内联脚本按 `prefers-color-scheme` 决定，并监听系统变化实时切换。
- 深色主题只覆盖 CSS 变量（新增 `:root[data-theme="dark"]` 块），同时把此前散落的硬编码色（`#eef1f6 / #c7d8ff / #0f172a` 等）收敛为 `--code-bg / --chip-bg / --hover-bg / --nav-hover / --*-line / --preview-*` 等变量。

### 2.4 AI 模式的行为差异
- 自动 + 有 Key → `deepseek`（function calling 抽取）；自动 + 无 Key → `rule`；
- 强制 AI + 无 Key → 提取器仍解析为 `deepseek`，调用时由 `llm._client()` 抛出「未配置 API Key（可在「设置 → 模型 API」中填写，或写入 .env）」，路由映射为 **400 友好提示**（非 500）；
- 非 AI → `rule`，纯离线；
- 顶栏徽标实时展示 `AI 模式 · deepseek-chat` / `Mock 模式（本地规则模拟）` / `非 AI 模式（本地规则）`。

## 3. 变更文件

| 文件 | 变更 |
|------|------|
| `app/settings_store.py` | **新增**：设置读写、原子写、Key 掩码、默认值合并与校验 |
| `app/config.py` | 重写为函数式配置（热生效）+ `mode_badge()` + `APP_VERSION`；保留 `.env` 兜底与旧 `EXTRACTOR` 兼容 |
| `app/pipeline/llm.py` | `_client()`/`chat_tool()` 改为实时读配置；新增 `test_connection()`（异常转可读信息） |
| `app/pipeline/extract.py` | 改用 `config.*`；`describe()/has_llm()` 跟随设置 |
| `app/service.py` | 改用 `config.extractor_mode()/extractor_label()` |
| `app/main.py` | 新增 `/settings` 页与 `/api/settings`（GET/POST）、`/api/settings/api-key`、`/api/settings/test`、`/api/settings/reset`；上下文注入 `theme / mode_badge / ai_mode / has_api_key / app_version` |
| `app/templates/settings.html` | **新增**：主题 / AI 模式 / 模型 API / 数据信息 / 关于（含测试连接、清除 Key、恢复默认） |
| `app/templates/base.html` | 服务端主题属性 + 系统偏好解析、导航新增「设置」、顶栏模式徽标与主题切换按钮、页脚版本号 |
| `app/templates/import.html` | 非 AI 模式下给出"去设置启用 AI"的引导 |
| `app/static/style.css` | 深色变量块 + 派生色 Token 收敛 + `.mode-badge / .choice-row / .choice` 等新组件 + 移动端适配 |
| `app/static/app.js` | 顶栏主题快速切换（本地即时生效 + 写入设置） |
| `scripts/smoke_e2e.py` | 46 → **60** 项断言（设置页渲染、默认值、模式热生效、主题直出、非法值拒绝、Key 掩码与清除、连通性失败可读、原子落盘、密钥不经 HTTP 暴露、恢复默认） |

## 4. 验证结果

**离线冒烟（60/60）** 新增覆盖：
`GET /settings` 渲染 → 默认 `theme=system / ai_mode=auto / 无 Key → rule` → 切 `on` 后 `/api/analyze` 立即显示 `deepseek`（热生效）→ 切 `off` 回到 `rule` → 设 `dark` 后 `/` 与 `/settings` 均直出 `data-theme="dark"` → 样式表含深色变量块 → 非法主题 400 → Key 保存后 `masked_key=sk-t****abcd` 且响应不含明文 → 连通性测试 400 + 可读 message（不 500）→ 清除 Key 回退 → `data/settings.json` 结构完整且 `secrets.json` 存在 → `GET /data/secrets.json` = 404 → 恢复默认生效。

**真实服务（8/9）**：设置页渲染、默认读取、深色直出、强制 AI 热生效、Key 掩码、连通性失败提示、切回非 AI + 清 Key、切浅色即时生效全部通过。
> 唯一失败项是**测试脚本自身断言写错**（我假设清除 Key 后 `secrets.json` 仍保留 `api_key` 空值，实际按设计写为 `{}`）；随后单独验证语义正确：设置 → `{"api_key": "..."}`、清除 → `{}`、`has_api_key=False`、掩码 `sk-a****6789`。

**前端**：`node --check app/static/app.js` 通过。

## 5. 已知边界（本阶段未做）

- 「测试连接」在**无外网环境**只能验证到"失败且信息可读"；真实 Key 的连通性需在有网机器上点一次；
- 数据管理中的**一键备份/恢复**（打包 `data/`）仍为 P1，本阶段只展示数据信息与备份说明；
- 相似度阈值、默认标签格式等解析偏好设置未纳入（留待 Phase 6 与相似功能一起设计）；
- 主题仅覆盖 `style.css` 变量，`md-preview` 等少量深色固定区域已单独适配，但若有新增页面需沿用 Token。

## 6. 复测方法

```bash
python scripts/smoke_e2e.py    # 60/60
python run.py                  # 手工：设置页切主题/切模式/保存 Key/测试连接 → 顶栏徽标与主题即时变化
```
