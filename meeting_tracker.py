import json
from datetime import date, datetime
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
import os
import requests

class _MeetingLinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.href = None
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            self.href = dict(attrs).get("href", "")
            self.parts = []

    def handle_data(self, data):
        if self.href is not None:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self.href is not None:
            title = " ".join(" ".join(self.parts).split())
            self.links.append((self.href, title))
            self.href = None
            self.parts = []


def check_meeting_watch_sources(calendar_path="meeting_calendar.json",
                                state_path=".cache/meeting_watch_state.json"):
    """Monitor official meeting pages for newly linked meeting/abstract notices."""
    try:
        data = json.loads(Path(calendar_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ["会议来源监测：无法读取会议来源配置。"]

    try:
        previous = json.loads(Path(state_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        previous = {}

    current = {}
    updates = []
    keywords = ("会议", "年会", "大会", "征文", "骨科学术", "运动医疗",
                "conference", "annual meeting", "congress", "call for abstracts",
                "scientific programme", "scientific program")
    for source in data.get("watch_sources", []):
        name, url = source.get("name", "会议来源"), source.get("url", "")
        try:
            response = requests.get(
                url, headers={"User-Agent": "PersonalNewsIntelligence/1.0"},
                timeout=12
            )
            response.raise_for_status()
            parser = _MeetingLinkParser()
            parser.feed(response.text)
            host = urlparse(url).netloc
            found = []
            for href, title in parser.links:
                if not title or not any(word.lower() in title.lower() for word in keywords):
                    continue
                full_url = urljoin(url, href)
                parsed = urlparse(full_url)
                if parsed.scheme not in ("http", "https") or not parsed.netloc:
                    continue
                if full_url not in found:
                    found.append(full_url)
                if len(found) >= 20:
                    break
            current[name] = found
            old = set(previous.get(name, []))
            new_links = [link for link in found if link not in old]
            for link in new_links[:3]:
                updates.append(f"- **新会议公告线索｜{name}：** {link}")
            if not new_links:
                updates.append(f"- {name}：页面可访问，未发现新的会议/征文链接（HTTP {response.status_code}）")
        except Exception as exc:
            current[name] = previous.get(name, [])
            updates.append(f"- {name}：本次监测失败（{type(exc).__name__}），保留已有日历数据")

    try:
        Path(state_path).parent.mkdir(parents=True, exist_ok=True)
        Path(state_path).write_text(
            json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        updates.append(f"- 会议来源监测状态未能保存：{type(exc).__name__}")
    return updates


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
