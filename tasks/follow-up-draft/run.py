"""LangGraph PSE — personal-crm 跟进消息草拟（follow-up-draft 任务）。

这是 build_graph 通用核心的第三个落地任务，验证「通用核心可复用」：
- 复用 crm-qa / weekly-review 同款只读查库模式与通用 PSE 图
- 新增任务只需：tasks/follow-up-draft/prompts/*.md + 本文件注入 verify_fn + 确定性数据层

流程：
    默认：直接跑确定性聚合（零成本，无需 langgraph / API key）
    --llm：用通用 PSE 图 → Planner 提纲 → Specialist 草拟 →
           Evaluator(LLM) 评审 → Verify(程序化核对人名+备注) → (不符则 Fix 重试)

用法:
    python run.py                 # 仅打印候选人与真实上下文
    python run.py --llm          # 额外用 LLM 生成跟进草稿（需 langgraph + key）
    python run.py --db <路径>     # 指定 crm.db
"""

import argparse
import json
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent

try:
    from dotenv import load_dotenv
    load_dotenv(BASE.parent.parent / ".env")
except Exception:
    pass  # 无 python-dotenv 时退化为直接用环境变量 / 默认路径

sys.path.insert(0, str(BASE))
from draft_data import DEFAULT_DB, gather  # noqa: E402

# 让通用核心能找到本任务的提示词 (tasks/follow-up-draft/prompts/*.md)
sys.path.insert(0, str(BASE.parent.parent / "src"))


def _verify_drafts(report: str, data: dict) -> tuple[list, list]:
    """程序化验证：每张草稿必须对应真实候选人，且不得歪曲跟进备注。

    设计原则（与 crm-qa / weekly-review 一致）：宁漏不误——
    - 强校验『人名』：报告必须为每个候选人都产出一段草稿（按 ### 标题匹配真实姓名）
    - 强校验『跟进备注』：若候选人 follow_up_note 非空，草稿须包含该备注原文
      （防止 LLM 无视/篡改用户的跟进意图）
    - 不强制校验聊天细节（自然语言复述易误判），但 LLM 评审会兜底
    """
    bad: list[str] = []
    ok: list[str] = []

    candidates = data.get("candidates", [])
    if not candidates:
        # 无候选人时，报告应明确写「无」，不得编造草稿
        if "无" in report or "暂无" in report or "没有" in report:
            ok.append("无候选人，报告已如实说明")
        else:
            bad.append("真实数据中无候选人，但报告疑似产出了草稿（可能编造）")
        return bad, ok

    # 1) 每个候选人都应有对应草稿段
    for c in candidates:
        name = c["name"]
        if name not in report:
            bad.append(f"报告缺少 {name} 的草稿（该联系人有待跟进事项）")
            continue
        # 2) 跟进备注原文须出现在草稿中
        note = c.get("follow_up_note", "")
        if note:
            if note not in report:
                bad.append(f"{name} 的草稿未包含跟进备注原文：「{note}」")
            else:
                ok.append(f"{name} 草稿含跟进备注")
        else:
            ok.append(f"{name} 草稿已生成")

    return bad, ok


def _verify_state(state: dict) -> tuple[list, list]:
    return _verify_drafts(
        state.get("artifact", ""),
        state.get("task_data", {}).get("draft_data", {}),
    )


def main():
    ap = argparse.ArgumentParser(description="personal-crm 跟进消息草拟 (langgraph-pse)")
    ap.add_argument("--db", default=os.getenv("CRM_DB_PATH", DEFAULT_DB))
    ap.add_argument("--llm", action="store_true",
                    help="用 LLM 生成跟进草稿（需 langgraph + API key）")
    ap.add_argument("--provider", choices=["deepseek", "free"], default="deepseek",
                    help="LLM 网关：deepseek（默认）或 free（需配置 FREE_*）")
    args = ap.parse_args()

    print(f"🔍 聚合跟进草拟数据: {args.db}")
    data = gather(args.db)
    m = data["metrics"]
    print(f"   待草拟候选人 {m['candidates']} | 逾期 {m['follow_up_overdue']} | 7天内 {m['follow_up_due_7d']}")

    if not args.llm:
        for c in data["candidates"]:
            print(f"\n### {c['name']} ({c['group']}) [{c['urgency']}]")
            print(f"    跟进日期: {c['follow_up_date']}  最近互动: {c['last_interaction']}")
            if c["follow_up_note"]:
                print(f"    跟进备注: {c['follow_up_note']}")
            print(f"    最近聊天 ({len(c['recent_chat'])} 条):")
            for ch in c["recent_chat"]:
                print(f"      [{ch['dir']} {ch['date']}] {ch['content']}")
        print("\n（加 --llm 可用 LangGraph PSE 生成个性化跟进草稿并做人名/备注核对）")
        return

    # ── LLM 模式：复用通用 PSE 核心 ──
    try:
        from langgraph_pse.config import settings
        from langgraph_pse.graph import build_graph
    except Exception as e:
        print(f"❌ 无法加载 langgraph 运行环境: {e}\n（请先 `uv sync` 并配置对应 provider 的 API key）")
        sys.exit(1)

    data_str = json.dumps(data, ensure_ascii=False, indent=2)
    max_retries = settings.PSE_MAX_RETRIES or 3

    graph = build_graph(
        task="follow-up-draft",
        verify_fn=_verify_state,
        use_planner=True,
        max_retries=max_retries,
        provider=args.provider,
    )

    task_input = (
        "以下是 personal-crm 数据库中需要跟进的联系人及其真实上下文（JSON）。"
        "请为每位联系⼈草拟一条个性化的跟进消息：\n\n"
        f"{data_str}"
    )
    result = graph.invoke({
        "artifact": "",
        "task_data": {"draft_data": data},
        "task_input": task_input,
        "max_retries": max_retries,
    })
    report = result.get("artifact", "")
    out_path = BASE / "follow_up_drafts.md"
    out_path.write_text(report, encoding="utf-8")
    print(f"\n✅ 跟进草稿已保存 → {out_path}")


if __name__ == "__main__":
    main()
