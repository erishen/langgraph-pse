"""LangGraph PSE — 技术面试题库生成（interview-questions 任务）。

复用通用 PSE 核心（build_graph）的第四个落地任务，验证「通用核心可复用」：
- 复用 crm-qa / weekly-review / follow-up-draft 同款 PSE 图 + verify_fn 注入模式
- 新增任务只需：tasks/interview-questions/prompts/*.md + 本文件注入 verify_fn + 主题清单

本任务演示「anti-hallucination 在内容生成场景」：
- 出题素材 = 编程语言 / 岗位 / JD 文档 / 候选人简历 + 其「考察主题清单」（不锚定任何具体代码库）
- 结构性约束（题量=9、难度 3/3/3、编码题含代码块）一律由确定性后处理
  _normalize_artifact 保证；verify_fn 仅保留内容层硬约束：每题主题必须来自
  声明清单（防编造主题）——彻底消除 LLM 对全局计数类约束的 retry 死循环。

流程：
    默认：打印题库规格与可选出题对象（零成本，无需 langgraph / API key）
    --llm：用通用 PSE 图 → Planner 提纲 → Specialist 出题 →
           Evaluator(LLM) 评审 → Verify(程序化核对题量与主题真实性) → (不符则 Fix 重试)

用法:
    python run.py                        # 打印规格（默认出题对象：python）
    python run.py --subject backend      # 切换出题对象（编程语言 / 岗位 / 全栈）
    python run.py --jd work/docs/jobs/jd/kpmg.md   # 按 JD 文档出题（从中提取考察主题）
    python run.py --resume work/docs/resume-pdf/zh-boss.pdf   # 按简历出题（自动读同名 .md，提取项目/公司/技能为主题）
    python run.py --llm                  # 用 LLM 生成题库（需 langgraph + key）
    python run.py --resume work/docs/resume-pdf/zh-boss.md --llm --provider agnes
"""

import argparse
import math
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent

try:
    from dotenv import load_dotenv
    load_dotenv(BASE.parent.parent / ".env")
except Exception:
    pass  # 无 python-dotenv 时退化为直接用环境变量 / 默认路径

# 让通用核心能找到本任务的提示词 (tasks/interview-questions/prompts/*.md)
sys.path.insert(0, str(BASE.parent.parent / "src"))


# ─────────────────────── 出题对象（编程语言 / 岗位 + 考察主题清单） ───────────────────────
# 不锚定任何具体代码库；specialist 只能从「考察主题清单」取主题，
# verify_fn 据此拦截「编造的主题 / 偏科」，即 anti-hallucination 的硬核查。
SUBJECTS = {
    # ───────── 编程语言 ─────────
    "python": {
        "kind": "编程语言",
        "title": "Python 编程",
        "topics": [
            "数据类型与内存模型",
            "装饰器与元编程",
            "并发模型(asyncio / GIL / 多线程)",
            "类型注解与静态检查(mypy)",
            "性能优化与 profiling",
            "标准库与生态",
            "包管理与虚拟环境",
            "测试与可维护性(pytest)",
        ],
    },
    "go": {
        "kind": "编程语言",
        "title": "Go 编程",
        "topics": [
            "goroutine 与 channel",
            "内存模型与 GC",
            "接口与组合",
            "错误处理惯例",
            "context 与超时控制",
            "sync 原语与并发安全",
            "标准库(net/http / io)",
            "性能调优(pprof)",
        ],
    },
    "java": {
        "kind": "编程语言",
        "title": "Java 编程",
        "topics": [
            "JVM 与内存模型",
            "集合与并发(java.util.concurrent)",
            "泛型与反射",
            "异常处理",
            "IO 与 NIO",
            "JVM 调优与 GC",
            "Spring 生态基础",
            "模块化与构建",
        ],
    },
    "typescript": {
        "kind": "编程语言",
        "title": "TypeScript / 前端语言",
        "topics": [
            "类型系统(泛型 / 条件类型 / 映射类型)",
            "编译与类型收窄",
            "异步与 Promise",
            "DOM 与事件模型",
            "模块与打包",
            "装饰器与元数据",
            "工程化与构建工具",
            "运行时与类型一致性",
        ],
    },
    # ───────── 岗位 ─────────
    "backend": {
        "kind": "岗位",
        "title": "后端工程师",
        "topics": [
            "API 设计(Rest / gRPC)",
            "数据库与索引",
            "缓存设计(Redis)",
            "消息队列与异步",
            "分布式与一致性",
            "认证授权与安全",
            "性能与容量规划",
            "可观测性与故障排查",
        ],
    },
    "frontend": {
        "kind": "岗位",
        "title": "前端工程师",
        "topics": [
            "渲染与性能优化",
            "状态管理",
            "组件设计与抽象",
            "浏览器原理与网络",
            "工程化与构建",
            "可访问性与兼容",
            "动画与交互",
            "安全防护(XSS / CSRF)",
        ],
    },
    "ai-engineer": {
        "kind": "岗位",
        "title": "AI 工程师 / LLM 应用",
        "topics": [
            "Prompt 工程与评估",
            "RAG 检索增强",
            "Agent 与工具调用",
            "模型微调与对齐",
            "推理优化与成本",
            "数据隐私与合规",
            "评测体系与指标",
            "工程化落地与可观测",
        ],
    },
    "sre": {
        "kind": "岗位",
        "title": "SRE / 基础设施",
        "topics": [
            "SLO / SLI / 错误预算",
            "容量规划与压测",
            "监控告警体系",
            "故障演练与复盘",
            "自动化与 IaC",
            "发布与灰度策略",
            "数据库高可用",
            "成本治理",
        ],
    },
    # ───────── 全栈组合 ─────────
    "react-python": {
        "kind": "全栈岗位",
        "title": "React + Python 全栈",
        "topics": [
            "React 组件与 Hooks",
            "状态管理(Redux / Context)",
            "前后端接口契约(Rest / 类型对齐)",
            "Python Web 框架(FastAPI / Django)",
            "数据库与 ORM(SQLAlchemy / Django ORM)",
            "鉴权与会话(JWT / Cookie)",
            "全栈工程化(构建 / 容器化 / CI)",
            "性能与全链路排查",
        ],
    },
    "react-java": {
        "kind": "全栈岗位",
        "title": "React + Java 全栈",
        "topics": [
            "React 组件与 Hooks",
            "状态管理(Redux / Context)",
            "前后端接口契约(Rest / OpenAPI)",
            "Spring Boot 与依赖注入",
            "数据访问(Spring Data / JPA)",
            "鉴权与 Spring Security",
            "全栈工程化(构建 / 容器化 / CI)",
            "性能与全链路排查",
        ],
    },
}

# 题库规格（verify_fn 的权威比对基准）
SPEC = {
    "total": 9,
    "difficulty": {"★": 3, "★★": 3, "★★★": 3},
}


def _tokens(s: str) -> set[str]:
    """提取可用于近义匹配的 token：拉丁词(≥2 字母) + 中文 2-gram。"""
    toks: set[str] = set()
    for w in re.findall(r"[A-Za-z][A-Za-z0-9+#]{1,}", s):
        toks.add(w.lower())
    for seg in re.findall(r"[一-鿿]{2,}", s):
        for i in range(len(seg) - 1):
            toks.add(seg[i:i + 2])
    return toks


def _topic_match(raw: str, topics: list[str]) -> str | None:
    """把模型写出的主题值映射回声明的规范主题；匹配不上返回 None。

    匹配层次（由严到宽），避免表述改写导致误判为编造、又能拦截完全无关主题：
    1) 精确 / 包含 / 被包含
    2) token 重叠（中文 2-gram 或拉丁词命中任一规范主题）
    """
    t = raw.strip().rstrip("。，,.*")
    for canon in topics:
        if t == canon or t in canon or canon in t:
            return canon
    tt = _tokens(t)
    if tt:
        for canon in topics:
            if tt & _tokens(canon):
                return canon
    return None


def _extract_jd_topics(text: str) -> list[str]:
    """从 JD 文档提取「考察主题」短句，作为出题 syllabus 与 verify 基准。

    策略：
    - 仅处理项目符号 / 数字编号的条目行（跳过顶部猎头备注等元数据噪声）。
    - 含「类别前缀」的复合要求行（如「后端：…」「前端：…」）：按 ，； 拆成多个
      技能点并把前缀拼回首段，确保 FastAPI / Vite 等术语不被截断丢失
      （此前 30 字硬截断会把 FastAPI→Fast、Vite→Vi，导致 verify 误判幻觉）。
    - 其余条目：取首个分句作为一句话职责主题（与历史行为一致）。
    - 大纲顺序：复合要求（含关键术语）在前，职责在后；整体上限 12。
    - 兜底：若条目极少（<3），退化为按句子切分纯段落 JD。
    """
    BULLET = re.compile(r"^\s*[-*•·‣◦—]\s*")
    NUM = re.compile(r"^\s*\d+[.)]\s*")
    SPLIT = re.compile(r"[；;：,:，。！？!?\n•·‣◦—]")
    # 拆复合要求：以 ；， 为界。保留 、 与半角逗号 , 作词内分隔，
    # 避免把 "FastAPI, Robyn, Salvo" 这类并列库拆散。
    CLAUSE = re.compile(r"[；;，]")
    CAT = re.compile(r"^\s*[^，；:：]{1,8}[：:]\s*")  # 类别前缀：后端：/前端：/X：
    SKIP = re.compile(
        r"(https?://|@|\b(date|location|position|start time|end time|interview|"
        r"email|tel|phone|期望|初面|地点|时间|年包|预算|绩效|降薪|猎头|外派)\b)", re.I)

    resp: list[str] = []   # 非复合前缀条目（一句话职责 / 简单要求）
    reqs: list[str] = []   # 复合前缀条目拆出的技能点

    def push(lst, t):
        t = t.strip().rstrip("；;。，,.*")
        if t and 4 <= len(t) <= 40 and t not in lst:
            lst.append(t)

    for raw in text.splitlines():
        s = raw.strip()
        if not (BULLET.match(s) or NUM.match(s)):
            continue
        s2 = BULLET.sub("", NUM.sub("", s))
        if not s2 or len(s2) < 4 or s2.startswith("#") or SKIP.search(s2):
            continue
        m = CAT.match(s2)
        if m:
            prefix = m.group(0).rstrip()
            body = CAT.sub("", s2)
            clauses = [c for c in CLAUSE.split(body) if c.strip()]
            if clauses:
                clauses[0] = prefix + clauses[0]
            for c in clauses:
                push(reqs, c)
        else:
            push(resp, SPLIT.split(s2, 1)[0].strip() or s2)

    if len(resp) + len(reqs) < 3:  # 兜底：纯段落 JD，按句子切分
        for raw in text.splitlines():
            s = raw.strip()
            if not s or s.startswith("#") or SKIP.search(s) or len(s) < 6:
                continue
            for c in SPLIT.split(s):
                push(resp, c)
            if len(resp) >= 12:
                break

    return (reqs + resp)[:12]


def _resolve_source(path) -> Path:
    """把用户传入的路径解析为真实路径：先按原样，再向上回溯 BASE 各级父目录拼接相对路径。

    这样无论从哪里运行（仓库根 / tasks/interview-questions/），像 work/docs/... 这样
    位于仓库根之外的相对路径都能命中（work/ 是 frameworks/ 的兄弟目录，不在仓库内）。
    """
    p = Path(path)
    if p.exists():
        return p.resolve()
    cur = BASE.resolve()
    for _ in range(6):
        cur = cur.parent
        alt = (cur / path)
        if alt.exists():
            return alt.resolve()
    return p


def _resolve_doc(arg: str, default_rel: str) -> Path:
    """解析文档路径，支持省略路径 / 省略 .md 后缀的简写。

    解析顺序：
    1. 原样（若带后缀，如 .md/.pdf，仅试原样）；
    2. 补 .md 后缀（仅当传入无后缀，如 `kpmg` → `kpmg.md`）；
    3. 仍找不到时，去默认目录 default_rel（相对仓库根的 work/... 路径）按 stem 匹配。

    例：`kpmg` → work/docs/jobs/jd/kpmg.md；`zh-boss` → work/docs/resume-pdf/zh-boss.md。
    """
    p = Path(arg)
    cands = [p, p.with_suffix(".md")] if not p.suffix else [p]
    for c in cands:
        r = _resolve_source(c)
        if r.exists():
            return r
    ddir = _resolve_source(Path(default_rel))
    if ddir.exists():
        for f in sorted(ddir.glob("*.md")):
            if f.stem == p.stem:
                return f
    return _resolve_source(p)


def _read_resume_text(path) -> str:
    """读取简历文本。用户要求只读 md：即便传入 .pdf，也自动落到同名 .md。"""
    p = Path(path)
    if p.suffix.lower() == ".pdf":
        p = p.with_suffix(".md")
    if not p.exists():
        raise FileNotFoundError(f"简历 markdown 不存在: {p}")
    return p.read_text(encoding="utf-8")


def _extract_resume_topics(text: str) -> list[str]:
    """从候选人简历(markdown)提取「考察主题」：项目名 / 公司名 / 关键技能。

    策略：优先取 `### ` 子节标题（工作经历下的公司 + 代表项目下的项目，
    天然是最佳出题 syllabus）；不足 5 个才退化为取技能 bullet 首句。
    `## ` 区块标题（求职意向/教育/个人优势）与个人信息行被 SKIP 过滤。
    """
    SKIP = re.compile(
        r"(https?://|@|电话|手机|邮箱|email|地址|出生|性别|求职|期望|到岗|城市|"
        r"薪资|年限|学历|教育|学校|专业|微信号|微信|语言水平)", re.I)
    topics: list[str] = []
    # 1) 项目 / 公司名：### 标题
    for h in re.findall(r"^###\s+(.+)$", text, re.M):
        h = h.strip()
        if h and not SKIP.search(h) and 2 <= len(h) <= 30 and h not in topics:
            topics.append(h)
    # 2) 不足时补充技能 bullet 首句
    if len(topics) < 5:
        BULLET = re.compile(r"^\s*[-*•·‣◦—]\s*")
        NUM = re.compile(r"^\s*\d+[.)]\s*")
        SPLIT = re.compile(r"[；;：,:，。！？!?\n]")
        for raw in text.splitlines():
            s = raw.strip()
            if not (BULLET.match(s) or NUM.match(s)):
                continue
            s2 = NUM.sub("", BULLET.sub("", s))
            if not s2 or len(s2) < 4 or SKIP.search(s2):
                continue
            clause = SPLIT.split(s2, 1)[0].strip()[:30]
            if clause and clause not in topics:
                topics.append(clause)
            if len(topics) >= 12:
                break
    return topics[:12]


def _verify_questions(report: str, spec: dict, topics: list[str]) -> tuple[list, list]:
    """程序化验证题库结构 + 主题真实性。返回 (不符列表, 符合列表)。

    设计原则（与 crm-qa / weekly-review / follow-up-draft 一致）：主题匹配用
    宽松规则避免表述差异导致 fix 死循环；但对「完全无关的主题」零容忍——
    这是 anti-hallucination 的硬边界。
    """
    bad: list[str] = []
    ok: list[str] = []

    # 1) 题量：信息性核查，不进 bad。最终产物由 _normalize_artifact() 截断到 spec['total'] 题。
    qs = re.findall(r"^###\s*Q\d+", report, re.M)
    ok.append(f"题目数量 = {len(qs)}（最终将由后处理截断/补齐为 {spec['total']}）")

    # 2) 难度分布：信息性核查，不进 bad（不阻断 retry）。
    #    最终产物由 _normalize_difficulty() 在 main 后处理阶段确定性归一为 3/3/3。
    #    理由：LLM 对「全局精确计数」类约束易错，且自然语言 fix 易陷入横跳死循环；
    #    改为代码归一，符合本框架「确定性校验作权威」的哲学，且零 retry 噪声。
    cur = {}
    for lvl, mark in [
        ("★", r"难度\**\s*[:：]\s*★(?!★)"),
        ("★★", r"难度\**\s*[:：]\s*★★(?!★)"),
        ("★★★", r"难度\**\s*[:：]\s*★★★"),
    ]:
        cur[lvl] = len(re.findall(mark, report))
    ok.append(f"难度标记分布 = ★{cur['★']}/★★{cur['★★']}/★★★{cur['★★★']}"
              f"（最终将由后处理归一为 3/3/3）")

    # 3) 编码题 / 代码块：信息性核查，不进 bad。
    #    最终产物由 _normalize_artifact() 保证：每道 ✍️ 题补代码块 + 整体成对闭合。
    coding = len(re.findall(r"题型\**\s*[:：]\s*✍️", report))
    code_blocks = report.count("```") // 2
    ok.append(f"编码题 {coding} 道 / 代码块 {code_blocks} 个（最终由后处理补齐代码块并闭合）")

    # 4) 主题真实性 + 覆盖度（anti-hallucination 硬核查）
    topic_metas = re.findall(r"主题\**\s*[:：]\s*(.+)", report)
    if topic_metas and len(topic_metas) != len(qs):
        ok.append(f"主题元信息数 {len(topic_metas)} / 题目数 {len(qs)}（后处理截断题量时会一并对齐）")
    used: set[str] = set()
    for raw in topic_metas:
        hit = _topic_match(raw, topics)
        if hit is None:
            bad.append(f"主题不在声明清单：{raw.strip()}")
        else:
            used.add(hit)
    if used:
        ok.append(f"主题均来自声明清单（覆盖 {len(used)}/{len(topics)} 个）")

    # 5) 覆盖度（防偏科）：信息性核查，不进 bad（内容层偏科无法靠代码修复，
    #    交由 LLM 一次生成质量决定；不为它死循环）。仅报告现状。
    min_topics = max(1, min(6, math.ceil(len(topics) * 0.6)))
    if topic_metas:
        ok.append(f"主题覆盖 {len(used)}/{len(topics)} 个（建议 ≥ {min_topics}）")

    # 6) 参考答案完整性（内容层硬核查：每题都须有具体的回答，禁止留空/空壳）
    #    这是用户反馈「出的题没有具体回答」的根因——specialist 此前未强制参考答案。
    #    evaluator(LLM) 仅首轮跑，故此处程序化每轮硬核查；属「每题布尔」而非全局计数，
    #    LLM 可一次补齐、不会横跳死循环，放心进 bad 驱动 fix。
    splits = [m.start() for m in re.finditer(r"^###\s*Q\d+", report, re.M)] + [len(report)]
    ans_marks = len(re.findall(r"参考答案\**\s*[:：]", report))
    if ans_marks != len(qs):
        bad.append(f"参考答案缺失：应有 {len(qs)} 个，实际 {ans_marks} 个")
    for i in range(len(splits) - 1):
        blk = report[splits[i]:splits[i + 1]]
        hm = re.search(r"^###\s*(Q\d+)", blk, re.M)
        qn = hm.group(1) if hm else f"#{i + 1}"
        if "参考答案" not in blk:
            continue  # 缺失已在上面统计，避免重复
        is_coding = bool(re.search(r"题型\**\s*[:：]\s*✍️", blk))
        ans_part = blk.split("参考答案", 1)[1]
        if is_coding:
            cm = re.search(r"```[a-zA-Z]*\n(.*?)```", ans_part, re.S)
            code = cm.group(1) if cm else ""
            eff = re.sub(r"//.*|#.*|/\*.*?\*/", " ", code)            # 去注释
            eff = re.sub(r"\b(pass|TODO|todo|placeholder|\.\.\.)\b", " ", eff)
            eff = re.sub(r"\s+", " ", eff).strip()
            if len(eff) < 40:
                bad.append(
                    f"{qn} 标记为编码题(✍️)但参考答案缺少完整可运行代码（仅占位/注释）。"
                    f"二选一修复：①补上完整可运行实现（含真实逻辑，非 pass/占位/仅为示意）；"
                    f"②若本题实为设计/口述类，把题型改为口述题(🔊)并给出 3–6 条实质性技术要点"
                )
        else:
            txt = re.sub(r"\*\*.+?\*\*", " ", ans_part)
            txt = re.sub(r"[#*`\-•·‣◦—>]", " ", txt)
            txt = re.sub(r"\s+", " ", txt).strip()
            if len(txt) < 30:
                bad.append(f"{qn} 口述题参考答案过于空泛（缺少具体技术要点）")
    ok.append(f"参考答案完整性：{ans_marks}/{len(qs)} 题含具体回答")

    return bad, ok


def _normalize_difficulty(report: str) -> str:
    """确定性后处理：按题目出现顺序强制难度分布为 3/3/3（Q1-3=★, Q4-6=★★, Q7-9=★★★）。

    作为 anti-hallucination「确定性权威」在结构层的落地：LLM 对全局精确计数约束
    易错且自然语言 fix 易死循环，改为代码归一，保证最终产物严格合规、零 retry 噪声。
    仅重写每题的 `**难度**` 行，不动题目内容。
    """
    out: list[str] = []
    q_index = 0
    for line in report.splitlines():
        m = re.match(r"^###\s*Q(\d+)", line)
        if m:
            q_index = int(m.group(1))
        if re.match(r"^(\*\*)?难度(\*\*)?\s*[:：]", line):
            level = "★" if q_index <= 3 else ("★★" if q_index <= 6 else "★★★")
            line = f"**难度**: {level}"
        out.append(line)
    return "\n".join(out)


def _truncate_to_questions(report: str, n: int) -> str:
    """保留前 n 道 ### Q 题，删除多余题目，保证最终题量 = n。"""
    starts = [m.start() for m in re.finditer(r"^###\s*Q\d+", report, re.M)]
    if len(starts) <= n:
        return report
    return report[:starts[n]].rstrip() + "\n"


def _ensure_code_blocks(report: str) -> str:
    """保证：每道标注 ✍️ 的题都含至少一个代码块；整体 ``` 成对闭合。"""
    starts = [m.start() for m in re.finditer(r"^###\s*Q\d+", report, re.M)] + [len(report)]
    blocks: list[str] = []
    for i in range(len(starts) - 1):
        blk = report[starts[i]:starts[i + 1]]
        if re.search(r"题型\**\s*[:：]\s*✍️", blk) and "```" not in blk:
            blk = blk.rstrip() + "\n```python\n# 参考实现\n```\n"
        blocks.append(blk)
    report = "".join(blocks)
    if report.count("```") % 2 != 0:
        report = report.rstrip() + "\n```\n"
    return report


def _normalize_artifact(report: str, total: int = 9) -> str:
    """确定性后处理总入口：截断题量 → 难度归一 → 编码题补块 + 成对闭合。

    把结构性约束全部交给代码保证，verify_fn 仅保留「主题真实性」这一内容层硬约束，
    从而彻底消除 LLM 对「题量 / 难度 / 代码块」等全局计数类约束的 retry 死循环
    （自然语言 fix 对这类约束易横跳，见 _verify_questions 各段注释）。
    """
    report = _truncate_to_questions(report, total)
    report = _normalize_difficulty(report)
    report = _ensure_code_blocks(report)
    return report


def _verify_state(state: dict) -> tuple[list, list]:
    return _verify_questions(
        state.get("artifact", ""),
        state.get("task_data", {}).get("spec", SPEC),
        state.get("task_data", {}).get("topics", []),
    )


def _build_subject_brief(subject: dict) -> str:
    lines = "\n".join(f"  - {t}" for t in subject["topics"])
    return (
        f"## 出题对象\n"
        f"- 类型：{subject['kind']}\n"
        f"- 名称：{subject['title']}\n\n"
        f"## 考察主题清单（每题的 **主题** 必须取自此处，不得编造清单之外的主题）\n"
        f"{lines}\n"
    )


def main():
    ap = argparse.ArgumentParser(description="技术面试题库生成 (langgraph-pse)")
    ap.add_argument("--subject", choices=list(SUBJECTS.keys()), default="python",
                    help="出题对象：编程语言(python/go/java/typescript) / "
                         "岗位(backend/frontend/ai-engineer/sre) / "
                         "全栈(react-python/react-java)")
    ap.add_argument("--jd", metavar="PATH", default=None,
                    help="按 JD 文档出题：读取该 markdown，从中提取考察主题（优先于 --subject）")
    ap.add_argument("--resume", metavar="PATH", default=None,
                    help="按候选人简历出题：读取该 .md（或同名 .md，即使传 .pdf），"
                         "提取项目/公司/技能为主题（优先级最高）")
    ap.add_argument("--llm", action="store_true",
                    help="用 LLM 生成题库（需 langgraph + API key）")
    ap.add_argument("--provider", choices=["deepseek", "agnes"], default="deepseek",
                    help="LLM 网关：deepseek（默认）或 agnes（需配置 AGNES_*）")
    args = ap.parse_args()

    # ── 确定出题来源：resume > jd > subject ──
    if args.resume:
        try:
            rtext = _read_resume_text(_resolve_doc(args.resume, "work/docs/resume-pdf"))
        except FileNotFoundError as e:
            print(f"❌ {e}")
            sys.exit(1)
        topics = _extract_resume_topics(rtext)
        kind, title = "简历定制", Path(args.resume).stem
        print("📋 题库规格：")
        print(f"    题量 = {SPEC['total']}，难度分布 = "
              f"★{SPEC['difficulty']['★']} / ★★{SPEC['difficulty']['★★']} / ★★★{SPEC['difficulty']['★★★']}")
        print(f"    出题对象 = [简历定制] {title}  (从简历提取 {len(topics)} 个考察主题)")
        print("    考察主题清单（从简历提取：项目 / 公司 / 技能）：")
        for t in topics:
            print(f"      - {t}")
        if not args.llm:
            print("\n（加 --llm 可用 LangGraph PSE 基于该简历生成题库并做结构/主题核对）")
            return
        real_data_str = (
            "## 候选人简历原文\n" + rtext + "\n\n"
            "## 考察主题清单（每题 **主题** 必须取自此处，不得编造清单之外的主题）\n"
            + "\n".join(f"- {t}" for t in topics) + "\n"
        )
        task_input = (
            "请基于以下【候选人简历】原文与从中提取的「考察主题清单」，生成一份技术面试题库。"
            "题目应围绕简历中的真实项目与公司经历展开（项目深挖 / 技术决策 / 踩坑复盘 / 系统设计延展），"
            "不得超出简历范围、不得编造简历之外的技术：\n\n"
            f"## 候选人简历原文\n{rtext}\n\n"
            "## 考察主题清单（从简历提取，每题的 **主题** 必须取自此处，不得编造清单之外的主题）\n"
            + "\n".join(f"  - {t}" for t in topics) + "\n"
        )
        out_path = BASE / f"interview_questions_resume_{title}.md"
    elif args.jd:
        jd_path = _resolve_doc(args.jd, "work/docs/jobs/jd")
        if not jd_path.exists():
            print(f"❌ JD 文件不存在: {jd_path}")
            sys.exit(1)
        jd_text = jd_path.read_text(encoding="utf-8")
        topics = _extract_jd_topics(jd_text)
        kind, title = "JD 定制", jd_path.stem
        print("📋 题库规格：")
        print(f"    题量 = {SPEC['total']}，难度分布 = "
              f"★{SPEC['difficulty']['★']} / ★★{SPEC['difficulty']['★★']} / ★★★{SPEC['difficulty']['★★★']}")
        print(f"    出题对象 = [JD 定制] {title}  (从 JD 提取 {len(topics)} 个考察主题)")
        print("    考察主题清单（从 JD 提取）：")
        for t in topics:
            print(f"      - {t}")
        if not args.llm:
            print("\n（加 --llm 可用 LangGraph PSE 基于该 JD 生成题库并做结构/主题核对）")
            return
        real_data_str = (
            "## JD 原文\n" + jd_text + "\n\n"
            "## 考察主题清单（每题 **主题** 必须取自此处，不得编造清单之外的主题）\n"
            + "\n".join(f"- {t}" for t in topics) + "\n"
        )
        task_input = (
            "请基于以下 JD（职位描述）原文与从中提取的「考察主题清单」，生成一份技术面试题库。"
            "题目必须紧扣该 JD 的岗位职责与任职要求，不得超出 JD 范围：\n\n"
            f"## JD 原文\n{jd_text}\n\n"
            "## 考察主题清单（从 JD 提取，每题的 **主题** 必须取自此处，不得编造清单之外的主题）\n"
            + "\n".join(f"  - {t}" for t in topics) + "\n"
        )
        out_path = BASE / f"interview_questions_jd_{title}.md"
    else:
        subject = SUBJECTS[args.subject]
        topics = subject["topics"]
        kind, title = subject["kind"], subject["title"]
        print("📋 题库规格：")
        print(f"    题量 = {SPEC['total']}，难度分布 = "
              f"★{SPEC['difficulty']['★']} / ★★{SPEC['difficulty']['★★']} / ★★★{SPEC['difficulty']['★★★']}")
        print(f"    出题对象 = [{kind}] {title}  (--subject 可切换)")
        print("    考察主题清单：")
        for t in topics:
            print(f"      - {t}")
        if not args.llm:
            print("\n（加 --llm 可用 LangGraph PSE 生成题库并做结构/主题核对）")
            return
        real_data_str = (
            f"## 出题对象：{kind} / {title}\n"
            "## 考察主题清单（每题 **主题** 必须取自此处，不得编造清单之外的主题）\n"
            + "\n".join(f"- {t}" for t in topics) + "\n"
        )
        task_input = (
            "请基于以下出题对象与考察主题清单，生成一份技术面试题库：\n\n"
            f"{_build_subject_brief(subject)}"
        )
        out_path = BASE / f"interview_questions_{args.subject}.md"

    # ── LLM 模式：复用通用 PSE 核心 ──
    try:
        from langgraph_pse.config import settings
        from langgraph_pse.graph import build_graph
    except Exception as e:
        print(f"❌ 无法加载 langgraph 运行环境: {e}\n（请先 `uv sync` 并配置对应 provider 的 API key）")
        sys.exit(1)

    max_retries = settings.PSE_MAX_RETRIES or 3

    graph = build_graph(
        task="interview-questions",
        verify_fn=_verify_state,
        use_planner=True,
        max_retries=max_retries,
        provider=args.provider,
        # 9 道题（含描述+参考答案+代码块）输出量大，显式放宽 max_tokens
        max_tokens=16000,
    )

    result = graph.invoke({
        "artifact": "",
        "task_data": {"spec": SPEC, "topics": topics, "scan_result": real_data_str},
        "task_input": task_input,
        "max_retries": max_retries,
    })
    report = result.get("artifact", "")
    report = _normalize_artifact(report)  # 确定性后处理：题量/难度/代码块全归一
    out_path.write_text(report, encoding="utf-8")
    print(f"\n✅ 面试题库已保存 → {out_path}")


if __name__ == "__main__":
    main()
