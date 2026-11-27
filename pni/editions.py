"""Local edition settings. The deployed schedule is configured separately."""
import re

EDITIONS = {
    "morning": {"title": "晨间科研情报", "cron": "30 22 * * *", "reservation": 200_000,
                "tools": 3, "tavily_credits": 4},
    "evening": {"title": "晚间实时新闻", "cron": "30 11 * * *", "reservation": 120_000,
                "tools": 5, "tavily_credits": 4},
}

TECH = re.compile(r"半导体|芯片|人工智能|大模型|算力|先进制造|商业航天|科技|新能源|产业|通信网|工业互联网|"
                  r"卫星互联网|万兆光网|6G|5G-A|量子|脑机接口|具身智能|人形机器人|光刻|晶圆|"
                  r"智能网联|自动驾驶|低空经济|信息无障碍|"
                  r"semiconductor|\bchips?\b|artificial intelligence|\bAI\b|quantum|technology|"
                  r"renewable energ|industrial|space technolog", re.I)


def news_topic(card):
    return "technology" if TECH.search(card["title"]) else "major"


def edition_for_cron(cron):
    for edition, config in EDITIONS.items():
        if config["cron"] == cron:
            return edition
    raise ValueError("Unrecognized PNI schedule")
