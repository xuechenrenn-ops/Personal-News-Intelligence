import json
import os
import re
import sys
import time
import difflib
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import feedparser
import requests
from html import unescape

BAICHUAN_URL = "https://api.baichuan-ai.com/v1/chat/completions"
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
PUSHPLUS_URL = "https://www.pushplus.plus/send"
MEDICAL_FEEDS = [
    ("MedicalXpress-骨科", "https://medicalxpress.com/rss-feed/orthopedics-news/"),
    ("MedicalXpress-运动医学", "https://medicalxpress.com/rss-feed/sports-medicine-news/"),
    ("MedicalXpress-综合医学（用于补充医疗AI线索）", "https://medicalxpress.com/rss-feed/"),
]
GENERAL_FEEDS = [
    # 中国国内：优先覆盖即时、社会民生、财经和生活资讯
    ("中国新闻网-即时", "https://www.chinanews.com.cn/rss/scroll-news.xml"),
    ("中国新闻网-社会", "https://www.chinanews.com.cn/rss/society.xml"),
    ("中国新闻网-财经", "https://www.chinanews.com.cn/rss/finance.xml"),
    ("中国新闻网-生活", "https://www.chinanews.com.cn/rss/life.xml"),
    # 中国视角的国际与产业补充
    ("中国新闻网-国际", "https://www.chinanews.com.cn/rss/world.xml"),
    ("新华网-国际", "http://www.xinhuanet.com/world/news_world.xml"),
    ("新华网-财经", "http://www.xinhuanet.com/fortune/news_fortune.xml"),
    ("新华网-科技", "http://www.xinhuanet.com/tech/news_tech.xml"),
    # 国际媒体：避免只依赖单一国家或单一媒体
    ("BBC-World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
]
TIMEOUT = 90

MEDICAL_PROMPT = """你是严谨的医学资讯编辑。从候选新闻中筛选最多3条高价值内容，主题必须直接属于骨关节外科、运动医学或医疗AI。骨科与运动医学优先，医疗AI其次。严格排除风湿免疫、皮肤科、肿瘤、普通内科等与上述主题无直接关系的新闻；不得为了凑数选择其他医学领域内容，符合条件的不足3条就少选。优先原始研究、正式指南、专业学会和权威期刊；排除营销、重复转载和无可靠依据的内容。研究类尽可能说明研究设计、样本量、对象、主要结局、关键结果、临床意义和局限。材料未提供就写“原文未提供”，绝不猜测。区分相关与因果、统计学与临床意义；不得把单项研究说成指南。AI新闻说明真实临床验证状态及应用限制。只能选择输入中存在的候选项，逐条返回原始id；不得创造来源、日期、链接、DOI、PMID或研究数据。只输出合法JSON，格式：{"items":[{"id":"原始id","category":"骨关节外科与运动医学或医疗AI","headline":"中文标题","summary":"准确概述","evidence":"证据详情","clinical_significance":"临床意义","limitations":"局限"}]}"""

MEDICAL_EVIDENCE_PROMPT = MEDICAL_PROMPT + """

第二阶段：证据复核。候选项包含 article_evidence，其中 article_excerpt 来自新闻原文页面，pubmed_record（若存在）来自 PubMed。initial_draft 只是未核实的初筛草稿，不能作为证据。请优先依据 PubMed 原始摘要和原文页面核实研究信息。必须区分随机对照试验、队列/病例对照/横断面研究、病例系列、动物/体外实验、综述或新闻报道。只有来源明确提供时才能报告样本量、干预组、对照组、结局、效应量、置信区间和P值。不要根据“大型试验”等新闻措辞推断样本量或证据等级。若没有匹配到 PubMed 记录，不得声称已核实论文；若只能访问新闻摘要，明确写“仅依据新闻原文，未取得论文摘要/全文”。动物实验不得推导为已证实的人体治疗效果。临床意义必须与研究设计和结果相称。可以修正初筛草稿中的错误；无法核实的信息写“原文未提供”或“无法核实”。保持原始 id，不得创造 PMID、DOI、链接或数据。
"""

GENERAL_PROMPT = """你是客观、严谨的中文新闻编辑。从候选新闻中筛选3-4条重要内容，兼顾中国国内与国际视角。中国国内新闻是重要组成部分，覆盖政策及其实际影响、社会民生、经济、科技和医疗健康；不必大量选择纯会议、领导活动或礼仪性报道，除非具有明确重大政策或现实影响。国际新闻关注重大事件、地缘政治、全球经济、科技与公共卫生。尽量兼顾不同媒体和来源，不因媒体立场预设结论。优先过去24小时新闻，重大事件最多放宽至48小时；去重、排除营销和未经核实传闻，宁缺毋滥。目标上适当兼顾国内与国际，不设僵硬配额，不为满足比例而凑数。每条说明发生了什么、必要背景、对普通人或社会的实际影响。明确区分已确认事实、媒体分析和预测，不夸大影响；材料不足时说明无法确认。只能选择输入中存在的候选项，逐条返回原始id；不得创造标题、来源、日期或链接。只输出合法JSON，格式：{"items":[{"id":"原始id","category":"中国国内或国际或科技经济或民生健康","headline":"中文标题","summary":"准确简述","background":"必要背景","public_impact":"现实影响","verification_note":"事实边界"}]}"""

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

def fetch_candidates(feeds, prefix, age_hours):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    items, seen = [], set()
    headers = {"User-Agent": "PersonalNewsIntelligence/1.0"}
    for feed_label, url in feeds:
        print("读取RSS：" + feed_label + "｜" + url, flush=True)
        try:
            response = requests.get(url, headers=headers, timeout=20)
            response.raise_for_status()
            feed = feedparser.parse(response.content)
        except Exception as exc:
            print(f"警告：RSS读取失败：{feed_label}｜{type(exc).__name__}: {exc}", flush=True)
            continue
        if feed.bozo:
            print(f"提示：RSS格式存在解析警告：{feed_label}", flush=True)
        if not feed.entries:
            print(f"提示：RSS没有返回新闻条目：{feed_label}｜HTTP {response.status_code}", flush=True)
            continue
        source_fallback = str(feed.feed.get("title") or feed_label).strip()
        accepted = 0
        for entry in feed.entries[:6]:
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
            source_name = src.get("title", "") if isinstance(src, dict) else ""
            summary = " ".join(str(entry.get("summary") or entry.get("description") or "").split())[:900]
            items.append({"id": f"{prefix}{len(items)+1}", "title": title[:400],
                "source": source_name or source_fallback,
                "published_at": published.isoformat() if published else "原始RSS未提供",
                "url": link, "summary": summary})
            accepted += 1
        print(f"RSS采集结果：{feed_label}｜有效候选{accepted}条", flush=True)
    return items


def _strip_html(fragment):
    fragment = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", fragment)
    fragment = re.sub(r"(?s)<[^>]+>", " ", fragment)
    return " ".join(unescape(fragment).split())


def _meta_value(html, wanted_names):
    for tag in re.findall(r"(?is)<meta\b[^>]*>", html):
        attrs = {}
        for match in re.finditer(r"""([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", tag):
            attrs[match.group(1).lower()] = next((x for x in match.groups()[1:] if x is not None), "")
        key = (attrs.get("name") or attrs.get("property") or attrs.get("itemprop") or "").lower()
        if key in wanted_names and attrs.get("content"):
            return unescape(attrs["content"]).strip()
    return ""


def _extract_article_excerpt(html):
    html = re.sub(r"(?is)<(script|style|nav|footer|header|aside).*?>.*?</\1>", " ", html)
    paragraphs = re.findall(r"(?is)<p\b[^>]*>(.*?)</p>", html)
    cleaned = [_strip_html(p) for p in paragraphs]
    cleaned = [p for p in cleaned if len(p) >= 60]
    excerpt = "\n".join(cleaned[:18])
    if len(excerpt) < 300:
        description = _meta_value(html, {"description", "og:description", "twitter:description"})
        excerpt = (description + "\n" + excerpt).strip()
    return excerpt[:6500]


def _pubmed_records(pmids):
    if not pmids:
        return []
    time.sleep(0.36)
    params = {"db": "pubmed", "id": ",".join(pmids[:3]), "retmode": "xml"}
    response = requests.get(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
        params=params, headers={"User-Agent": "PersonalNewsIntelligence/1.0"},
        timeout=20
    )
    response.raise_for_status()
    root = ET.fromstring(response.content)
    records = []
    for node in root.findall(".//PubmedArticle"):
        pmid = (node.findtext(".//PMID") or "").strip()
        title_node = node.find(".//ArticleTitle")
        title = "".join(title_node.itertext()).strip() if title_node is not None else ""
        abstract_parts = []
        for part in node.findall(".//Abstract/AbstractText"):
            label = part.attrib.get("Label", "")
            value = "".join(part.itertext()).strip()
            if value:
                abstract_parts.append((label + ": " if label else "") + value)
        doi = ""
        for article_id in node.findall(".//PubmedData/ArticleIdList/ArticleId"):
            if article_id.attrib.get("IdType") == "doi":
                doi = (article_id.text or "").strip()
                break
        journal = (node.findtext(".//Journal/Title") or "").strip()
        pubdate = " ".join((x.text or "").strip() for x in node.findall(".//JournalIssue/PubDate/*") if x.text)
        records.append({
            "pmid": pmid, "title": title, "abstract": "\n".join(abstract_parts)[:6500],
            "doi": doi, "journal": journal, "publication_date": pubdate
        })
    return records


def _find_pubmed_record(title, doi, pmid):
    if pmid:
        return (_pubmed_records([pmid]) or [None])[0]
    term = ""
    if doi:
        term = '"' + doi + '"[AID]'
    elif title:
        term = '"' + title.replace('"', "") + '"[Title]'
    if not term:
        return None
    time.sleep(0.36)
    response = requests.get(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
        params={"db": "pubmed", "term": term, "retmode": "json", "retmax": 3},
        headers={"User-Agent": "PersonalNewsIntelligence/1.0"}, timeout=20
    )
    response.raise_for_status()
    ids = response.json().get("esearchresult", {}).get("idlist", [])
    if not ids:
        return None
    records = _pubmed_records(ids)
    if doi:
        doi_norm = doi.lower().replace("https://doi.org/", "").replace("http://doi.org/", "")
        for record in records:
            if record.get("doi", "").lower() == doi_norm:
                return record
        return None
    target = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
    ranked = sorted(
        records,
        key=lambda record: difflib.SequenceMatcher(
            None, target,
            re.sub(r"[^a-z0-9]+", " ", record.get("title", "").lower()).strip()
        ).ratio(),
        reverse=True
    )
    if ranked:
        best = ranked[0]
        score = difflib.SequenceMatcher(
            None, target,
            re.sub(r"[^a-z0-9]+", " ", best.get("title", "").lower()).strip()
        ).ratio()
        if score >= 0.82:
            return best
    return None


def fetch_medical_evidence(candidate):
    result = {
        "article_page_status": "not_fetched",
        "article_excerpt": "",
        "doi": "",
        "pubmed_record": None,
        "verification_status": "not_verified"
    }
    html = ""
    try:
        response = requests.get(
            candidate["url"],
            headers={"User-Agent": "Mozilla/5.0 (compatible; PersonalNewsIntelligence/1.0)"},
            timeout=20
        )
        response.raise_for_status()
        html = response.text
        result["article_page_status"] = "fetched"
        result["article_excerpt"] = _extract_article_excerpt(html)
        result["doi"] = _meta_value(html, {"citation_doi", "dc.identifier"})
        if not result["doi"]:
            doi_match = re.search(r"https?://doi\.org/(10\.\d{4,9}/[-._;()/:A-Z0-9]+)", html, re.I)
            if doi_match:
                result["doi"] = doi_match.group(1).rstrip(".,;)")
    except Exception as exc:
        result["article_page_status"] = "fetch_failed:" + type(exc).__name__
        print(f"医学原文抓取失败：{candidate.get('title', '')[:90]}｜{type(exc).__name__}", flush=True)

    pmid_match = re.search(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", html, re.I)
    pmid = pmid_match.group(1) if pmid_match else ""
    if result["doi"] or pmid:
        try:
            result["pubmed_record"] = _find_pubmed_record(candidate.get("title", ""), result["doi"], pmid)
        except Exception as exc:
            print(f"PubMed查询失败：{candidate.get('title', '')[:90]}｜{type(exc).__name__}", flush=True)
    else:
        try:
            time.sleep(0.36)
            result["pubmed_record"] = _find_pubmed_record(candidate.get("title", ""), "", "")
        except Exception as exc:
            print(f"PubMed标题匹配失败：{candidate.get('title', '')[:90]}｜{type(exc).__name__}", flush=True)

    if result["pubmed_record"]:
        result["verification_status"] = "pubmed_matched"
    elif result["article_excerpt"]:
        result["verification_status"] = "article_only"
    else:
        result["verification_status"] = "no_source_text"
    return result


def enrich_medical_items(screened_items):
    enriched_candidates = []
    for item in screened_items:
        source = dict(item["_source"])
        source["initial_draft"] = {
            key: value for key, value in item.items() if key != "_source"
        }
        source["article_evidence"] = fetch_medical_evidence(source)
        evidence = source["article_evidence"]
        pubmed_state = "PubMed已匹配" if evidence["pubmed_record"] else "未匹配PubMed"
        print(
            f"医学证据补充：{source.get('title', '')[:90]}｜"
            f"原文={evidence['article_page_status']}｜{pubmed_state}｜"
            f"状态={evidence['verification_status']}", flush=True
        )
        enriched_candidates.append(source)
    return enriched_candidates


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
            evidence_check = src.get("article_evidence") or {}
            pubmed = evidence_check.get("pubmed_record") or {}
            if pubmed.get("pmid"):
                lines.append(
                    f"- **论文核查：** 已匹配 PubMed｜PMID: {pubmed['pmid']}｜"
                    f"{pubmed.get('journal') or '期刊未提供'}｜"
                    f"{pubmed.get('publication_date') or '发表日期未提供'}"
                )
            elif evidence_check.get("verification_status") == "article_only":
                lines.append("- **论文核查：** 已获取新闻原文页面，但未匹配到 PubMed 记录；不代表论文不存在")
            elif evidence_check:
                lines.append("- **论文核查：** 未能获取可用原文或 PubMed 摘要，研究细节无法独立核实")
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
        screened_med_items = call_model(BAICHUAN_URL, os.environ["BAICHUAN_API_KEY"],
            "Baichuan-M3-Plus", MEDICAL_PROMPT, medical, 0.2)
        print(f"百川初筛完成：{len(screened_med_items)}条", flush=True)
        if screened_med_items:
            evidence_candidates = enrich_medical_items(screened_med_items)
            med_items = call_model(BAICHUAN_URL, os.environ["BAICHUAN_API_KEY"],
                "Baichuan-M3-Plus", MEDICAL_EVIDENCE_PROMPT, evidence_candidates, 0.1)
            print(f"百川证据复核完成：{len(med_items)}条", flush=True)
        else:
            med_items = []
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
        "---\n*资讯由自动采集与AI辅助整理；请以原始来源为准。PubMed/NCBI 数据通过 NCBI E-utilities 检索；NCBI 不对本简报内容作背书。*"])
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
