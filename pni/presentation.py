"""Render the existing brief Markdown as compact HTML and split only between articles."""
from dataclasses import dataclass
from datetime import date
from html import escape
import re
from urllib.parse import urlsplit

# The published 20,000-字 limit doesn't define units or HTML accounting.
# Full source HTML must fit both bounds, with a 10% local safety margin.
PUSHPLUS_PUBLISHED_LIMIT = 20_000
SAFE_LIMIT = PUSHPLUS_PUBLISHED_LIMIT * 9 // 10
WEEKDAYS = "一二三四五六日"
SAYINGS = (
    "看见差异，也记得追问它是否重要。", "把结论放回证据里，判断就多一分把握。",
    "值得读的消息，会让下一个问题更清楚。", "留一点时间给那些改变判断的发现。",
    "先了解发生了什么，再判断它意味着什么。", "读完一个结论，也读一眼它的适用范围。",
    "判断一条消息的价值，可以从它的出处开始。", "能说清楚的边界，和结论一样重要。",
    "一条扎实的证据，胜过许多未经核对的说法。", "把复杂问题拆开，先看最有把握的一部分。",
    "有些发现值得收藏，有些问题值得继续追踪。", "好的信息，让人更接近下一步行动。",
    "认真阅读，也包括给不确定性留位置。", "每天多核实一个关键细节，就是进展。",
)
LANES = {"professional": "专业情报", "extended": "拓展阅读"}
INTRO = "每天五分钟，读懂值得关注的世界。"
LINK = re.compile(r"\[([^\]\n]+)\]\(([^\s)]+)\)")
FIELD = re.compile(r"^\*\*([^*]+)\*\*\s*(.*)", re.S)



@dataclass(frozen=True)
class Article:
    lane: str
    section: str
    number: int
    title: str
    text: tuple[str, ...]

    @property
    def urls(self):
        return frozenset(url for block in self.text for _, url in LINK.findall(block) if valid_link(url))


@dataclass(frozen=True)
class Message:
    title: str
    html: str
    articles: tuple[Article, ...]

def valid_link(url):
    try:
        value = urlsplit(url)
        return value.scheme == "https" and bool(value.hostname) and not (value.username or value.password)
    except ValueError:
        return False


def inline(text):
    parts = []
    cursor = 0
    for match in LINK.finditer(text):
        label, url = match.groups()
        parts.append(escape(text[cursor:match.start()]))
        parts.append(f'<a href="{escape(url)}" style="color:#235994">{escape(label)}</a>'
                     if valid_link(url) else escape(label))
        cursor = match.end()
    parts.append(escape(text[cursor:]))
    return "".join(parts)


def parse_brief(markdown, lane, start=0):
    result = []
    section = LANES[lane]
    title = None
    body = []

    def flush():
        if title:
            result.append(Article(lane, section, start + len(result) + 1, title, tuple(body)))

    for line in markdown.splitlines():
        if line.startswith(("## ", "### ")):
            flush()
            title = None
            body = []
            if line.startswith("### "):
                title = line[4:].strip()
            elif line[3:].strip() not in {"国内医疗民生", "国内外重大事件", "国自然动态", "会议提醒"}:
                section = "日程与申报"
                title = line[3:].strip()
            else:
                section = line[3:].strip()
                if section in {"国自然动态", "会议提醒"}:
                    title = section
        elif title and line.strip():
            body.append(line.strip())
    flush()
    return result


def focus(article):
    for row in article.text:
        field = FIELD.match(row)
        if field and field[1] == "原文主要结果":
            finding = field[2]
            if finding.startswith("结果段原文短引"):
                return "摘要未提取到可直接概述的主要结局数据，正文保留原文及来源。"
            return re.split(r"(?<=。)(?=[^（])", finding, maxsplit=1)[0]
    for row in article.text:
        field = FIELD.match(row)
        if field and field[1] == "发生了什么":
            return field[2].split("。", 1)[0] + "。"
    for row in article.text:
        if len(row) > 35 and row.endswith(("。", "”")) and not row.startswith(("原始题名", "[")):
            return row.split("。", 1)[0].rstrip("”") + "。"
    return article.title + "。"


def render_article(article):
    content = [
        f'<div style="border-top:1px solid #dbe4ee;padding:18px 0 4px">'
        f'<p style="font-size:12px;color:#365c82;font-weight:bold;margin:0 0 5px">{article.number:02d} / {escape(article.section)}</p>'
        f'<h2 style="font-size:19px;color:#142f50;line-height:1.4;margin:0 0 9px">{inline(article.title)}</h2>'
    ]
    for row in article.text:
        if row.startswith("原始题名"):
            if not re.search(r"[\u4e00-\u9fff]", article.title):
                continue  # English-only heading already carries this title.
            # Chinese heading and permanent source identifier are already present.
            if article.urls:
                continue
        field = FIELD.match(row)
        if field:
            name, value = field.groups()
            label = {"原文主要结果": "关键结果", "临床意义（分析）": "临床意义",
                     "科研启发（待验证）": "科研启发 · 待验证"}.get(name, name)
            background = ' style="background:#edf4fb;border-left:3px solid #587ea7;padding:8px 10px"' if name == "原文主要结果" else ""
            content.append(f'<p{background}><b style="color:#234368">{escape(label)}</b>　{inline(value)}</p>')
        elif row.startswith("[") or " ｜ " in row or row.startswith("证据范围"):
            content.append(f'<p style="font-size:13px;color:#526478">{inline(row)}</p>')
        else:
            content.append(f'<p>{inline(row)}</p>')
    return "".join(content) + "</div>"


def render(articles, day, *, context=""):
    weekday = WEEKDAYS[day.weekday()]
    heading = (
        '<div style="max-width:640px;margin:auto;padding:12px 14px;color:#243449;font-size:15px;line-height:1.65;overflow-wrap:anywhere">'
        '<div style="background:#153451;color:#ffffff;border-radius:9px;padding:18px 15px">'
        '<p style="font-size:12px;letter-spacing:1.1px;margin:0;color:#e1edf9">PERSONAL NEWS INTELLIGENCE</p>'
        f'<p style="font-size:20px;font-weight:bold;margin:6px 0">{INTRO}</p>'
        f'<p style="font-size:13px;margin:0;color:#d7e6f5">{day:%Y年%m月%d日} · 星期{weekday}{escape(context)}</p></div>'
        f'<p style="color:#36516b;padding:5px 0"><b>每日寄语</b>　{SAYINGS[day.toordinal() % len(SAYINGS)]}</p>'
        '<div style="background:#f1f5fa;border-radius:7px;padding:9px 12px;margin:0 0 12px">'
        '<h2 style="font-size:16px;color:#173a61;margin:0 0 8px">30秒今日导读</h2>'
    )
    for article in articles:
        heading += f'<p style="margin:6px 0"><b style="color:#254975">{article.number:02d} {escape(article.section)}</b>　{inline(focus(article))}</p>'
    if not articles:
        heading += "<p>暂无达到来源和质量条件的资讯。</p>"
    return heading + "</div>" + "".join(render_article(x) for x in articles) + "</div>"


def measure(markup):
    return {"characters": len(markup), "utf8_bytes": len(markup.encode("utf-8")),
            "utf16_units": len(markup.encode("utf-16-le")) // 2}


def plan(professional, extended, day, *, limit=SAFE_LIMIT):
    first = parse_brief(professional, "professional")
    second = parse_brief(extended, "extended", len(first))
    all_articles = tuple(first + second)

    def fits(markup):
        sizes = measure(markup)
        return max(sizes["utf8_bytes"], sizes["utf16_units"]) <= limit

    def valid_message(articles, title):
        html = render(articles, day, context=" · " + title)
        return Message(f"{title}｜{day.isoformat()}", html, tuple(articles))

    combined = valid_message(all_articles, "每日简报")
    if fits(combined.html):
        return [combined]
    messages = []
    for lane, group in (("professional", first), ("extended", second)):
        if not group:
            continue
        chunks = [[*group]]
        if not fits(valid_message(group, LANES[lane]).html):
            chunks = [[]]
            title_bound = LANES[lane] + f" {len(group)}/{len(group)}"
            for article in group:
                if not fits(valid_message([article], title_bound).html):
                    raise ValueError(f"文章 {article.number} 无法在单条安全预算内完整发送")
                candidate = [*chunks[-1], article]
                if not fits(valid_message(candidate, title_bound).html):
                    chunks.append([article])
                else:
                    chunks[-1] = candidate
        for index, chunk in enumerate(chunks, 1):
            suffix = f" {index}/{len(chunks)}" if len(chunks) > 1 else ""
            messages.append(valid_message(chunk, LANES[lane] + suffix))
    return messages


def document(markup):
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Personal News Intelligence</title><body style="margin:0;background:#fff;'
            'font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif">' + markup + "</body></html>")
