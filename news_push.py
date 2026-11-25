import os
import feedparser
import requests

token = os.environ["PUSHPLUS_TOKEN"]

# 先只接一个稳定、公开的 RSS 源做验证
RSS_URL = "http://feeds.bbci.co.uk/news/health/rss.xml"
feed = feedparser.parse(RSS_URL)

items = feed.entries[:5]

lines = ["今日健康新闻（云端自动抓取）", ""]
for i, entry in enumerate(items, 1):
    title = entry.get("title", "无标题")
    link = entry.get("link", "")
    lines.append(f"{i}. {title}")
    if link:
        lines.append(f"   {link}")
    lines.append("")

content = "\n".join(lines)

resp = requests.post(
    "https://www.pushplus.plus/send",
    json={
        "token": token,
        "title": "每日健康新闻（测试版）",
        "content": content,
        "template": "txt",
    },
    timeout=15,
)
print("HTTP 状态:", resp.status_code)
print("响应:", resp.text)
resp.raise_for_status()
