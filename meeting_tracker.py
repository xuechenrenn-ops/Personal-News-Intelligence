import json
from datetime import date, datetime
from pathlib import Path


def build_meeting_brief(calendar_path="meeting_calendar.json", today=None):
    """Render verified upcoming meetings and reminders from the curated calendar."""
    today = today or date.today()
    path = Path(calendar_path)
    if not path.exists():
        return "## 学术会议动态\n会议日历文件不存在，暂不生成会议提醒。"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return f"## 学术会议动态\n会议日历读取失败：{type(exc).__name__}。"

    reminder_days = int(data.get("reminder_days", 30))
    urgent_days = int(data.get("urgent_days", 7))
    upcoming = []
    for event in data.get("events", []):
        try:
            start = date.fromisoformat(event["start_date"])
            end = date.fromisoformat(event.get("end_date", event["start_date"]))
        except (KeyError, TypeError, ValueError):
            continue
        days = (start - today).days
        if end < today or days > reminder_days:
            continue
        upcoming.append((days, event))

    lines = ["## 学术会议动态"]
    if not upcoming:
        lines.append(f"未来{reminder_days}天暂无已核实日期的重要会议。")
    else:
        for days, event in sorted(upcoming, key=lambda item: item[0]):
            if days < 0:
                status = "会议进行中"
            elif days <= urgent_days:
                status = f"重要提醒：距开幕{days}天"
            else:
                status = f"未来会议：距开幕{days}天"
            lines.append(
                f"- **{event.get('name', '未命名会议')}**（{event.get('scope', '学术会议')}）\n"
                f"  - 时间：{event.get('start_date')} 至 {event.get('end_date', event.get('start_date'))}\n"
                f"  - 地点：{event.get('location', '官网未提供')}\n"
                f"  - 状态：{status}\n"
                f"  - 来源：{event.get('source', '组织方官网')}｜{event.get('url', '')}"
            )
    lines.append("会议日期仅在组织方来源明确公布后录入；会议摘要、报告不等同于同行评议论文。")
    return "\n".join(lines)
