"""知识库归类（Phase 6）：三个专题 + 一级分类。

设计（见 docs/04 §FR-03）：
- 三个**专题**单独总结：自我介绍 / 个人发展 / 反问环节；
- 其余按**一级分类**：项目经历 / 技术栈 / 系统设计 / 团队协作 / 行为问题 / 其他；
- 分类是"打标 + 可人工修正"的结果，持久化在 `entries.topic` 与 `entries.group_name`。
"""
from __future__ import annotations

# 专题（顺序即页面顺序）
TOPICS: list[tuple[str, str, str]] = [
    ("self_intro", "自我介绍", "跨场汇总所有自我介绍，打磨不同版本的表述与时长"),
    ("growth", "个人发展", "职业规划 / 优缺点 / 离职原因 / 求职动机 / 成长诉求"),
    ("reverse", "反问环节", "我向面试官提出的问题（含面试官回答），下次面试可直接复用"),
]
TOPIC_KEYS = [key for key, _, _ in TOPICS]
TOPIC_LABELS = {key: label for key, label, _ in TOPICS}
TOPIC_DESCS = {key: desc for key, _, desc in TOPICS}

# 一级分类（顺序即页面顺序）
GROUPS = ["项目经历", "技术栈", "系统设计", "团队协作", "行为问题", "其他"]

_SELF_INTRO = ("自我介绍", "介绍一下你自己", "介绍下你自己", "自我评价", "自我介绍一下")
_GROWTH = ("职业规划", "个人发展", "优缺点", "优点和缺点", "离职", "为什么离开", "为什么离职",
           "求职动机", "为什么选择我们", "为什么来", "成长", "三年后", "五年后", "打算怎么发展",
           "职业目标", "期望薪资", "薪资要求")
_REVERSE_INVITE = ("你有什么想问", "有什么想问我们", "你有什么问题", "想问我们的", "有什么想问的吗")

_SYSDESIGN = ("系统设计", "架构设计", "高并发", "高可用", "怎么设计一个", "设计一个", "限流",
              "微服务", "容量规划", "扩展性", "分布式事务", "消息队列", "缓存架构", "怎么保证")
_PROJECT = ("项目", "负责", "难点", "亮点", "最有成就感", "成果", "业务", "上线", "实习经历")
_TEAM = ("团队", "协作", "沟通", "跨部门", "配合", "冲突", "leader", "管理", "带人", "推动",
         "合作", "同事")
_BEHAVIOR = ("压力", "失败", "困难", "挑战", "最难忘", "举例说明", "star", "情景", "怎么处理",
             "如何看待", "最有成就", "最大的")
_TECH = ("jvm", "java", "redis", "缓存", "算法", "数据库", "mysql", "sql", "http", "tcp",
         "线程", "并发", "锁", "内存", "gc", "网络", "spring", "索引", "事务", "垃圾回收",
         "linux", "docker", "kafka", "分布式", "接口", "性能优化", "数据结构", "设计模式")


def _hit(text: str, keys: tuple[str, ...]) -> bool:
    low = (text or "").lower()
    return any(k.lower() in low for k in keys)


def detect_topic(question: str, category_name: str = "",
                 category_type: str = "", direction: str = "normal") -> str:
    """专题判定：反问 > 自我介绍 > 个人发展。

    「反问环节」只在明确满足条件时成立（R1）：
      - 抽取阶段已判定 `direction=reverse`（面试官邀请 + 我提问 + 面试官回答），或
      - 问题文本本身是面试官的邀请话术且答案来自我（此时归入个人发展/其他，不算反问）。
    """
    if (direction or "") == "reverse":
        return "reverse"
    text = f"{category_name or ''} {question or ''}"
    if _hit(text, _SELF_INTRO):
        return "self_intro"
    if _hit(text, _GROWTH):
        return "growth"
    return ""


def detect_group(question: str, category_name: str = "",
                 category_type: str = "", topic: str = "") -> str:
    """一级分类判定（专题条目直接归到专题名下的分组）。

    顺序很重要：**系统设计 → 技术栈 → 项目经历 → 团队协作 → 行为问题 → 其他**。
    技术栈优先于"项目/行为"关键词，避免"项目里用的缓存怎么实现"被误归到项目经历。
    """
    if topic:
        return TOPIC_LABELS.get(topic, "其他")
    text = f"{category_name or ''} {question or ''}"
    if _hit(text, _SYSDESIGN):
        return "系统设计"
    if (category_type or "") == "technical" or _hit(text, _TECH):
        return "技术栈"
    if _hit(text, _PROJECT):
        return "项目经历"
    if _hit(text, _TEAM):
        return "团队协作"
    if _hit(text, _BEHAVIOR):
        return "行为问题"
    return "其他"


def classify(question: str, category_name: str = "", category_type: str = "",
             direction: str = "normal") -> tuple[str, str]:
    """返回 (topic, group_name)。"""
    topic = detect_topic(question, category_name, category_type, direction)
    return topic, detect_group(question, category_name, category_type, topic)


def is_reverse_invite(text: str) -> bool:
    """面试官的"你有什么想问我们的吗"邀请话术（供抽取阶段判定反转向）。"""
    return _hit(text or "", _REVERSE_INVITE)
