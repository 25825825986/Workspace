"""简历导入解析（Phase 7 / Q7）。

支持 `.txt / .md / .docx`：
- `.docx` 采用**零依赖**方案——docx 本质是 zip 包，读取 `word/document.xml`，
  抽取 `<w:t>` 文本节点并保留段落换行；因此本机不装 python-docx 也能用。
- PDF 明确不支持（需额外依赖），由调用方给出提示；解析结果允许人工编辑（Q7 优化）。
"""
from __future__ import annotations

import base64
import binascii
import re
import zipfile
from io import BytesIO

SUPPORTED_EXT = (".txt", ".md", ".markdown", ".docx")

# 技能词典（用于从简历中抽取技能关键词；命中即计入，可人工再改）
SKILL_VOCAB = (
    "python", "java", "golang", "go", "c++", "c#", "javascript", "typescript", "vue", "react",
    "node", "spring", "springboot", "django", "flask", "fastapi", "mysql", "postgresql",
    "oracle", "redis", "mongodb", "elasticsearch", "kafka", "rabbitmq", "rocketmq", "docker",
    "kubernetes", "k8s", "linux", "git", "jenkins", "ci/cd", "spark", "flink", "hadoop", "hive",
    "clickhouse", "nginx", "grpc", "restful", "微服务", "分布式", "高并发", "高可用", "性能优化",
    "系统设计", "领域驱动", "ddd", "devops", "自动化测试", "单元测试", "机器学习", "深度学习",
    "pytorch", "tensorflow", "spark", "sql", "shell", "prometheus", "grafana", "skywalking",
)

_PROJECT_SUFFIX = ("系统", "平台", "服务", "项目", "模块", "中台", "引擎", "组件", "工具")
_PROJECT_RE = re.compile(r"[A-Za-z0-9\u4e00-\u9fa5][A-Za-z0-9\u4e00-\u9fa5\-_/（）()]{1,28}?"
                         r"(?:" + "|".join(_PROJECT_SUFFIX) + r")")
_YEARS_RE = re.compile(r"(\d{1,2})\s*(?:年|years?)\s*(?:以上)?\s*(?:工作)?(?:经验|经历)?")


class ResumeError(ValueError):
    """简历解析失败（文件类型/内容问题），转为 HTTP 400 友好提示。"""


def decode_data_url(data_url: str) -> bytes:
    """解析前端 FileReader 产生的 dataURL（base64）。"""
    if not data_url:
        raise ResumeError("未收到文件内容")
    payload = data_url.split(",", 1)[-1]
    try:
        return base64.b64decode(payload, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise ResumeError("文件内容不是合法的 base64 数据") from exc


def extract_text(filename: str, raw: bytes) -> str:
    """按扩展名抽取纯文本。"""
    name = (filename or "").strip().lower()
    if name.endswith(".docx"):
        return _docx_text(raw)
    if name.endswith((".txt", ".md", ".markdown")) or name == "":
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            try:
                return raw.decode("gbk")
            except UnicodeDecodeError as exc:
                raise ResumeError("文本编码无法识别（请另存为 UTF-8 的 .txt/.md）") from exc
    if name.endswith(".pdf"):
        raise ResumeError("暂不支持 PDF：请另存为 .docx / .txt / .md 后再导入")
    raise ResumeError(f"不支持的文件类型：{filename or '未知'}（支持 .txt/.md/.docx）")


def _docx_text(raw: bytes) -> str:
    """零依赖抽取 docx 文本（zip → word/document.xml → <w:t> 节点）。"""
    try:
        with zipfile.ZipFile(BytesIO(raw)) as zf:
            xml = zf.read("word/document.xml").decode("utf-8", errors="ignore")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ResumeError("docx 文件损坏或格式不正确") from exc
    paragraphs: list[str] = []
    for para in re.findall(r"<w:p[ >].*?</w:p>", xml, flags=re.S):
        texts = re.findall(r"<w:t[^>]*>(.*?)</w:t>", para, flags=re.S)
        line = "".join(texts)
        line = (line.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                    .replace("&quot;", '"').replace("&apos;", "'")).strip()
        if line:
            paragraphs.append(line)
    if not paragraphs:                       # 有些 docx 用 <w:t/> 直接排版
        paragraphs = [t.strip() for t in re.findall(r"<w:t[^>]*>(.*?)</w:t>", xml, flags=re.S)
                      if t.strip()]
    return "\n".join(paragraphs)


def parse_keywords(text: str) -> dict:
    """抽取技能 / 项目名 / 经验年限（结果可在界面上人工修改）。"""
    low = (text or "").lower()
    skills: list[str] = []
    for term in SKILL_VOCAB:
        if term in low and term not in skills:
            skills.append(term)
    projects: list[str] = []
    for m in _PROJECT_RE.finditer(text or ""):
        name = m.group(0).strip()
        if 3 <= len(name) <= 32 and name not in projects:
            projects.append(name)
    years = 0
    m = _YEARS_RE.search(text or "")
    if m:
        try:
            years = min(int(m.group(1)), 40)
        except ValueError:
            years = 0
    return {"skills": skills[:30], "projects": projects[:12], "years": years}
