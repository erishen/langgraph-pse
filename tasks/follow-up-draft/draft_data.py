"""personal-crm 跟进消息草拟数据聚合（确定性，只读）。

为「跟进消息草拟」任务准备候选人与真实上下文：
- 候选人 = 有 follow_up_date 且逾期 / 7 天内需跟进的联系人（与 weekly-review 口径一致）
- 每个候选附：最近互动日、最近若干条真实聊天（含方向/本地日期/内容）、follow_up_note
LLM 只需基于这些**真实上下文**草拟个性化跟进文案，不得编造共同经历/事件/日期。

可由 run.py 直接打印（零成本），也可作为 task_data["draft_data"] 注入 LLM 模式供 verify 对照。

设计原则：
- 只读打开（mode=ro&immutable），绝不修改 crm.db
- 所有字段均来自 contacts / contact_records / chat_messages 真实数据，零编造
- 真实库路径只通过环境变量 CRM_DB_PATH 或 --db 指定，绝不硬编码个人绝对路径
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
_RECENT_CHAT_N = 10  # 每个候选人最多附带的最近聊天条数


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
    """清洗会破坏 Markdown 表格/文本的字符：竖线改全角，去除换行。"""
    return (str(s) if s is not None else "").replace("|", "｜").replace("\n", " ").strip()


def _dir_label(direction: str | None) -> str:
    if direction == "sent":
        return "我→对方"
    if direction == "received":
        return "对方→我"
    return "未知"


def gather(db_path: str = DEFAULT_DB) -> dict:
    if not os.path.exists(db_path):
        raise FileNotFoundError(
            f"找不到 CRM 数据库: {db_path}\n"
            f"请通过环境变量 CRM_DB_PATH 指定，或在运行时加 --db <绝对路径>；\n"
            f"例如在 .env 中设置 CRM_DB_PATH=/path/to/crm.db。"
        )
    con = _open(db_path)
    cur = con.cursor()
    today = datetime.now(_SHANGHAI).date()
    d7_future = today + timedelta(days=7)

    # 每联系人元信息
    meta = {r["id"]: r for r in cur.execute(
        'SELECT id, name, "group" AS grp FROM contacts'
    ).fetchall()}
    name_of = {cid: (m["name"] or f"联系人#{cid}") for cid, m in meta.items()}
    group_of = {cid: (m["grp"] or "其他") for cid, m in meta.items()}

    # 最近互动日期（contact_records / chat 取最大）
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

    # 最近聊天（按 sent_at 倒序取前 N，再反转成时间正序）
    recent_raw = {}
    for cid, sent_raw, direction, content in cur.execute(
        "SELECT contact_id, sent_at, direction, content FROM chat_messages "
        "WHERE contact_id IS NOT NULL AND sent_at IS NOT NULL "
        "ORDER BY contact_id, sent_at DESC"
    ).fetchall():
        cid = int(cid)
        recent_raw.setdefault(cid, [])
        if len(recent_raw[cid]) >= _RECENT_CHAT_N:
            continue
        try:
            sd = _local_date(datetime.fromisoformat(sent_raw))
        except (ValueError, TypeError):
            continue
        recent_raw[cid].append({
            "dir": _dir_label(direction),
            "date": sd.isoformat(),
            "content": _clean(content),
        })
    recent_chat = {
        cid: list(reversed(rows)) for cid, rows in recent_raw.items()
    }

    # 候选人：有 follow_up_date 且逾期 / 7 天内
    fu_rows = cur.execute(
        "SELECT contact_id, follow_up_date, follow_up_note "
        "FROM contact_records WHERE follow_up_date IS NOT NULL"
    ).fetchall()
    candidates = []
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
            continue  # 超过 7 天不纳入本周草拟
        cid = int(cid)
        li = last_interaction(cid)
        candidates.append({
            "id": cid,
            "name": _clean(name_of.get(cid, f"联系人#{cid}")),
            "group": _clean(group_of.get(cid, "其他")),
            "follow_up_date": fud,
            "follow_up_note": _clean(note or ""),
            "urgency": urgency,
            "last_interaction": li.isoformat() if li else None,
            "recent_chat": recent_chat.get(cid, []),
        })
    # 高紧急在前
    candidates.sort(key=lambda x: x["urgency"], reverse=True)

    con.close()

    metrics = {
        "candidates": len(candidates),
        "follow_up_overdue": sum(1 for c in candidates if c["urgency"].startswith("高")),
        "follow_up_due_7d": sum(1 for c in candidates if c["urgency"].startswith("中")),
    }
    return {
        "db_path": db_path,
        "generated_at": today.isoformat(),
        "metrics": metrics,
        "candidates": candidates,
    }


def main():
    ap = argparse.ArgumentParser(description="personal-crm 跟进消息草拟数据聚合")
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
    print(f"待草拟候选人 {m['candidates']} | 逾期 {m['follow_up_overdue']} | 7天内 {m['follow_up_due_7d']}")
    print()
    for c in result["candidates"]:
        print(f"### {c['name']} ({c['group']}) [{c['urgency']}]")
        print(f"    跟进日期: {c['follow_up_date']}  最近互动: {c['last_interaction']}")
        if c["follow_up_note"]:
            print(f"    跟进备注: {c['follow_up_note']}")
        print(f"    最近聊天 ({len(c['recent_chat'])} 条):")
        for ch in c["recent_chat"]:
            print(f"      [{ch['dir']} {ch['date']}] {ch['content']}")
        print()


if __name__ == "__main__":
    main()
