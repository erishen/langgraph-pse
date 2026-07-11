"""personal-crm 每周关系复盘数据聚合（确定性，只读）。

读取 crm.db（只读 + immutable），产出结构化复盘指标。
- 可由 run.py 直接打印（零成本，无需 langgraph / API key）
- 也可被 langgraph-pse 的 weekly-review 任务作为 task_data["review_data"] 注入（供 LLM 写报告 + 程序化 verify 对照）

设计原则：
- 只读打开（mode=ro&immutable），绝不修改 crm.db
- 所有数字均来自 contacts / contact_records / chat_messages 的真实字段，零编造
- 中性占位默认 db 路径：真实库路径只通过环境变量 CRM_DB_PATH 或 --db 指定，绝不硬编码个人绝对路径
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

DEFAULT_DB = "crm.db"
_SHANGHAI = ZoneInfo("Asia/Shanghai")


def _open(db_path: str) -> sqlite3.Connection:
    # 只读 + immutable：即使库处于 WAL 运行态也能安全打开，不阻塞写入方
    con = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _local_date(dt: datetime) -> date:
    """chat_messages.sent_at 在库中存为 naive UTC → 转上海本地日期。"""
    if dt.tzinfo is None:
        return (dt + timedelta(hours=8)).date()
    return dt.astimezone(_SHANGHAI).date()


def _clean(s) -> str:
    """清洗会破坏 Markdown 表格的字符：姓名/分组里的竖线 | 改为全角 ｜，去除换行。"""
    return (str(s) if s is not None else "").replace("|", "｜").replace("\n", " ").strip()


def gather(db_path: str = DEFAULT_DB) -> dict:
    if not os.path.exists(db_path):
        raise FileNotFoundError(
            f"找不到 CRM 数据库: {db_path}\n"
            f"请通过环境变量 CRM_DB_PATH 指定，或在运行时加 --db <绝对路径>；\n"
            f"例如在 .env 中设置 CRM_DB_PATH=/path/to/crm.db。"
        )
    con = _open(db_path)
    cur = con.cursor()
    today = date.today()
    week_start = today - timedelta(days=today.weekday())  # 周一 00:00
    month_start = today.replace(day=1)
    d30 = today - timedelta(days=30)
    d7_future = today + timedelta(days=7)

    total_contacts = cur.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    chat_messages_total = cur.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0]
    contact_records_total = cur.execute("SELECT COUNT(*) FROM contact_records").fetchone()[0]

    # 本周互动（按 contact_records.contact_date >= 本周一）
    wr = cur.execute(
        "SELECT COUNT(*), COUNT(DISTINCT contact_id) FROM contact_records WHERE contact_date >= ?",
        (week_start.isoformat(),),
    ).fetchone()
    week_interactions = int(wr[0] or 0)
    week_contacts = int(wr[1] or 0)

    # 连续互动天数（近 30 天，按 contact_date 是否出现）
    recent_dates = set()
    for (s,) in cur.execute(
        "SELECT contact_date FROM contact_records WHERE contact_date >= ?",
        (d30.isoformat(),),
    ).fetchall():
        try:
            recent_dates.add(date.fromisoformat(s))
        except (ValueError, TypeError):
            pass
    streak = 0
    check = today
    for _ in range(30):
        if check in recent_dates:
            streak += 1
            check -= timedelta(days=1)
        else:
            break

    month_contacts = int(cur.execute(
        "SELECT COUNT(DISTINCT contact_id) FROM contact_records WHERE contact_date >= ?",
        (month_start.isoformat(),),
    ).fetchone()[0] or 0)

    # 本周新增（首次互动 >= 本周一）
    first_inter = dict(cur.execute(
        "SELECT contact_id, MIN(contact_date) FROM contact_records GROUP BY contact_id"
    ).fetchall())
    new_contacts_this_week = sum(
        1 for d in first_inter.values()
        if d and date.fromisoformat(d) >= week_start
    )

    # 每联系人元信息
    meta = {r["id"]: r for r in cur.execute(
        'SELECT id, name, "group" AS grp, target_frequency_days FROM contacts'
    ).fetchall()}
    name_of = {cid: (m["name"] or f"联系人#{cid}") for cid, m in meta.items()}
    group_of = {cid: (m["grp"] or "其他") for cid, m in meta.items()}
    target_of = {cid: (m["target_frequency_days"] or 30) for cid, m in meta.items()}

    # 最近互动日期：max(最近 contact_record.contact_date, 最近 chat 本地日期)
    last_record = dict(cur.execute(
        "SELECT contact_id, MAX(contact_date) FROM contact_records GROUP BY contact_id"
    ).fetchall())
    chat_local: dict[int, date] = {}
    for cid, sent_raw in cur.execute(
        "SELECT contact_id, sent_at FROM chat_messages "
        "WHERE sent_at IS NOT NULL AND contact_id IS NOT NULL"
    ).fetchall():
        try:
            sd = _local_date(datetime.fromisoformat(sent_raw))
        except (ValueError, TypeError):
            continue
        cid = int(cid)
        if cid not in chat_local or sd > chat_local[cid]:
            chat_local[cid] = sd

    def last_interaction(cid: int) -> date | None:
        dates: list[date] = []
        lr = last_record.get(cid)
        if lr:
            try:
                dates.append(date.fromisoformat(lr))
            except (ValueError, TypeError):
                pass
        if cid in chat_local:
            dates.append(chat_local[cid])
        return max(dates) if dates else None

    # 计算每联系人距上次互动天数（None = 从未互动）
    staleness: dict[int, int | None] = {}
    for cid in meta:
        li = last_interaction(cid)
        staleness[cid] = (today - li).days if li else None

    active_30d_ids = {
        cid for cid, days in staleness.items()
        if days is not None and (today - timedelta(days=days)) >= d30
    }
    active_30d = len(active_30d_ids)

    stale_ids = [cid for cid, days in staleness.items() if days is not None and days > 30]
    stale_30d = len(stale_ids)
    never_interacted = sum(1 for days in staleness.values() if days is None)

    # 变冷 Top15（有互动，按天数降序）
    cooling_sorted = sorted(
        ((cid, days) for cid, days in staleness.items() if days is not None),
        key=lambda kv: -kv[1],
    )[:15]
    cooling_sample = [
        {
            "id": cid,
            "name": _clean(name_of[cid]),
            "group": _clean(group_of.get(cid, "其他")),
            "days_since": days,
            "target_frequency_days": target_of.get(cid, 30),
            "over_target": days > target_of.get(cid, 30),
        }
        for cid, days in cooling_sorted
    ]

    # 跟进：contact_records.follow_up_date 非空，逾期(高) / 7天内(中)
    fu_rows = cur.execute(
        "SELECT contact_id, follow_up_date, follow_up_note "
        "FROM contact_records WHERE follow_up_date IS NOT NULL"
    ).fetchall()
    fu_list = []
    for cid, fud, note in fu_rows:
        try:
            fud_d = date.fromisoformat(fud)
        except (ValueError, TypeError):
            continue
        if fud_d < today:
            urgency = f"高(逾期{(today - fud_d).days}天)"
        elif fud_d <= d7_future:
            urgency = f"中({(fud_d - today).days}天后)"
        else:
            continue  # 超过 7 天不纳入本周建议
        fu_list.append({
            "id": cid,
            "name": _clean(name_of.get(int(cid), f"联系人#{cid}")),
            "group": _clean(group_of.get(int(cid), "其他")),
            "follow_up_date": fud,
            "follow_up_note": note or "",
            "urgency": urgency,
        })
    # 高紧急在前（"高" > "中" 字典序）
    fu_list.sort(key=lambda x: x["urgency"], reverse=True)
    follow_up_sample = fu_list[:15]
    follow_up_overdue = sum(1 for x in fu_list if x["urgency"].startswith("高"))
    follow_up_due_7d = sum(1 for x in fu_list if x["urgency"].startswith("中"))

    # 分组透视
    by_group: dict[str, dict[str, int]] = {}
    for cid in meta:
        g = group_of.get(cid, "其他")
        d = by_group.setdefault(g, {"contacts": 0, "active_30d": 0, "stale_30d": 0})
        d["contacts"] += 1
        if cid in active_30d_ids:
            d["active_30d"] += 1
        if staleness[cid] is not None and staleness[cid] > 30:
            d["stale_30d"] += 1

    con.close()

    metrics = {
        "total_contacts": total_contacts,
        "week_interactions": week_interactions,
        "week_contacts": week_contacts,
        "streak": streak,
        "month_contacts": month_contacts,
        "new_contacts_this_week": new_contacts_this_week,
        "chat_messages_total": chat_messages_total,
        "contact_records_total": contact_records_total,
        "active_30d": active_30d,
        "stale_30d": stale_30d,
        "never_interacted": never_interacted,
        "follow_up_overdue": follow_up_overdue,
        "follow_up_due_7d": follow_up_due_7d,
    }
    return {
        "db_path": db_path,
        "generated_at": today.isoformat(),
        "metrics": metrics,
        "cooling_sample": cooling_sample,
        "follow_up_sample": follow_up_sample,
        "by_group": by_group,
    }


def main():
    ap = argparse.ArgumentParser(description="personal-crm 每周关系复盘数据聚合")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    try:
        result = gather(args.db)
    except FileNotFoundError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    m = result["metrics"]
    print(f"数据库: {result['db_path']}  (生成于 {result['generated_at']})")
    print(f"联系人 {m['total_contacts']} | 本周互动 {m['week_interactions']} "
          f"涉及 {m['week_contacts']} 人 | 连续 {m['streak']} 天")
    print(f"本月互动人数 {m['month_contacts']} | 本周新增 {m['new_contacts_this_week']}")
    print(f"近30天活跃 {m['active_30d']} | 变冷(>30天) {m['stale_30d']} | 从未互动 {m['never_interacted']}")
    print(f"跟进逾期 {m['follow_up_overdue']} | 7天内需跟进 {m['follow_up_due_7d']}")
    print(f"\n变冷 Top{len(result['cooling_sample'])}:")
    for c in result["cooling_sample"]:
        flag = " 超期" if c["over_target"] else ""
        print(f"  - {c['name']} ({c['group']}) 距上次 {c['days_since']} 天 / "
              f"目标 {c['target_frequency_days']} 天{flag}")
    print(f"\n建议主动联系 {len(result['follow_up_sample'])}:")
    for c in result["follow_up_sample"]:
        print(f"  - {c['name']} ({c['group']}) 跟进 {c['follow_up_date']} [{c['urgency']}] {c['follow_up_note']}")


if __name__ == "__main__":
    main()
