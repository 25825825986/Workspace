# AI_Review 第八阶段交付说明 —— 设计评审优化清单落地（P0 / P1 / P2）

> 版本：v1.0 ｜ 阶段：Phase 8（设计体验优化，已完成，待验收）
> 依据：`docs/11-design-review.md` 优化清单
> 验证：后端断言 **109/109**、界面 30 张截图 + 8 项 DOM 校验 + 像素审计 **AUDIT_OK**、UI Kit 真实浏览器行为 **18/18**

---

## 1. 优化清单完成情况

### P0（阻塞/重要，已全部完成）

| # | 清单项 | 实现 | 位置 |
|---|--------|------|------|
| 1 | **自动保存 + 离开保护** | 模拟作答与单题复盘的作答/批注/建议改为 **800ms 防抖自动保存**；`beforeunload` 脏数据拦截；页面显示"已保存 12:03:45"微字；标记（掌握/不确定/不会）变更即时保存 | `mock_run.html`、`review_detail.html`（`data-autosave`）、`app.js` `markDirty/markSaved` |
| 2 | **统一反馈组件** | 自研 `ui.toast()`（含"撤销"动作、`role=status/alert`）与 `ui.confirm()`（`role=dialog` + `aria-modal` + 焦点陷阱 + Esc + 输入框），**替换全部 23 处原生 confirm/alert/prompt** | `app.js` UI Kit + 13 个模板；冒烟断言"模板已无原生 confirm/alert/prompt" |
| 3 | **加载形态** | 按钮内联 spinner（`ui.loading`）、顶部不定进度条（`role=progressbar`）、导入页骨架屏（`ui.skeleton`）；用于识别/解析/入库/出题/测试连接/相似重算 | `app.js`、`style.css`、`import.html` 等 |

### P1（结构化提升，已全部完成）

| # | 清单项 | 实现 |
|---|--------|------|
| 4 | **无障碍补齐** | `aria-live`/`role=status`（提示条与 Toast）、`role=progressbar`（进度条）、`fieldset/legend`（主题与 AI 模式单选）、`aria-expanded`/`aria-haspopup`（"更多"浮动菜单、"快速编辑"）、跳转到主内容链接（`.skip-link`）、`:focus-visible` 覆盖链接/标签/导航/Tab、弹层焦点陷阱与焦点回归、`<caption class="sr-only">`（表格）、表单 `label`/`sr-only` 补齐 |
| 5 | **入口收敛** | 删除 2 个独立导出预览页 → 统一为**「预览抽屉」**（`ui.drawer`，含"下载 .md"入口，并提示已落盘 `data/export/`）；`/knowledge` 成为知识库唯一路径，`/bank`、`/bank/export`、`/interviews/<id>/export` 全部 **301**；导出入口每页只保留 1 组 |
| 6 | **分页 / 渐进加载** | 记录页每页 20 条 + **「加载更多」**（`/partials/records` 片段接口，前端插入，避免重复渲染逻辑）+ "已显示 N / 共 M"；知识库默认展示 60 条 + 「加载更多」（`limit` 参数）；对比页作答表默认 20 行 + 「显示全部」 |
| 7 | **单题工作合并** | 记录页每题新增 **「快速编辑」**：行内展开优化建议/批注并保存（`/api/qa/<id>/update`），无需跳到详情页；详情页补充"查看知识库对比"入口 |
| 8 | **撤销机制** | 删除问答 → Toast 内「撤销」（`/api/qa/<id>/restore`，软删除可恢复）；取消相似关联 → Toast 内「撤销」（`/api/similar/restore`，带 `undo_payload`） |

### P2（打磨，已完成）

| # | 清单项 | 实现 |
|---|--------|------|
| 9 | **克制动效** | 卡片入场 `ui-rise 180ms`、浮动菜单 `ui-pop 100ms`、进度条 `ui-indeterminate`、骨架 shimmer；全部在 `prefers-reduced-motion: reduce` 下自动关闭 |
| 10 | **移动端导航** | ≤640px 隐藏顶部横向滚动导航，改用**底部 Tab**（5 项，含安全区内边距），并给 `body` 预留空间 |
| 11 | **图标与文案** | 新增 SVG sprite（eye/eye-off/download/plus/play/trash/search/check/link）用于预览、下载、导入、播放、删除等关键动作；文案统一："重算相似关联→更新相似关联"、"重跑分类→重新归类"、"整场入库→加入知识库"、"无回答→未提取到回答"、"入库→加入知识库" |
| 12 | **⌘K 全局搜索** | 命令面板（`ui.palette`）：输入即搜 `/api/search`（面试记录 + 知识点 + 页面跳转），↑/↓ 选择、Enter 打开、Esc 关闭；顶栏与记录页均有入口 |
| 13 | **token 收敛 + 审计断言** | 新增 `--on-brand`（浅色 `#fff` / 深色 `#08111f`，修正深色主按钮对比度）、`--overlay`、`--elevate`；token 块之外**零硬编码颜色** |

## 2. 关键实现说明

- **UI Kit 集中在 `app/static/app.js`**（单文件、零依赖）：`ui.toast / confirm / loading / progress / drawer / palette / markDirty / markSaved / icon / skeleton`，并提供**声明式**能力：
  `[data-post]`（含 `data-confirm` 走自研弹层）、`[data-export-preview]`（抽屉预览）、`[data-load-more]`（分页片段）、`[data-autosave]` + `data-autosave-payload`（防抖自动保存）、`[data-popover-trigger]/[data-popover]`（浮动菜单，点击外部/Esc 关闭）。
- **兼容性**：`/bank*` 与 `/interviews/<id>/export` 保留 301，旧书签不 404；下载接口路径不变。
- **可回归**：新增 `app/static/ui-kit-selftest.html` + `scripts/ui_kit_check.py`，把 UI 行为变成可执行断言。

## 3. 验证结果

| 验证项 | 命令 | 结果 |
|--------|------|------|
| 后端全链路 | `python scripts/smoke_e2e.py` | **109/109 通过**（新增 15 项：撤销删除、撤销关联、分页片段、全局搜索、旧路由 301、UI Kit/无障碍/底部 Tab/无原生弹窗断言） |
| 界面渲染 | `python scripts/screenshot_ui.py [--dark/--mobile]` | 30 张截图；**DOM 校验 8/8 通过** |
| 像素审计 | `python scripts/ui_audit.py` | **AUDIT_OK**（无空白/溢出/截断；桌面留白 186/189px；对比度 浅色 6.2:1、深色 10.2–10.9:1） |
| UI 行为（真实浏览器） | `python scripts/ui_kit_check.py` | **18/18 通过**（Toast 含动作、弹层 role/aria/Esc/返回值、进度条语义、按钮 loading、抽屉开合、命令面板开合） |
| JS 语法 | `node --check app/static/app.js` | 通过 |

## 4. 与评审清单的差异说明（如实记录）

- 评审建议"导出预览改为抽屉"已实现，但**保留了"下载 .md"按钮**（用户手动备份路径更直接），未移除。
- 评审建议"危险操作二次确认 + 撤销"两项都做了；撤销仅在**删除问答**与**取消相似关联**上开放（这两类数据可无损恢复），其余操作（入库、确认、模拟作答）不提供撤销——入库会写知识库频次，撤销语义复杂，避免产生"假撤销"。
- 评审建议"图标体系"仅覆盖关键动作（预览/下载/导入/播放/删除/搜索），其余按钮保留纯文字：个人工具场景下文字更清晰，属**有意取舍**。
- 未做（评审未列入 P0–P2，仍为已知边界）：列表虚拟滚动（分页已够）、`⌘K` 搜索结果高亮、Toast 队列上限（当前顺序堆叠）。

## 5. 复测方法

```bash
python scripts/smoke_e2e.py            # 109/109
python scripts/dev_seed.py             # 可选：灌演示数据
python run.py                          # 启动
python scripts/ui_kit_check.py         # UI Kit 行为 18/18（需 Chrome/Edge）
python scripts/screenshot_ui.py --dark # 界面截图（浅色/--dark/--mobile）
python scripts/ui_audit.py             # 像素审计 AUDIT_OK
```
