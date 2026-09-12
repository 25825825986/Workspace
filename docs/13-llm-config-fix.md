# 模型 API 无法调用：排查与修复记录

> 现象：已在项目里配置了模型 API，但 AI 功能仍不可用（调用失败）。
> 结论：**配置值填错两处**（地址多写了模型路径、模型 ID 缺厂商前缀）；网络、Key、依赖均正常。
> 修复后：`test_connection` 通过（1.8–3.4s）、真实 function calling 通过、**完整 AI 抽取入库跑通**。

---

## 1. 定位过程（证据链）

| 步骤 | 命令/检查 | 结果 |
|------|-----------|------|
| 1 | 读 `data/settings.json` | `llm.base_url = https://api.siliconflow.cn/v1/deepseek-ai/DeepSeek-V4-Flash`、`model = DeepSeek-V4-Flash` |
| 2 | 读 `data/secrets.json` | Key 已保存（掩码 `sk-t****zvhr`）✓ |
| 3 | 检查依赖 | venv 内 `openai 3.8.0` 已安装 ✓（`openai` 缺失时应用会自动降级为零依赖 HTTP） |
| 4 | 裸 HTTP 探测 `GET {base_url}/models` | **HTTP 404** ← 关键证据 |
| 5 | DNS | `api.siliconflow.cn → 47.102.37.23` 正常，**网络未被阻断**（能拿到服务端 404 响应） |
| 6 | `GET https://api.siliconflow.cn/v1/models` | HTTP 200，94 个模型；其中确有 `deepseek-ai/DeepSeek-V4-Flash` |
| 7 | `POST /v1/chat/completions` `model=DeepSeek-V4-Flash` | **HTTP 400 `Model does not exist`** ← 第二个问题 |
| 8 | 同接口 `model=deepseek-ai/DeepSeek-V3` | HTTP 200 ✓（证明平台与 Key 都可用） |

### 根因
1. **Base URL 多写了模型路径**：填成 `…/v1/deepseek-ai/DeepSeek-V4-Flash`，SDK 会在其后拼 `/chat/completions`，最终请求 `…/v1/deepseek-ai/DeepSeek-V4-Flash/chat/completions` → **404**。
   正确写法：Base URL 只到版本段 `https://api.siliconflow.cn/v1`。
2. **模型 ID 缺少厂商前缀**：`DeepSeek-V4-Flash` 不存在；平台实际 ID 为 **`deepseek-ai/DeepSeek-V4-Flash`**（SiliconFlow 的模型 ID 带 `厂商/` 前缀）。带前缀后调用成功。

## 2. 修复内容

### 2.1 配置修正（已写入你的 `data/settings.json`）
```json
{
  "theme": "dark",
  "ai_mode": "auto",
  "llm": {
    "base_url": "https://api.siliconflow.cn/v1",
    "model": "deepseek-ai/DeepSeek-V4-Flash"
  }
}
```
> 主题与 AI 模式保持不变；Key 未改动。

### 2.2 代码加固（避免同类问题再次发生）
| 改动 | 位置 | 作用 |
|------|------|------|
| **地址自动规范化** `normalize_base_url()` | `app/config.py` | 自动去掉 `/chat/completions`、`/models` 等端点后缀；若版本段（`/v1`）后还跟着路径（通常是模型 ID），**自动截断到版本段**并给出修正说明 |
| **两步式连接测试** | `app/pipeline/llm.py` | 「测试连接」先查 `/models` 校验模型 ID 是否存在（不存在则给出**相近候选**），再做最小对话；错误按状态码翻译成人话（404→地址多了路径、400→模型 ID 问题、401→Key 无效、403→余额/权限、429→限流） |
| **拉取可用模型** | `/api/settings/models` + 设置页按钮 | 一键拉取平台模型列表填入下拉框（`datalist`），不用手抄 ID |
| **零依赖 HTTP 兜底** | `app/pipeline/llm.py` | 未安装 `openai` 包时，自动用标准库 `urllib` 走同一套 OpenAI 兼容协议（function calling 一样可用） |
| **设置页提示** | `app/templates/settings.html` | 说明"Base URL 只填到版本段"；地址被自动修正时在页面黄色提示并建议保存 |
| **R2 相关归一化** | `app/pipeline/postprocess.py` | LLM 把"你有什么想问我们的吗"邀请话术误判为反问时，自动改用候选人的提问作为问题（与规则路径一致） |
| **测试数据隔离** | `scripts/smoke_e2e.py` + `AI_REVIEW_DATA_DIR` | 冒烟测试改用 `.test-data/`，**不再触碰真实 `data/` 与其中的 Key** |

## 3. 验证结果

### 3.1 配置与连通性（`python scripts/llm_diag.py`）
```
ai_mode        : auto        extractor_mode : deepseek
api_key        : sk-t****zvhr
base_url 生效  : https://api.siliconflow.cn/v1
model          : deepseek-ai/DeepSeek-V4-Flash
平台模型列表校验：共 94 个模型；配置的模型是否存在：True
最小对话测试：ok=True，连接成功（3356 ms）
```

### 3.2 真实端到端（`python scripts/llm_e2e_check.py`）→ **11/11 通过**
- 配置规范化 ✓、模型列表 ✓（模型在列表中 ✓）
- **function calling（SDK 路径）** ✓ 返回结构化 `extract_qa` 结果
- **零依赖 urllib 兜底通道** ✓ 同样返回工具调用
- **完整 AI 抽取入库** ✓：`extractor=deepseek`、`parser_version` 含 deepseek、问题回填**原文区间**（`quote` 可在原文中核对）、回答保留口误/卡壳（"嗯…呃…"）、反问环节识别为 `direction=reverse`（且已修正为"我的提问"语义）

### 3.3 回归（`python scripts/smoke_e2e.py`）→ **116/116 通过**
含新增的离线断言：4 组 base_url 规范化用例、误填后自动修正提示、保存后设置接口返回规范化结果、未配置 Key 时模型列表接口 400 + 提示。

## 4. 以后遇到 AI 调不通，按这个顺序查

```bash
python scripts/llm_diag.py         # ① 配置解析 + 模型列表校验 + 最小对话（不修改数据）
python run.py                      # ② 打开「设置 → 模型 API」点「测试连接」「拉取可用模型」
python scripts/llm_e2e_check.py    # ③ 真实端到端（工具调用 + 完整抽取入库）
```

对照表：

| 报错 | 含义 | 处理 |
|------|------|------|
| HTTP 404 | Base URL 多了路径 | 只填到 `/v1`（本工具会自动截断并提示） |
| HTTP 400 `Model does not exist` | 模型 ID 错 | 点「拉取可用模型」选择正确 ID（常见需 `厂商/` 前缀） |
| HTTP 401 | Key 无效/过期 | 重新生成 Key 并保存 |
| HTTP 403 | 无权限/余额不足 | 检查账户额度 |
| HTTP 429 | 限流 | 稍后重试（应用已内置 1 次重试） |
| 网络不可达 | 断网/代理/防火墙 | 检查网络与代理设置 |
| 未配置 API Key | Key 未保存 | 「设置 → 模型 API」填写并保存 |
