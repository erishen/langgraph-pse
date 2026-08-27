"""LangGraph PSE — 通用 Planner → Specialist → Evaluator → Fix 状态机核心。

任务无关：通过 task 参数加载 tasks/<task>/prompts/{planner,specialist,evaluator}.md，
通过 verify_fn 注入任务专属的程序化核查。当前内置 4 个任务（均复用同一图，不改核心）：
crm-qa（CRM 数据质量看门狗）、weekly-review（每周关系复盘）、
follow-up-draft（跟进消息草拟）、interview-questions（面试题库生成，非 CRM 场景）。

图结构:
    START → [planner] → specialist → evaluator ─┬─(通过)─▶ END
                                              └─(仍有问题)─▶ fix → evaluator (循环)
- planner / specialist：LLM 两角色（规划 / 执行）
- evaluator：合并闸门 = LLM 评审(仅首轮) + 程序化 verify_fn 硬核查（每轮，防编造）
- fix：LLM 按核查出的问题修正产物
"""

import json
import re
from typing import Callable, Optional, TypedDict

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph

from .model import create_model
from .prompts import load_prompt
from .tools import read_file, run_bash


class PSEState(TypedDict, total=False):
    task_input: str          # 给 planner / specialist 的任务上下文
    task_data: dict          # 任务额外数据（如扫描结果），供 verify_fn 使用
    plan: str                # Planner 输出的执行规划
    artifact: str            # Specialist 输出的产物（报告 / 修正数据等）
    attempts: int
    fictitious: list         # 核查出的问题列表（程序化 + 评审合并）
    verified: list           # 通过项
    eval_issues: list        # 评审员(LLM)发现的问题，仅首轮有效
    max_retries: int


# ─────────────────────── 节点 ───────────────────────

def _make_planner_node(client, prompt: str, tools):
    def planner(state: PSEState) -> dict:
        task = state.get("task_input", "")
        agent = create_agent(client, tools, system_prompt=prompt or None)
        result = agent.invoke({"messages": [HumanMessage(content=task)]})
        plan = result["messages"][-1].content
        print(f"✅ 规划已完成 ({len(plan)} 字)")
        return {"plan": plan}

    return planner


def _make_specialist_node(client, prompt: str, tools):
    def specialist(state: PSEState) -> dict:
        task = state.get("task_input", "")
        ctx = state.get("plan") or ""
        full = (task + "\n\n## 执行规划\n" + ctx) if ctx else task
        agent = create_agent(client, tools, system_prompt=prompt or None)
        result = agent.invoke({"messages": [HumanMessage(content=full)]})
        artifact = result["messages"][-1].content
        if not artifact:
            raise RuntimeError("Specialist 未输出任何内容")
        return {"artifact": artifact}

    return specialist


def _real_data(state: PSEState) -> dict:
    """取出任务真实数据载荷，任务无关：按各任务实际使用的 key 依次取首个非空值。

    各任务把真实数据放在 task_data 的不同键下：crm-qa 用 scan_result，
    weekly-review 用 review_data，follow-up-draft 用 draft_data；
    interview-questions 复用 scan_result。evaluator / fix 节点据此注入真实对照，
    避免把校验/修正逻辑写死在单一 key 上。
    """
    td = state.get("task_data", {}) or {}
    return (td.get("scan_result") or td.get("review_data")
            or td.get("draft_data") or {})


def _parse_eval_issues(text: str) -> list[str]:
    """解析评审员(LLM)输出：PASS/无问题 → []；否则收集 '- ' 开头的真实缺陷行。

    安全网：评审员偶尔违反纪律，把「XX（通过）」「XX 一致」类确认行也写成
    '- ' 开头，会被误当成问题触发无谓的 fix 重试（浪费 token）。这里显式丢弃
    含确认语义的行，只保留真正需要修复的缺陷。
    """
    if not text:
        return []
    if "PASS" in text.upper() and "-" not in text:
        return []
    confirm = re.compile(r"（通过）|通过。|一致。|无遗漏|✅|✔|——通过")
    issues: list[str] = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s.startswith("- "):
            continue
        body = s[2:].strip()
        if confirm.search(body):
            continue
        issues.append(body)
    return issues


def _make_evaluator_node(client, prompt: str, verify_fn: Optional[Callable]):
    """合并闸门：LLM 评审(仅首轮) + 程序化 verify_fn 硬核查(每轮)。

    - 首轮(attempts==0)调用 LLM 对照真实数据审查产物，输出问题清单；
      仅首轮跑 LLM，避免主观问题导致 fix 死循环。
    - 程序化 verify_fn 每轮都跑（确定性，比 LLM 自判可靠，防编造）。
    - 二者合并为 fictitious，驱动 fix 重试。
    """
    def evaluator(state: PSEState) -> dict:
        artifact = state.get("artifact", "")
        scan = _real_data(state)
        attempts = state.get("attempts", 0)

        # 1) LLM 评审（仅首轮）
        eval_issues: list = []
        if attempts == 0 and prompt:
            scan_str = json.dumps(scan, ensure_ascii=False, indent=2)
            full = (
                f"## 待评估的产物\n{artifact}\n\n"
                f"## 真实数据（供核对，禁止以产物之外的内容为依据）\n{scan_str}"
            )
            agent = create_agent(client, [], system_prompt=prompt)
            result = agent.invoke({"messages": [HumanMessage(content=full)]})
            text = result["messages"][-1].content
            eval_issues = _parse_eval_issues(text)
            print(f"  🔍 评审员发现问题 {len(eval_issues)} 项")

        # 2) 程序化核查（每轮）
        if verify_fn is not None:
            prog_bad, ok = verify_fn(state)
        else:
            prog_bad, ok = [], []

        all_bad = list(prog_bad) + list(eval_issues)
        print(f"\n{'=' * 60}")
        print(f"  核查 (第{attempts + 1}次) — 程序化通过 {len(ok)} 项, "
              f"程序化问题 {len(prog_bad)} 项, 评审问题 {len(eval_issues)} 项")
        for b in all_bad:
            print(f"    ❌ {b}")
        if not all_bad:
            print("  ✅ 核查通过")
        return {"fictitious": all_bad, "verified": ok,
                "attempts": attempts + 1, "eval_issues": eval_issues}

    return evaluator


def _make_fix_node(client):
    def fix(state: PSEState) -> dict:
        artifact = state["artifact"]
        issues = state.get("fictitious", [])
        if not issues:
            return {"artifact": artifact, "eval_issues": []}
        # 把真实数据注入修正上下文，避免 LLM 凭空编造/删数字
        scan = _real_data(state)
        scan_str = json.dumps(scan, ensure_ascii=False, indent=2)
        print("  🔄 自动修正中...")
        prompt = (
            "以下产物被程序化核查发现问题，请修正。\n\n"
            "**问题清单（必须修复）**:\n" + "\n".join(f"- {i}" for i in issues) + "\n\n"
            "**真实数据（修正时必须以此为准，把错误数字改为真实值，"
            "不得编造也不得删除数字）**:\n"
            f"{scan_str}\n\n"
            "**规则**:\n"
            "1. 仅修正问题清单中指出的错误，将错误数字改为真实数据中的正确值\n"
            "2. 不要删除任何正确的数字或内容，保持其余部分不变\n"
            "3. 若真实数据中某样本列表（如 cooling_sample / follow_up_sample）为空，"
            "对应章节必须写明「无」，严禁编造联系人、数字或创建表格行\n"
            "4. 输出修正后的完整产物，不输出解释\n\n"
            f"## 当前产物\n{artifact}"
        )
        resp = client.invoke([HumanMessage(content=prompt)])
        fixed = resp.content if hasattr(resp, "content") else str(resp)
        # 清除评审问题，使后续重试只由程序化核查驱动
        return {"artifact": fixed, "eval_issues": []}

    return fix


def _should_fix(state: PSEState) -> str:
    if state.get("fictitious") and state.get("attempts", 0) <= state.get("max_retries", 3):
        return "fix"
    return END


# ─────────────────────── 图构建 ───────────────────────

def build_graph(
    client=None,
    task: Optional[str] = None,
    tools=None,
    verify_fn: Optional[Callable] = None,
    max_retries: int = 3,
    use_planner: bool = True,
    provider: str = "deepseek",
    max_tokens: Optional[int] = None,
):
    """构建并编译通用 PSE 图。

    client:      LangChain ChatModel（缺省按 provider 创建）。
    task:        任务名，用于加载 tasks/<task>/prompts/{planner,specialist,evaluator}.md。
    tools:       注入 agent 的工具列表（默认 read_file + run_bash）。
    verify_fn:   程序化核查函数，签名 (state) -> (bad: list, ok: list)；不传则默认通过。
    use_planner: 是否包含 planner 节点（无规划需求的任务可关掉，从 specialist 起步）。
    provider:    "deepseek" | "agnes"，决定 LLM 网关。
    max_tokens:  最大输出 token 数（仅在 client 为 None 时生效）。长输出任务建议显式设置。
    返回编译后的 graph，用 graph.invoke({task_input, task_data, max_retries}) 调用。
    """
    if client is None:
        client = create_model(provider, max_tokens=max_tokens)
    tools = tools or [read_file, run_bash]

    planner_prompt = load_prompt("planner", task) if use_planner else ""
    specialist_prompt = load_prompt("specialist", task)
    evaluator_prompt = load_prompt("evaluator", task)

    planner = _make_planner_node(client, planner_prompt, tools) if use_planner else None
    specialist = _make_specialist_node(client, specialist_prompt, tools)
    evaluator = _make_evaluator_node(client, evaluator_prompt, verify_fn)
    fix = _make_fix_node(client)

    g = StateGraph(PSEState)
    if use_planner:
        g.add_node("planner", planner)
    g.add_node("specialist", specialist)
    g.add_node("evaluator", evaluator)
    g.add_node("fix", fix)

    if use_planner:
        g.add_edge(START, "planner")
        g.add_edge("planner", "specialist")
    else:
        g.add_edge(START, "specialist")
    g.add_edge("specialist", "evaluator")
    g.add_conditional_edges("evaluator", _should_fix, {"fix": "fix", END: END})
    g.add_edge("fix", "evaluator")

    return g.compile()
