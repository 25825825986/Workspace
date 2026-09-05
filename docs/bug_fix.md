# Bug 修复记录：下载接口挂起（Content-Disposition 中文文件名）

> 状态：已修复并验证 ｜ 涉及版本：Phase 3 骨架（Flask + Werkzeug 3.1.8）
> 修复文件：`app/main.py`（另见仓库内 `docs/03-phase3-delivery.md`）

## 1. Bug 现象（本地实测复现）

- `GET /bank/export`（导出预览页）正常，约 14ms 返回；
- `GET /download/bank.md` 与 `GET /download/interviews/<id>.md` 挂起：
  连接建立后返回 0 字节，客户端一直等到超时；
- 服务以 `threaded=True` 运行，因此只有下载受影响，其余页面正常。

复现命令（修复前）：

```text
curl.exe -sS -m 8 -D headers.txt -o body.bin http://127.0.0.1:8000/download/bank.md
→ curl: (28) Operation timed out after 8011ms with 0 bytes received   （headers.txt 为空）
curl.exe -sS -m 8 -o body.bin http://127.0.0.1:8000/bank/export
→ exit 0，body 3760 字节（对照：页面正常）
```

## 2. 根因

原两个下载路由把**中文字符直接写入响应头 Content-Disposition**：

```python
# 修复前（节选）
headers={"Content-Disposition": f'attachment; filename="{fname}"'}        # 面试复盘-xxx.md
headers={"Content-Disposition": 'attachment; filename="面经库快照.md"'}   # 面经库快照.md
```

HTTP/1.1 响应头按 latin-1 序列化，Werkzeug ≥ 3.1 无法把非 ASCII（中文）编码进
latin-1 header，响应头序列化抛异常发生在连接处理线程内 → 连接不关闭也不写字节，
客户端表现为"连上了但 0 字节、直到超时"。（Flask 内置测试客户端内部不走该序列化路径，
所以 `scripts/smoke_e2e.py` 中旧的 200 断言未暴露此问题；真实 socket 服务必然复现。）

## 3. 修改内容（app/main.py）

| 位置 | 改动 |
|------|------|
| 第 12–13 行 | 新增 `import re`、`from urllib.parse import quote` |
| 第 130–183 行（导出下载区） | 重写为三个工具函数 + 两个路由调用它们 |

新增/修改的函数：

- `_sanitize_filename(name, fallback)`（L141）：清洗文件名的控制字符、
  换行与 Windows/HTTP 非法字符 `<>:"/\|?*` 及 `\x00-\x1f` → 替换为 `_`，
  折叠空白、去头尾点与空格、截断到 150 字符；空结果回退默认名。
- `_content_disposition(utf8_name, ascii_fallback)`（L150）：按 **RFC 6266 / RFC 5987**
  生成 latin-1 安全的响应头：
  - 文件名全 ASCII → `filename="原名"` 直接用；
  - 含中文 → `filename="ASCII 回退名"` + `filename*=UTF-8''<百分号编码的真实名>`。
- `_download_md(md, utf8_name, ascii_fallback)`（L165）：统一生成附件响应，
  mimetype 只传 `text/markdown`（顺带消除 Werkzeug 追加 charset 造成的重复 `charset=utf-8`）。
- `download_interview_md`（L173）与 `download_bank_md`（L182）：改用上述函数；
  面试文件名 = `面试复盘-<标题>.md`（UTF-8 名）+ `review_<id>.md`（ASCII 回退）；
  面经库 = `面经库快照.md` + `bank_snapshot.md`。

顺带修复（同一区域暴露出的相邻问题）：

- **不存在 id 的面试下载从 500 改为 404**：`download_interview_md` 增加
  `if not iv: abort(404)`（修复前 `exporter.interview_to_md` 抛 KeyError → 500）。

## 4. 验证结果

### 4.1 修复后真实服务（werkzeug 3.1.8，threaded）

```text
GET /download/bank.md
→ curl exit 0，HTTP 200，body 323 字节
  Content-Disposition: attachment; filename="bank_snapshot.md"; filename*=UTF-8''%E9%9D%A2%E7%BB%8F%E5%BA%93%E5%BF%AB%E7%85%A7.md
  Content-Type: text/markdown; charset=utf-8

GET /download/interviews/<id>.md（标题为中文：示例公司-后端工程师-2024-05-20）
→ curl exit 0，HTTP 200，body 5093 字节
  Content-Disposition: attachment; filename="review_<id>.md"; filename*=UTF-8''%E9%9D%A2%E8%AF%95%E5%A4%8D%E7%9B%98-...
  Content-Type: text/markdown; charset=utf-8

GET /download/interviews/nope.md（不存在的 id）
→ HTTP 404（原为 500）
```

### 4.2 边界用例（脚本化断言全部通过）

对以下文件名逐一生成响应头并断言 `header.encode("latin-1")` 成功、
清洗结果不含非法字符：

- 纯中文 `面经库快照.md` / 中文+连字符的面试标题
- 含非法字符 `a"b/c\d|e*f:g?h<k>` + 换行 → 全部替换为 `_`
- 含 emoji `emoji😀ok` → 走 filename* 百分号编码
- 空串 → 回退 `fallback.md`；全 ASCII 标题 → 直接使用原名
- 200 字符超长名 → 截断到 150；连字符 `-` 合法保留（不被误清洗）

### 4.3 回归

`python scripts/smoke_e2e.py`：**28/28 断言通过**（含 R2 提取不被改写、R3 跨场去重计数、
两处下载与导出分节等原有全部用例）。

## 5. 遗留边界与说明（已知、可接受）

1. **老旧的下载工具/代理**只认 `filename=`（ASCII 回退名，如 `bank_snapshot.md`），
   忽略 `filename*`，此时保存为 ASCII 名而非中文名 —— 现代浏览器均优先 `filename*`，
   属 RFC 兼容的正常降级。
2. **同名文件覆盖**：浏览器/下载器行为决定（默认自动改名或询问），服务端不处理（个人本地工具无必要）。
3. **非 UTF-8 文件名**：`filename*` 固定按 UTF-8 百分号编码（RFC 5987 标准做法）；
   若确需 GBK 历史客户端兼容需额外提供 `filename*=GBK''`，本项目无此诉求。
4. **Windows 保留名**（如 `CON.md`、`NUL.md`）与末尾点/空格已由 `_sanitize_filename` 清洗大部分；
   极端保留名（`CON` 系列）在极少数下载器落地 Windows 时仍可能失败 —— 个人场景影响可忽略，
   如需彻底规避可再加保留名黑名单（P1）。
5. 测试客户端断言在 Flask 内置 client 下不经过 latin-1 序列化，**验证挂起必须以真实
   socket/curl 复现**（见第 1 节复现命令与第 4.1 节结果）。

## 6. 复测方法

```bash
python run.py                                    # 启动后：
curl -sS -m 10 -D - -o /dev/null http://127.0.0.1:8000/download/bank.md
curl -sS -m 10 -D - -o /dev/null http://127.0.0.1:8000/download/interviews/<id>.md
python scripts/smoke_e2e.py                       # 28/28
```
