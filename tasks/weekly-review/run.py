"""LangGraph PSE — personal-crm 每周关系复盘（weekly-review 任务）。

这是 build_graph 通用核心的第二个落地任务，用来验证「通用核心可复用」：
- 复用 crm-qa 同款只读工具（query_crm / crm_qa_scan）与通用 PSE 图
- 新增任务只需：tasks/weekly-review/prompts/*.md + 本文件注入 verify_fn + 确定性数据层

流程：
    默认：直接跑确定性聚合（零成本，无需 langgraph / API key）
    --llm：用通用 PSE 图 → Planner 提纲 → Specialist 写报告 →
           Evaluator(LLM) 评审 → Verify(程序化核对数字+人名) → (不符则 Fix 重试)

用法:
    python run.py                 # 仅跑确定性聚合并打印复盘
    python run.py --llm          # 额外用 LLM 生成自然语言复盘（需 langgraph + key）
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
from review_data import DEFAULT_DB, gather  # noqa: E402

# 让通用核心能找到本任务的提示词 (tasks/weekly-review/prompts/*.md)
sys.path.insert(0, str(BASE.parent.parent / "src"))


def _norm(s: str) -> str:
    """规范化表头/键名：去空格、全角括号转半角，便于容错匹配。"""
    return s.strip().replace("（", "(").replace("）", ")").replace(" ", "")


def _parse_table(report: str, keyword: str) -> list[dict]:
    """在报告中找到含 keyword 的标题行，解析其后首个 Markdown 表格，返回 [{规范列名: 单元格}]。"""
    lines = report.splitlines()
    hi = -1
    for i, ln in enumerate(lines):
        if ln.strip().startswith("#") and keyword in ln:
            hi = i
            break
    if hi == -1:
        return []
    table = []
    for ln in lines[hi + 1:]:
        s = ln.strip()
        if s.startswith("|"):
            table.append(s)
        elif table:
            break  # 表格结束
    if len(table) < 2:
        return []
    headers = [_norm(h) for h in table[0].strip("|").split("|")]
    rows = []
    for tr in table[2:]:  # 跳过表头 + 分隔行
        cells = [c.strip() for c in tr.strip("|").split("|")]
        if len(cells) != len(headers):
            continue
        rows.append({headers[i]: cells[i] for i in range(len(headers))})
    return rows


def _verify_report(report: str, data: dict) -> tuple[list, list]:
    """程序化验证：报告的数字指标必须命中真实 metrics；表格人名/天数/日期必须命中样本。

    设计原则（与 crm-qa 一致）：宁可漏报也不误报——只要报告出现了真实值即视为通过，
    避免 LLM 用自然语言表述（如「约 6%」）被误判为幻觉而让 fix 死循环。
    但表格行做「强校验」：人名必须真实、天数/日期必须精确，杜绝编造联系人。
    """
    bad: list[str] = []
    ok: list[str] = []

    metrics = data.get("metrics", {})
    # ── 1) 关键指标：报告里出现过该真实值（含千分位）即过 ──
    for k, v in metrics.items():
        forms = {str(v), f"{v:,}"}
        if any(form in report for form in forms):
            ok.append(f"指标 {k} = {v}")
        else:
            bad.append(f"报告未出现真实指标 {k} 的值 {v}")

    # ── 2) 变冷表格：强校验人名 + 天数 ──
    cool_names = {c["name"] for c in data.get("cooling_sample", [])}
    cool_map = {c["name"]: c for c in data.get("cooling_sample", [])}
    cool_rows = _parse_table(report, "变冷")
    stale_30d = metrics.get("stale_30d", 0)
    if stale_30d == 0:
        if cool_rows:
            bad.append("报告列出了变冷联系人，但真实 stale_30d=0")
    else:
        expected = min(stale_30d, 15)
        if len(cool_rows) != expected:
            bad.append(f"变冷表格行数 {len(cool_rows)} 但应为 {expected}（stale_30d≤15 时全列，否则列前15）")
        for r in cool_rows:
            name = r.get(_norm("联系人"))
            if name not in cool_names:
                bad.append(f"变冷表格含未知联系人: {name}")
                continue
            real = cool_map[name]
            cell = r.get(_norm("距上次(天)"), "")
            if str(real["days_since"]) not in cell:
                bad.append(f"变冷表格 {name} 的「距上次(天)」={cell} 不符真实值 {real['days_since']}")
            else:
                ok.append(f"变冷 {name} 真实")

    # ── 3) 跟进表格：强校验人名 + 日期 ──
    fu_names = {c["name"] for c in data.get("follow_up_sample", [])}
    fu_map = {c["name"]: c for c in data.get("follow_up_sample", [])}
    fu_rows = _parse_table(report, "跟进") or _parse_table(report, "建议")
    expected_fu = min(len(data.get("follow_up_sample", [])), 15)
    if expected_fu == 0:
        if fu_rows:
            bad.append("报告列出了跟进联系人，但真实 follow_up 样本为空")
    else:
        if len(fu_rows) != expected_fu:
            bad.append(f"跟进表格行数 {len(fu_rows)} 但应为 {expected_fu}")
        for r in fu_rows:
            name = r.get(_norm("联系人"))
            if name not in fu_names:
                bad.append(f"跟进表格含未知联系人: {name}")
                continue
            real = fu_map[name]
            cell = r.get(_norm("跟进日期"), "")
            if real["follow_up_date"] not in cell:
                bad.append(f"跟进表格 {name} 的「跟进日期」={cell} 不符真实值 {real['follow_up_date']}")
            else:
                ok.append(f"跟进 {name} 真实")

    return bad, ok


def _verify_state(state: dict) -> tuple[list, list]:
    return _verify_report(
        state.get("artifact", ""),
        state.get("task_data", {}).get("review_data", {}),
    )


def main():
    ap = argparse.ArgumentParser(description="personal-crm 每周关系复盘 (langgraph-pse)")
    ap.add_argument("--db", default=os.getenv("CRM_DB_PATH", DEFAULT_DB))
    ap.add_argument("--llm", action="store_true",
                    help="用 LLM 生成自然语言复盘（需 langgraph + API key）")
    ap.add_argument("--provider", choices=["deepseek", "free"], default="deepseek",
                    help="LLM 网关：deepseek（默认）或 free（需配置 FREE_*）")
    args = ap.parse_args()

    print(f"🔍 聚合每周关系复盘数据: {args.db}")
    data = gather(args.db)
    m = data["metrics"]
    print(f"   联系人 {m['total_contacts']} | 本周互动 {m['week_interactions']} "
          f"(涉及 {m['week_contacts']} 人) | 连续 {m['streak']} 天")
    print(f"   近30天活跃 {m['active_30d']} | 变冷(>30天) {m['stale_30d']} | 从未互动 {m['never_interacted']}")
    di = m["week_interactions_delta"]
    print(f"   本周互动环比上周: {di:+d} (上周 {m['week_interactions_prev']} 次 / {m['week_contacts_prev']} 人)")
    print(f"   跟进逾期 {m['follow_up_overdue']} | 7天内需跟进 {m['follow_up_due_7d']}")

    if not args.llm:
        print("\n## 变冷 Top")
        for c in data["cooling_sample"]:
            flag = " 超期" if c["over_target"] else ""
            print(f"  - {c['name']} ({c['group']}) 距上次 {c['days_since']} 天 / "
                  f"目标 {c['target_frequency_days']} 天{flag}")
        print("\n## 建议主动联系")
        for c in data["follow_up_sample"]:
            print(f"  - {c['name']} ({c['group']}) 跟进 {c['follow_up_date']} [{c['urgency']}] {c['follow_up_note']}")
        print("\n（加 --llm 可用 LangGraph PSE 生成自然语言复盘并做数字/人名核对）")
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
        task="weekly-review",
        verify_fn=_verify_state,
        use_planner=True,
        max_retries=max_retries,
        provider=args.provider,
    )

    task_input = (
        "以下是 personal-crm 数据库的每周关系复盘聚合数据（JSON），请据此撰写每周关系复盘报告：\n\n"
        f"{data_str}"
    )
    result = graph.invoke({
        "artifact": "",
        "task_data": {"review_data": data},
        "task_input": task_input,
        "max_retries": max_retries,
    })
    report = result.get("artifact", "")
    out_path = BASE / "weekly_review.md"
    out_path.write_text(report, encoding="utf-8")
    print(f"\n✅ 自然语言复盘已保存 → {out_path}")


if __name__ == "__main__":
    main()
