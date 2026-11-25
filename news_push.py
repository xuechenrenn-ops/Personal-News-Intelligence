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
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from cache_manager import cleanup_daily_transients, run_weekly_cache_cleanup
from tavily_search import fetch_tavily_candidates

BAICHUAN_URL = "https://api.baichuan-ai.com/v1/chat/completions"
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
PUSHPLUS_URL = "https://www.pushplus.plus/send"
def load_source_registry():
    try:
        with open('sources.json', 'r', encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError('来源配置读取失败：' + type(exc).__name__) from exc

SOURCE_REGISTRY = load_source_registry()
MEDICAL_FEEDS = [(x['label'], x['url']) for x in SOURCE_REGISTRY['rss']['medical']]
GENERAL_FEEDS = [(x['label'], x['url']) for x in SOURCE_REGISTRY['rss']['general']]
OFFICIAL_PAGE_SOURCES = SOURCE_REGISTRY.get('official_pages', [])
TIMEOUT = 90

MEDICAL_PROMPT = """你是严谨的医学研究编辑。最多筛选3篇最值得关注的研究，骨科临床研究与骨科全领域AI研究相结合，不足时宁缺毋滥。候选池可能存在方向分布不均，必须先识别每篇研究所属临床亚专科与AI技术方向；当存在多个质量相近且主题不同的高质量候选时，尽量避免3篇集中于同一亚专科，并优先体现骨科临床、骨科AI及其他医疗AI的代表性。若存在方法学可靠、临床问题明确的非影像AI研究，应与影像AI同等参与竞争；不得为了凑齐AI类别而降低质量，也不得机械规定每天必须有某一专科。临床范围覆盖运动医学、关节镜、韧带、半月板、肩肘、足踝、创伤骨科、骨折、脊柱、关节置换、骨肿瘤、骨质疏松、康复和围手术期管理，不得只关注运动医学。骨科AI不限于医学影像，覆盖临床诊断与决策支持、风险/预后预测、精准医疗、手术机器人/导航、康复、步态/可穿戴设备、临床大模型/Agent/RAG、多模态、因果推断、联邦学习、小样本、可解释AI、数字孪生和真实世界数据。最近7天优先、30天补充，重大突破可放宽至90天，明确区分首次在线、正式出版和数据库收录日期。综合临床问题、研究设计、样本量、方法学、外部/前瞻验证、临床转化、新颖性和局限；区分人体/动物/体外、回顾性/前瞻性、预印本/会议摘要/同行评议论文。排除营销、重复转载及与骨科/临床AI无直接关联的研究。只能选择输入候选，不得创造来源、日期、链接、DOI、PMID或数据。只输出合法JSON：{"items":[{"id":"原始id","category":"骨科临床研究或骨科AI研究或其他医疗AI研究","headline":"中文标题","summary":"准确概述","evidence":"研究设计、对象、样本量和结果；未提供则说明","clinical_significance":"临床意义","limitations":"局限与证据边界","sports_medicine_insight":"对运动医学的潜在启发，未验证的思路迁移须明确标注"}]}"""

MEDICAL_EVIDENCE_PROMPT = MEDICAL_PROMPT + """

第二阶段：证据复核。候选项包含 article_evidence，其中 article_excerpt 来自新闻原文页面，pubmed_record（若存在）来自 PubMed。initial_draft 只是未核实的初筛草稿，不能作为证据。请优先依据 PubMed 原始摘要和原文页面核实研究信息。必须区分随机对照试验、队列/病例对照/横断面研究、病例系列、动物/体外实验、综述或新闻报道。只有来源明确提供时才能报告样本量、干预组、对照组、结局、效应量、置信区间和P值。不要根据“大型试验”等新闻措辞推断样本量或证据等级。若没有匹配到 PubMed 记录，不得声称已核实论文；若只能访问新闻摘要，明确写“仅依据新闻原文，未取得论文摘要/全文”。动物实验不得推导为已证实的人体治疗效果。临床意义必须与研究设计和结果相称。可以修正初筛草稿中的错误；无法核实的信息写“原文未提供”或“无法核实”。保持原始 id，不得创造 PMID、DOI、链接或数据。
"""

NSFC_PROMPT = """你是严谨的国家自然科学基金科研信息编辑。只筛选国家自然科学基金委官方域名下与基金指南、项目申请、指南建议征集、正式资助项目直接相关的信息，优先关注医学科学部H06运动系统、骨科、运动医学、医学AI与交叉研究。候选项已由程序标注nsfc_type，只能按该类型准确归类：正式指南/项目通知、指南建议征集、已资助项目。严格排除人员招聘、岗位选聘、流动项目主任招聘、会议活动及普通机构新闻。指南建议征集不得描述为正式申报指南；项目指南不得描述为已经获批；单条通知不得外推为资助趋势。严格区分官方事实与选题启发，不得推断未公布的申报条件、金额、截止日期或评审倾向。只选输入候选，最多2条，无可靠动态返回空数组。只输出合法JSON：{"items":[{"id":"原始id","category":"正式指南/项目通知或指南建议征集或已资助项目","headline":"中文标题","summary":"准确概述","background":"发布机构、适用范围与背景","public_impact":"对骨科/运动医学AI研究布局的影响","funding_implication":"未来运动医学AI选题启发，区分事实与推测","verification_note":"官方来源、日期及待核实事项"}]}"""

GENERAL_PROMPT = """你是客观、严谨的中文新闻编辑。从候选新闻中筛选最多3条重要内容，兼顾中国国内与国际视角，不足3条时允许少选。中国国内新闻是重要组成部分，优先关注国家卫健委、国家药监局、国家医保局等官方政策与公告，并覆盖政策实际影响、社会民生、经济、科技和医疗健康；涉及FDA与NMPA时，明确区分各自监管状态，不得把FDA授权写成中国获批；不必大量选择纯会议、领导活动或礼仪性报道，除非具有明确重大政策或现实影响。国际新闻关注重大事件、地缘政治、全球经济、科技与公共卫生。尽量兼顾不同媒体和来源，不因媒体立场预设结论。优先过去24小时新闻，重大事件最多放宽至48小时；去重、排除营销和未经核实传闻，宁缺毋滥。涉及医保、卫健、药监政策或药械审批时，优先选择官方来源；若候选仅为第三方媒体解读，不得将其表述为已核实的最新官方政策，必须明确标注“第三方报道，尚未核验官方原文”。目标上适当兼顾国内与国际，不设僵硬配额，不为满足比例而凑数。每条说明发生了什么、必要背景、对普通人或社会的实际影响。明确区分已确认事实、媒体分析和预测，不夸大影响；材料不足时说明无法确认。只能选择输入中存在的候选项，逐条返回原始id；不得创造标题、来源、日期或链接。只输出合法JSON，格式：{"items":[{"id":"原始id","category":"中国国内或国际或科技经济或民生健康","headline":"中文标题","summary":"准确简述","background":"必要背景","public_impact":"现实影响","verification_note":"事实边界"}]}"""

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
            status = getattr(getattr(exc, "response", None), "status_code", None)
            retry_after = getattr(getattr(exc, "response", None), "headers", {}).get("Retry-After")
            print(
                f"警告：RSS读取失败：{feed_label}｜HTTP={status or 'unknown'}｜"
                f"Retry-After={retry_after or 'none'}｜{type(exc).__name__}: {exc}",
                flush=True
            )
            continue
        if feed.bozo:
            print(f"提示：RSS格式存在解析警告：{feed_label}", flush=True)
        if not feed.entries:
            print(f"提示：RSS没有返回新闻条目：{feed_label}｜HTTP {response.status_code}", flush=True)
            continue
        source_fallback = str(feed.feed.get("title") or feed_label).strip()
        accepted = 0
        for entry in feed.entries[:8]:
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
                "url": link, "summary": summary, "source_category": feed_label})
            accepted += 1
        print(f"RSS采集结果：{feed_label}｜有效候选{accepted}条", flush=True)
    return items


class _AnchorCollector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.href = None
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == 'a':
            self.href = dict(attrs).get('href', '')
            self.text = []

    def handle_data(self, data):
        if self.href is not None:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == 'a' and self.href is not None:
            label = ' '.join(' '.join(self.text).split())
            self.links.append((self.href, label))
            self.href = None
            self.text = []


def fetch_official_page_candidates(sources):
    candidates = []
    headers = {'User-Agent': 'PersonalNewsIntelligence/1.0'}
    for source in sources:
        label, url = source.get('label', '官方来源'), source['url']
        if source.get('enabled', True) is False:
            print(f"跳过不可自动访问的官方来源：{label}｜{source.get('access_note', 'disabled')}", flush=True)
            continue
        try:
            response = requests.get(url, headers=headers, timeout=20)
            response.raise_for_status()
            parser = _AnchorCollector()
            parser.feed(response.text)
        except Exception as exc:
            status = getattr(getattr(exc, 'response', None), 'status_code', None)
            print(f'官方页面读取失败：{label}｜HTTP={status or "unknown"}｜{type(exc).__name__}', flush=True)
            continue
        accepted, seen = 0, set()
        host = urlparse(url).netloc
        keywords = source.get('keywords', [])
        for href, title in parser.links:
            if not title or len(title) < 8:
                continue
            full_url = urljoin(url, href)
            parsed = urlparse(full_url)
            if parsed.scheme not in ('http', 'https') or parsed.netloc != host or full_url in seen:
                continue
            if keywords and not any(word.lower() in title.lower() for word in keywords):
                continue
            if any(word in title for word in ('首页', '下一页', '上一页', '更多', '网站地图', '联系我们')):
                continue
            seen.add(full_url)
            date_match = re.search(r'(20\d{2})[-年/.](\d{1,2})[-月/.](\d{1,2})日?', title)
            published = '网页未提供'
            if date_match:
                try:
                    published = datetime(int(date_match.group(1)), int(date_match.group(2)), int(date_match.group(3)), tzinfo=timezone.utc).isoformat()
                except ValueError:
                    pass
            excerpt, detail_status = "", "listing_only"
            try:
                detail_response = requests.get(
                    full_url, headers=headers, timeout=15
                )
                detail_response.raise_for_status()
                excerpt = _extract_article_excerpt(detail_response.text)
                published_meta = _meta_value(
                    detail_response.text,
                    {"article:published_time", "datepublished", "citation_publication_date"}
                )
                if published_meta:
                    date_value = re.search(r'(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})', published_meta)
                    if date_value:
                        try:
                            published = datetime(
                                int(date_value.group(1)), int(date_value.group(2)), int(date_value.group(3)),
                                tzinfo=timezone.utc
                            ).isoformat()
                        except ValueError:
                            pass
                detail_status = "detail_fetched" if excerpt else "detail_no_text"
            except Exception as detail_exc:
                print(f"官方详情页读取失败：{title[:70]}｜{type(detail_exc).__name__}", flush=True)
            candidates.append({
                'id': f'O{len(candidates)+1}', 'title': title[:400],
                'source': label, 'published_at': published, 'url': full_url,
                'summary': excerpt[:1800] if excerpt else '仅取得官方列表标题，未能提取详情正文；不得据此推断政策细节或产品获批范围。',
                'evidence_status': detail_status,
                'category': source.get('category', '中国国内')
            })
            accepted += 1
            if accepted >= int(source.get('max_items', 5)):
                break
        print(f'官方页面采集：{label}｜候选{accepted}条｜HTTP {response.status_code}', flush=True)
    return candidates


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


AI_RESEARCH_TERMS = (
    "artificial intelligence", "machine learning", "deep learning", "large language model",
    "large language models", "llm", "robotics", "wearable", "algorithm", "multimodal",
    "clinical decision support", "人工智能", "机器学习", "深度学习", "大模型", "机器人",
    "可穿戴", "算法", "智能体", "多模态", "预测模型"
)
ORTHOPEDIC_TERMS = (
    "orthopedic", "orthopaedic", "musculoskeletal", "sports medicine", "arthroscopy",
    "meniscus", "ligament", "rotator cuff", "anterior cruciate", "spine", "fracture",
    "arthroplasty", "osteoporosis", "bone tumor", "骨科", "运动医学", "关节镜",
    "半月板", "韧带", "肩袖", "脊柱", "骨折", "关节置换", "骨质疏松", "肌肉骨骼",
    "足踝", "运动损伤", "肌腱"
)


def _medical_candidate_lane(item):
    source_category = str(item.get("source_category") or "")
    text = " ".join(str(item.get(key) or "") for key in ("title", "summary", "source_category")).lower()
    is_ai = any(term in text for term in AI_RESEARCH_TERMS)
    is_ortho = any(term in text for term in ORTHOPEDIC_TERMS) or "骨科" in source_category or "运动医学" in source_category
    if is_ortho and is_ai:
        return "骨科AI"
    if is_ortho:
        return "骨科临床"
    if is_ai:
        return "其他医疗AI"
    return "其他医学研究"


def balance_medical_candidates(candidates, max_per_lane=24, max_total=88):
    """Stable round-robin sampling prevents one source/query lane dominating the model prompt."""
    lanes = {"骨科临床": [], "骨科AI": [], "其他医疗AI": [], "其他医学研究": []}
    seen = set()
    for item in candidates:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        pubmed = item.get("pubmed_record") or {}
        identity = url or str(pubmed.get("pmid") or pubmed.get("doi") or item.get("id") or "")
        if identity and identity in seen:
            continue
        if identity:
            seen.add(identity)
        lanes[_medical_candidate_lane(item)].append(item)
    selected = []
    counts = {key: 0 for key in lanes}
    while len(selected) < max_total and any(lanes.values()):
        progressed = False
        for lane, bucket in lanes.items():
            if bucket and counts[lane] < max_per_lane and len(selected) < max_total:
                selected.append(bucket.pop(0))
                counts[lane] += 1
                progressed = True
        if not progressed:
            break
    print("医学候选平衡采样：" + "｜".join(f"{key}={value}" for key, value in counts.items()), flush=True)
    return selected


NSFC_EXCLUDED_TITLE_TERMS = (
    "招聘", "岗位", "流动项目主任", "人员选聘", "公开选聘", "诚聘", "工作人员",
    "招聘公告", "人才招聘", "聘任"
)
NSFC_SUGGESTION_TERMS = ("指南建议征集", "征集指南建议", "指南建议")
NSFC_FUNDED_TERMS = ("获批项目", "立项名单", "资助项目名单", "已资助项目", "批准资助")


def filter_nsfc_candidates(candidates):
    """Keep only relevant NSFC-domain funding notices; reject recruitment and unrelated pages."""
    accepted = []
    for item in candidates:
        url = str(item.get("url") or "").strip()
        host = urlparse(url).hostname or ""
        if not (host == "nsfc.gov.cn" or host.endswith(".nsfc.gov.cn")):
            continue
        title = str(item.get("title") or "").strip()
        if not title or any(term in title for term in NSFC_EXCLUDED_TITLE_TERMS):
            continue
        if any(term in title for term in NSFC_SUGGESTION_TERMS):
            kind = "指南建议征集"
        elif any(term in title for term in NSFC_FUNDED_TERMS):
            kind = "已资助项目"
        elif any(term in title for term in ("指南", "项目申请", "申报", "联合基金", "重点项目", "重大研究计划", "资助政策")):
            kind = "正式指南/项目通知"
        else:
            continue
        enriched = dict(item)
        enriched["nsfc_type"] = kind
        accepted.append(enriched)
    removed = len(candidates) - len(accepted)
    if removed:
        print(f"NSFC程序级过滤：排除{removed}条非官方域名、招聘或非资助类信息", flush=True)
    return accepted


def annotate_policy_source(items):
    official_domains = ("gov.cn", "nhc.gov.cn", "nhsa.gov.cn", "nmpa.gov.cn")
    policy_terms = ("医保", "卫健", "药监", "医疗政策", "政策解读", "医疗器械", "药品审批", "医保支付")
    for item in items:
        source = item.get("_source", {})
        title = str(item.get("headline") or source.get("title") or "")
        category = str(item.get("category") or "")
        host = (urlparse(str(source.get("url") or "")).hostname or "").lower()
        is_policy = any(term in title + category for term in policy_terms)
        is_official = any(host == domain or host.endswith("." + domain) for domain in official_domains)
        if is_policy and not is_official:
            note = "第三方报道/解读来源，尚未核验官方原文；不应视为最新官方政策公告。"
            existing = str(item.get("verification_note") or "").strip()
            item["verification_note"] = (existing + "；" if existing else "") + note
    return items


def fetch_pubmed_candidates():
    queries = [
        ('骨科临床研究全领域', '("Orthopedics"[MeSH Terms] OR "Sports Medicine"[MeSH Terms] OR arthroscopy[Title/Abstract] OR meniscus[Title/Abstract] OR ligament[Title/Abstract] OR "rotator cuff"[Title/Abstract] OR "anterior cruciate ligament"[Title/Abstract] OR fracture[Title/Abstract] OR spine[Title/Abstract] OR arthroplasty[Title/Abstract] OR osteoporosis[Title/Abstract] OR rehabilitation[Title/Abstract] OR "bone tumor"[Title/Abstract])'),
        ('骨科AI研究', '(("Artificial Intelligence"[MeSH Terms] OR "machine learning"[Title/Abstract] OR "deep learning"[Title/Abstract] OR "large language model"[Title/Abstract] OR robotics[Title/Abstract] OR wearable[Title/Abstract]) AND (orthopedic[Title/Abstract] OR orthopaedic[Title/Abstract] OR musculoskeletal[Title/Abstract] OR "sports medicine"[Title/Abstract] OR rehabilitation[Title/Abstract]))'),
        ('医疗AI交叉研究', '("Artificial Intelligence"[MeSH Terms] OR "machine learning"[Title/Abstract] OR "large language model"[Title/Abstract] OR "clinical decision support"[Title/Abstract] OR multimodal[Title/Abstract] OR agent[Title/Abstract]) AND (clinical[Title/Abstract] OR medicine[Title/Abstract] OR healthcare[Title/Abstract])')
    ]
    candidates, seen = [], set()
    headers = {'User-Agent': 'PersonalNewsIntelligence/1.0'}
    for label, query in queries:
        try:
            time.sleep(0.36)
            response = requests.get(
                'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi',
                params={'db':'pubmed','term':query + ' AND ("last 30 days"[Date - Publication])',
                        'retmode':'json','retmax':20,'sort':'date'},
                headers=headers, timeout=20
            )
            response.raise_for_status()
            ids = response.json().get('esearchresult', {}).get('idlist', [])
            records = _pubmed_records(ids)
        except Exception as exc:
            print(f'PubMed发现检索失败：{label}｜{type(exc).__name__}: {exc}', flush=True)
            continue
        for record in records:
            pmid = record.get('pmid')
            if not pmid or pmid in seen:
                continue
            seen.add(pmid)
            candidates.append({
                'id': f'P{len(candidates)+1}',
                'title': record.get('title') or 'PubMed未提供标题',
                'source': 'PubMed / NCBI',
                'published_at': record.get('publication_date') or 'PubMed未提供',
                'url': f'https://pubmed.ncbi.nlm.nih.gov/{pmid}/',
                'summary': record.get('abstract') or 'PubMed记录未提供摘要。',
                'pubmed_record': record, 'source_category': label
            })
        print(f'PubMed发现：{label}｜返回记录{len(records)}条', flush=True)
    return candidates


def _pubmed_records(pmids):
    if not pmids:
        return []
    time.sleep(0.36)
    params = {"db": "pubmed", "id": ",".join(pmids[:50]), "retmode": "xml"}
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
        "pubmed_record": candidate.get("pubmed_record"),
        "verification_status": "not_verified"
    }
    if result["pubmed_record"]:
        result["article_page_status"] = "pubmed_record"
        result["article_excerpt"] = result["pubmed_record"].get("abstract", "")
        result["doi"] = result["pubmed_record"].get("doi", "")
        result["verification_status"] = "pubmed_matched"
        return result
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
        status = getattr(getattr(exc, "response", None), "status_code", None)
        retry_after = getattr(getattr(exc, "response", None), "headers", {}).get("Retry-After")
        result["article_page_status"] = "fetch_failed:" + type(exc).__name__
        print(
            f"医学原文抓取失败：{candidate.get('title', '')[:90]}｜"
            f"HTTP={status or 'unknown'}｜Retry-After={retry_after or 'none'}｜{type(exc).__name__}",
            flush=True
        )

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
    # Decode the first complete JSON object; ignore trailing prose from the model.
    cleaned = raw.strip()
    if cleaned.startswith(chr(96) * 3):
        cleaned = cleaned.split("\n", 1)[-1]
    decoder = json.JSONDecoder()
    start = cleaned.find("{")
    if start < 0:
        raise ValueError(model + "未返回JSON对象")
    data, _ = decoder.raw_decode(cleaned[start:])
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
            f"- **类别：** {val(item, 'category', src.get('source_category', '未分类'))}",
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
                f"- **局限性：** {val(item, 'limitations')}",
                f"- **对运动医学的潜在启发：** {val(item, 'sports_medicine_insight')}"]
        else:
            lines += [f"- **背景：** {val(item, 'background')}",
                f"- **现实影响：** {val(item, 'public_impact')}"]
            if item.get("funding_implication"):
                lines.append(f"- **科研选题启发：** {val(item, 'funding_implication')}")
            if item.get("verification_note"):
                lines.append(f"- **核实说明：** {val(item, 'verification_note')}")
        lines.append(f"- **原文：** {src['url']}")
    return "\n".join(lines)

def _read_seen_state():
    try:
        with open('.cache/news_state.json', 'r', encoding='utf-8') as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        print(f'历史去重状态读取失败，将按新状态运行：{type(exc).__name__}', flush=True)
        return {}


def _normalize_seen_records(data, now=None):
    now = now or datetime.now(timezone.utc)
    records = data.get('records')
    if not isinstance(records, list):
        try:
            legacy_time = datetime.fromisoformat(str(data.get('updated_at')).replace('Z', '+00:00'))
            if legacy_time.tzinfo is None:
                legacy_time = legacy_time.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            legacy_time = now
        records = [{'url': url, 'last_seen_at': legacy_time.isoformat()}
                   for url in data.get('seen_urls', [])
                   if isinstance(url, str) and url.startswith(('http://', 'https://'))]
    cutoff = now - timedelta(days=30)
    kept, seen = [], set()
    for record in records:
        if not isinstance(record, dict):
            continue
        url = record.get('url')
        if not isinstance(url, str) or not url.startswith(('http://', 'https://')) or url in seen:
            continue
        try:
            stamp = datetime.fromisoformat(str(record.get('last_seen_at', '')).replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        if stamp >= cutoff:
            normalized = {'url': url, 'last_seen_at': stamp.astimezone(timezone.utc).isoformat()}
            if record.get('pmid'):
                normalized['pmid'] = str(record['pmid'])
            if record.get('doi'):
                normalized['doi'] = str(record['doi']).lower().replace('https://doi.org/', '').replace('http://doi.org/', '')
            kept.append(normalized)
            seen.add(url)
    return kept


def _write_seen_records(records):
    os.makedirs('.cache', exist_ok=True)
    with open('.cache/news_state.json', 'w', encoding='utf-8') as handle:
        json.dump({'updated_at': datetime.now(timezone.utc).isoformat(), 'records': records},
                  handle, ensure_ascii=False, indent=2)


def load_seen_urls():
    data = _read_seen_state()
    records = _normalize_seen_records(data)
    if 'records' not in data or len(records) != len(data.get('records', [])):
        _write_seen_records(records)
    return {record['url'] for record in records}


def save_seen_urls(urls):
    records = _normalize_seen_records(_read_seen_state())
    known = {record['url'] for record in records}
    now = datetime.now(timezone.utc).isoformat()
    for url in sorted(set(urls)):
        if isinstance(url, str) and url.startswith(('http://', 'https://')) and url not in known:
            records.append({'url': url, 'last_seen_at': now})
            known.add(url)
    _write_seen_records(_normalize_seen_records({'records': records}))


def load_seen_identifiers():
    records = _normalize_seen_records(_read_seen_state())
    pmids = {str(record['pmid']) for record in records if record.get('pmid')}
    dois = {str(record['doi']).lower() for record in records if record.get('doi')}
    return pmids, dois


def save_seen_items(items):
    records = _normalize_seen_records(_read_seen_state())
    by_url = {record['url']: record for record in records}
    now = datetime.now(timezone.utc).isoformat()
    for item in items:
        source = item.get('_source', {}) if isinstance(item, dict) else {}
        evidence = source.get('article_evidence') or {}
        pubmed = evidence.get('pubmed_record') or source.get('pubmed_record') or {}
        url = source.get('url')
        pmid = pubmed.get('pmid')
        doi = pubmed.get('doi') or evidence.get('doi') or source.get('doi')
        if doi:
            doi = str(doi).lower().replace('https://doi.org/', '').replace('http://doi.org/', '')
        if not url and not pmid and not doi:
            continue
        record = by_url.get(url) if url else None
        if record is None:
            record = {'url': url, 'last_seen_at': now} if url else {'last_seen_at': now}
            records.append(record)
            if url:
                by_url[url] = record
        if pmid:
            record['pmid'] = str(pmid)
        if doi:
            record['doi'] = doi
    _write_seen_records(_normalize_seen_records({'records': records}))


def remove_previously_pushed(candidates, seen_urls, seen_pmids=None, seen_dois=None):
    seen_pmids = seen_pmids or set()
    seen_dois = seen_dois or set()
    result = []
    for item in candidates:
        record = item.get('pubmed_record') or (item.get('article_evidence') or {}).get('pubmed_record') or {}
        pmid = str(record.get('pmid') or '')
        doi = str(record.get('doi') or item.get('doi') or '').lower()
        doi = doi.replace('https://doi.org/', '').replace('http://doi.org/', '')
        if item.get('url') in seen_urls or (pmid and pmid in seen_pmids) or (doi and doi in seen_dois):
            continue
        result.append(item)
    removed = len(candidates) - len(result)
    if removed:
        print(f'跨日去重：过滤已推送候选{removed}条（URL/PMID/DOI）', flush=True)
    return result

def _run_pipeline():
    weekly_cleanup = run_weekly_cache_cleanup()
    print(f"每周缓存清理：{weekly_cleanup}", flush=True)
    push_enabled = os.environ.get('PUSH_ENABLED', 'false').lower() == 'true'
    required = ['BAICHUAN_API_KEY', 'DEEPSEEK_API_KEY']
    if push_enabled:
        required.append('PUSHPLUS_TOKEN')
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        print('缺少必要环境变量：' + ', '.join(missing), flush=True)
        return 1

    print('开始采集新闻……', flush=True)
    seen_urls = load_seen_urls()
    seen_pmids, seen_dois = load_seen_identifiers()
    tavily_data = {"medical": [], "general": [], "nsfc": []}
    tavily_key = os.environ.get("TAVILY_KEY", "").strip()
    if tavily_key:
        try:
            tavily_data = fetch_tavily_candidates(tavily_key)
            print(f"Tavily主动搜索：学术={len(tavily_data['medical'])}条｜新闻={len(tavily_data['general'])}条｜NSFC={len(tavily_data['nsfc'])}条", flush=True)
        except Exception as exc:
            print(f"Tavily搜索失败，继续使用RSS/PubMed：{type(exc).__name__}", flush=True)
    else:
        print("未配置TAVILY_KEY，跳过主动搜索；RSS/PubMed继续运行。", flush=True)
    medical = fetch_candidates(MEDICAL_FEEDS, 'M', 24 * 90)
    medical.extend(tavily_data["medical"])
    try:
        medical.extend(fetch_pubmed_candidates())
    except Exception as exc:
        print(f'PubMed发现模块异常，继续使用其他来源：{type(exc).__name__}: {exc}', flush=True)
    medical_official = [x for x in OFFICIAL_PAGE_SOURCES if x.get('pipeline') == 'medical']
    general_official = [x for x in OFFICIAL_PAGE_SOURCES if x.get('pipeline') != 'medical']
    try:
        medical.extend(fetch_official_page_candidates(medical_official))
    except Exception as exc:
        print(f'国内外医疗监管来源模块异常，继续使用其他来源：{type(exc).__name__}: {exc}', flush=True)
    medical = remove_previously_pushed(medical, seen_urls, seen_pmids, seen_dois)
    medical = balance_medical_candidates(medical)

    general = fetch_candidates(GENERAL_FEEDS, 'G', 48)
    general.extend(tavily_data["general"])
    try:
        general.extend(fetch_official_page_candidates(general_official))
    except Exception as exc:
        print(f'国内官方来源模块异常，继续使用其他来源：{type(exc).__name__}: {exc}', flush=True)
    general = remove_previously_pushed(general, seen_urls)
    print(f'候选数量：医学{len(medical)}条（含PubMed），综合及国内官方来源{len(general)}条', flush=True)

    med_items = []
    try:
        screened_med_items = call_model(BAICHUAN_URL, os.environ['BAICHUAN_API_KEY'],
            'Baichuan-M3-Plus', MEDICAL_PROMPT, medical, 0.2)
        print(f'百川初筛完成：{len(screened_med_items)}条', flush=True)
        if screened_med_items:
            evidence_candidates = enrich_medical_items(screened_med_items)
            med_items = call_model(BAICHUAN_URL, os.environ['BAICHUAN_API_KEY'],
                'Baichuan-M3-Plus', MEDICAL_EVIDENCE_PROMPT, evidence_candidates, 0.1)
            print(f'百川证据复核完成：{len(med_items)}条', flush=True)
    except Exception as exc:
        print(f'百川医学模块失败，保留其他栏目继续运行：{type(exc).__name__}: {exc}', flush=True)

    news_items = []
    try:
        news_items = call_model(DEEPSEEK_URL, os.environ['DEEPSEEK_API_KEY'],
            'deepseek-flash', GENERAL_PROMPT, general, 0.4)
        news_items = annotate_policy_source(news_items)
        print(f'DeepSeek筛选完成：{len(news_items)}条', flush=True)
    except Exception as exc:
        print(f'DeepSeek综合新闻模块失败：{type(exc).__name__}: {exc}', flush=True)

    nsfc_items = []
    nsfc_candidates = filter_nsfc_candidates(remove_previously_pushed(tavily_data["nsfc"], seen_urls, seen_pmids, seen_dois))
    if nsfc_candidates:
        try:
            nsfc_items = call_model(BAICHUAN_URL, os.environ['BAICHUAN_API_KEY'],
                'Baichuan-M3-Plus', NSFC_PROMPT, nsfc_candidates, 0.1)
            for selected in nsfc_items:
                selected["category"] = selected["_source"].get("nsfc_type", selected.get("category", "未分类"))
            print(f"百川NSFC筛选完成：{len(nsfc_items)}条", flush=True)
        except Exception as exc:
            print(f"NSFC分析失败，不影响新闻主流程：{type(exc).__name__}", flush=True)

    if not med_items and not news_items and not nsfc_items:
        print('学术、NSFC与新闻模块均未产生结果，停止生成/推送。', flush=True)
        return 1

    local_now = datetime.now(timezone(timedelta(hours=8)))
    today = local_now.strftime('%Y-%m-%d')
    morning = MORNING_LINES[local_now.date().toordinal() % len(MORNING_LINES)]

    content = '\n\n'.join([
        f'# Personal News Intelligence\n**{today}｜每日专属资讯**',
        render('🦴 骨科临床研究与全领域医疗AI', med_items, True),
        render('🏛️ 国家自然科学基金科研动态', nsfc_items),
        render('🇨🇳 国内医疗政策、国内外重大事件与科技经济', news_items),
        '## 🌅 今日晨语\n' + morning,
        '---\n*资讯由自动采集与AI辅助整理；请以原始来源为准。研究细节以PubMed/期刊原文核验，政策以官方原文为准。*'
    ])
    # 不将完整简报写入Actions日志，避免长期保留正文。
    print(f"简报生成完成：骨科/医疗AI={len(med_items)}条｜NSFC={len(nsfc_items)}条｜综合新闻={len(news_items)}条｜正文字符数={len(content)}", flush=True)
    for group in (med_items, nsfc_items, news_items):
        for item in group:
            src = item.get('_source', {})
            print(f"入选标题：{item.get('headline', src.get('title', '未命名'))[:120]}｜{src.get('url', '')}", flush=True)

    if not push_enabled:
        print('安全模式：仅生成预览，不发送微信，也不更新已推送去重记录。', flush=True)
        return 0

    print('正在发送PushPlus……', flush=True)
    response = requests.post(PUSHPLUS_URL, json={
        'token': os.environ['PUSHPLUS_TOKEN'], 'title': f'📬 每日新闻情报 | {today}',
        'content': content, 'template': 'markdown'
    }, timeout=30)
    response.raise_for_status()
    result = response.json()
    if str(result.get('code')) != '200':
        raise RuntimeError('PushPlus返回非成功状态：' + json.dumps(result, ensure_ascii=False))
    save_seen_items(med_items + nsfc_items + news_items)
    print('PushPlus已确认接受每日简报；已更新跨日去重状态。', flush=True)

    return 0
def main():
    try:
        return _run_pipeline()
    finally:
        cleaned = cleanup_daily_transients()
        print(f"当日临时数据清理：{cleaned}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
