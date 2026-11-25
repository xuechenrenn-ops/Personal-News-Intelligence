import json
import os
import sys
from datetime import datetime, timedelta, timezone

import feedparser
import requests

BAICHUAN_URL = "https://api.baichuan-ai.com/v1/chat/completions"
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
PUSHPLUS_URL = "https://www.pushplus.plus/send"
MEDICAL_FEEDS = ["https://medicalxpress.com/rss-feed/"]
GENERAL_FEEDS = ["https://rsshub.app/zaobao/realtime/world"]
TIMEOUT = 90

MEDICAL_PROMPT = """你是严谨的医学资讯编辑。从候选新闻中筛选2-3条高价值内容，重点关注骨关节外科、运动医学及医疗AI。优先原始研究、正式指南、专业学会和权威期刊；排除营销、重复转载和无可靠依据的内容。研究类尽可能说明研究设计、样本量、对象、主要结局、关键结果、临床意义和局限。材料未提供就写“原文未提供”，绝不猜测。区分相关与因果、统计学与临床意义；不得把单项研究说成指南。AI新闻说明真实临床验证状态及应用限制。只能选择输入中存在的候选项，逐条返回原始id；不得创造来源、日期、链接、DOI、PMID或研究数据。只输出合法JSON，格式：{"items":[{"id":"原始id","category":"骨关节外科与运动医学或医疗AI","headline":"中文标题","summary":"准确概述","evidence":"证据详情","clinical_significance":"临床意义","limitations":"局限"}]}"""

GENERAL_PROMPT = """你是客观、严谨的中文新闻编辑。从候选新闻中筛选3-4条重要内容，覆盖国内外重大事件、民生与医疗健康、重要科技与经济资讯。优先过去24小时新闻，重大事件最多放宽至48小时；去重、排除营销和未经核实传闻，宁缺毋滥。每条说明发生了什么、必要背景、对普通人或社会的实际影响。明确区分已确认事实、媒体分析和预测，不夸大影响；材料不足时说明无法确认。只能选择输入中存在的候选项，逐条返回原始id；不得创造标题、来源、日期或链接。只输出合法JSON，格式：{"items":[{"id":"原始id","category":"国内外重大事件或民生与医疗健康或科技与经济","headline":"中文标题","summary":"准确简述","background":"必要背景","public_impact":"现实影响","verification_note":"事实边界"}]}"""

MORNING_LINES = [
"把今天走稳，明天自然会有新的方向。","不必急着抵达，持续向前本身就有意义。",
"认真生活的人，终会积累自己的光亮。","允许自己慢一点，但别轻易停下脚步。",
"把复杂的事拆小，一步一步就能做好。","每一次踏实的努力，都在悄悄改变未来。",
"先做好眼前的事，答案会在行动中出现。","生活不必时时精彩，平静而坚定也很好。",
"真正的进步，常常藏在重复的日常里。","保持好奇，也保持耐心，路会越走越宽。",
"今天多一点认真，明天就少一点遗憾。","不和别人比较速度，只确认自己仍在前进。",
"愿你有解决问题的勇气，也有接纳自己的温柔。","把注意力放回当下，很多事情会清晰起来。",
"微小的改变持续发生，也能抵达很远的地方。","不确定时先行动，行动会带来新的线索。",
"心中有方向，脚下有分寸，日子便有力量。","不必一次做好所有事，先完成最重要的一件。",
"认真对待平凡的一天，就是在认真对待人生。","愿今天的你，既有向前的劲头，也有从容的心境。",
"真正可靠的成长，来自长期而清醒的坚持。","遇到难题先别慌，理清一步，就向前一步。",
"生活的答案不总在远方，也在眼前的细节里。","给自己一点时间，好事情值得耐心。",
"不追求每一步都完美，只希望每一步都真实。","能专注做好当下，就是一种难得的能力。",
"把今天过扎实，就是给未来最好的准备。","愿你在忙碌中不失方向，在变化中保有定力。",
"困难提醒我们调整方法，而不是否定自己。","每个普通的今天，都可能成为改变的起点。",
"少一些内耗，多一些行动，事情会慢慢明朗。","保持善意和判断力，也保留重新开始的勇气。",
"不必害怕进展缓慢，只要方向没有偏离。","真正的底气，来自一次次把事情做好。",
"把力气用在能改变的地方，其他的交给时间。","愿你今天有所收获，也能坦然接受尚未完成。",
"持续学习不是为了追赶谁，而是拓宽自己的可能。","先照顾好节奏，才能走好更长的路。",
"不因一时的停顿，就低估长期坚持的价值。","愿每一份认真都有回响，每一步前行都有意义。"
]

def fetch_candidates(urls, prefix, age_hours):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    items, seen = [], set()
    for url in urls:
        print("读取RSS：" + url, flush=True)
        feed = feedparser.parse(url)
        if feed.bozo and not feed.entries:
            print("警告：RSS读取失败：" + url, flush=True)
            continue
        for entry in feed.entries[:12]:
            title = (entry.get("title") or "").strip()
            link = (entry.get("link") or "").strip()
            if not title or not link or link in seen:
                continue
            seen.add(link)
            parsed = entry.get("published_parsed") or entry.get("updated_parsed")
            published = datetime(*parsed[:6], tzinfo=timezone.utc) if parsed else None
            if published and published < cutoff:
                continue
            src = entry.get("source") or {}
            summary = " ".join(str(entry.get("summary") or entry.get("description") or "").split())[:2500]
            items.append({"id": f"{prefix}{len(items)+1}", "title": title[:400],
                "source": src.get("title", "") if isinstance(src, dict) else "",
                "published_at": published.isoformat() if published else "原始RSS未提供",
                "url": link, "summary": summary})
    return items

def call_model(url, key, model, prompt, candidates, temperature):
    if not candidates:
        print(model + "：无候选新闻，跳过调用。", flush=True)
        return []
    user_content = "严格遵守指令，只输出合法JSON。\n" + prompt + "\n候选新闻JSON：\n" + json.dumps(candidates, ensure_ascii=False)
    if url == BAICHUAN_URL:
        # Baichuan Chat Completions accepts only user/assistant roles; no system role.
        messages = [{"role": "user", "content": user_content}]
    else:
        messages = [
            {"role": "system", "content": "严格遵守指令，只输出合法JSON。"},
            {"role": "user", "content": user_content}
        ]
    payload = {"model": model, "messages": messages, "temperature": temperature}
    response = requests.post(url, headers={"Authorization": "Bearer " + key,
        "Content-Type": "application/json"}, json=payload, timeout=TIMEOUT)
    try:
        response.raise_for_status()
    except requests.HTTPError:
        # Print a short provider error message for diagnosis; never print request headers or API keys.
        detail = " ".join(response.text.split())[:600]
        raise RuntimeError(f"{model} API HTTP {response.status_code}: {detail}") from None
    raw = response.json()["choices"][0]["message"]["content"].strip()
    if raw.startswith(chr(96) * 3):
        raw = raw.split("\n", 1)[-1]
        if raw.endswith(chr(96) * 3):
            raw = raw[:-3]
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError(model + "返回结构不符合约定")
    source_by_id = {x["id"]: x for x in candidates}
    result, used = [], set()
    for item in data["items"]:
        if isinstance(item, dict) and item.get("id") in source_by_id and item["id"] not in used:
            item["_source"] = source_by_id[item["id"]]
            result.append(item)
            used.add(item["id"])
    return result

def val(item, key, fallback="原文未提供"):
    x = item.get(key)
    return str(x).strip() if x is not None and str(x).strip() else fallback

def render(title, items, medical=False):
    lines = ["## " + title]
    if not items:
        return "\n".join(lines + ["今日暂无通过筛选的可靠资讯。"])
    for i, item in enumerate(items, 1):
        src = item["_source"]
        lines += [f"### {i}. {val(item, 'headline', src['title'])}",
            f"- **来源与时间：** {val(src, 'source', 'RSS来源未注明')}｜{src['published_at']}",
            f"- **内容：** {val(item, 'summary')}"]
        if medical:
            lines += [f"- **证据信息：** {val(item, 'evidence')}",
                f"- **临床意义：** {val(item, 'clinical_significance')}",
                f"- **局限性：** {val(item, 'limitations')}"]
        else:
            lines += [f"- **背景：** {val(item, 'background')}",
                f"- **现实影响：** {val(item, 'public_impact')}"]
            if item.get("verification_note"):
                lines.append(f"- **核实说明：** {val(item, 'verification_note')}")
        lines.append(f"- **原文：** {src['url']}")
    return "\n".join(lines)

def main():
    required = ["PUSHPLUS_TOKEN", "BAICHUAN_API_KEY", "DEEPSEEK_API_KEY"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        print("缺少必要环境变量：" + ", ".join(missing), flush=True)
        return 1
    print("开始采集新闻……", flush=True)
    medical = fetch_candidates(MEDICAL_FEEDS, "M", 24 * 90)
    general = fetch_candidates(GENERAL_FEEDS, "G", 48)
    print(f"候选数量：医学{len(medical)}条，综合{len(general)}条", flush=True)
    try:
        med_items = call_model(BAICHUAN_URL, os.environ["BAICHUAN_API_KEY"],
            "Baichuan-M3-Plus", MEDICAL_PROMPT, medical, 0.2)
        print(f"百川筛选完成：{len(med_items)}条", flush=True)
    except Exception as exc:
        print(f"百川调用或解析失败：{type(exc).__name__}: {exc}", flush=True)
        return 1
    try:
        news_items = call_model(DEEPSEEK_URL, os.environ["DEEPSEEK_API_KEY"],
            "deepseek-flash", GENERAL_PROMPT, general, 0.4)
        print(f"DeepSeek筛选完成：{len(news_items)}条", flush=True)
    except Exception as exc:
        print(f"DeepSeek调用或解析失败：{type(exc).__name__}: {exc}", flush=True)
        return 1
    local_now = datetime.now(timezone(timedelta(hours=8)))
    today = local_now.strftime("%Y-%m-%d")
    morning = MORNING_LINES[local_now.date().toordinal() % len(MORNING_LINES)]
    content = "\n\n".join([
        f"# Personal News Intelligence\n**{today}｜每日专属资讯**",
        render("🦴 骨关节外科、运动医学与医疗AI", med_items, True),
        render("🌍 国内外重大事件、民生与科技经济", news_items),
        "## 🌅 今日晨语\n" + morning,
        "---\n*资讯由自动采集与AI辅助整理；请以原始来源为准。*"])
    print("简报预览：\n" + content, flush=True)
    if os.environ.get("PUSH_ENABLED", "false").lower() != "true":
        print("安全模式：仅生成预览，不发送微信。", flush=True)
        return 0
    print("正在发送PushPlus……", flush=True)
    response = requests.post(PUSHPLUS_URL, json={"token": os.environ["PUSHPLUS_TOKEN"],
        "title": f"📬 每日新闻情报 | {today}", "content": content,
        "template": "markdown"}, timeout=30)
    response.raise_for_status()
    result = response.json()
    if str(result.get("code")) != "200":
        raise RuntimeError("PushPlus返回非成功状态：" + json.dumps(result, ensure_ascii=False))
    print("PushPlus已确认接受推送请求。", flush=True)
    return 0

if __name__ == "__main__":
    sys.exit(main())
