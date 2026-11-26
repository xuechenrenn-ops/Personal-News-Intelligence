"""Validate factual fields before they can appear in an unattended digest."""
import re
import unicodedata
from datetime import date
from decimal import Decimal

from experiments.discover import EXCLUDED
from experiments.preview import clean_text
from experiments.run import eligible

ORTHO_TERMS = re.compile(r"orthop[ae]edi|arthroplast|arthroscop|fracture|menisc|ligament|"
                         r"osteopor|lumbar|cervical|musculoskelet|plantar fasciitis|"
                         r"frozen shoulder|adhesive capsulitis|rotator cuff|knee|cartilage|gait", re.I)
AI_TERMS = re.compile(r"artificial intelligence|machine learning|deep learning|large language model|"
                      r"neural network|clinical decision support|wearable|ehealth|digital health", re.I)
ACCIDENT = re.compile(r"fuel tanker|hostage.tak|(?:crash|fire) (?:kills|killed)|FIFA|celebrity|"
                      r"娱乐|网红|油罐车|交通事故|火灾|起火|明星", re.I)
EXCEPTION = re.compile(r"nationwide|one hundred|states of emergency|全国|百人|重大制度", re.I)
NUMBER = re.compile(r"(?<![A-Za-z0-9.])[-+−]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)%?")
STATISTICS = (
    (r"优势比|比值比|\bOR\b", r"odds ratio|\ba?OR\b"),
    (r"风险比（HR）|危险比|\bHR\b", r"hazard ratio|\ba?HR\b"),
    (r"相对风险|\bRR\b", r"relative risk|risk ratio|\ba?RR\b"),
    (r"置信区间|\bCI\b", r"confidence interval|\bCI\b"),
)
UNSUPPORTED_ADVICE = re.compile(r"应当使用|应常规|无需复核|可取代医生|应立即|证明|证实|"
                                r"保证|最佳疗法|值得推广|应采用|推荐给患者|属于行业趋势")
OBSERVATION = re.compile(r"retrospective|observational|registry|cross.sectional|case series", re.I)
CAUSAL = re.compile(r"导致|带来|预防了|使患者|改善了|降低了|[提高降低改善]患者")
BAD_RATIO = re.compile(
    r"\bOR\s*(?:为|=|是)?\s*\d+(?:\.\d+)?[^。；]{0,55}(?:风险降低|降低.*风险)|"
    r"(?:风险降低|降低[^，。；]{0,9}风险)[^。；]{0,30}\bOR\s*(?:为|=|是)?\s*\d+")
SOURCE_SENTENCES = re.compile(r"(?<=[。！？；])|(?<=[.!?])\s+(?=[A-Z\u4e00-\u9fff])|\n+")
RELEVANCE_LANES = ("clinical", "ortho-ai", "medical-ai")
STUDY_WEIGHT = re.compile(r"randomi[sz]ed|systematic review|meta.analysis|external validation|"
                          r"guideline|multicent[er]{2}|prospective", re.I)
NO_RESULTS_DESIGN = re.compile(r"trial protocol|protocol for|technical note|bibliometric", re.I)


def classify(card):
    title = card["title"]
    if EXCLUDED.search(title) or re.search(r"dentistr|periodont", title, re.I):
        return None
    if ORTHO_TERMS.search(title):
        return "ortho-ai" if AI_TERMS.search(title) else "clinical"
    if AI_TERMS.search(title) and re.search(r"patient|clinical|medical|hospital|health care|electronic health", title, re.I):
        return "medical-ai"
    return None


def choose_pool(cards, as_of, max_per=6, max_total=16):
    buckets = {lane: [] for lane in RELEVANCE_LANES}
    nonmedical = []
    for card in eligible(cards, as_of):
        if card["lane"] != "medical":
            if card["lane"] == "world" and ACCIDENT.search(card["title"]) and not EXCEPTION.search(card["title"]):
                continue
            nonmedical.append(card)
            continue
        lane = classify(card)
        if lane:
            buckets[lane].append(card)
    today = date.fromisoformat(as_of)
    for bucket in buckets.values():
        bucket.sort(key=lambda row: (
            bool(NO_RESULTS_DESIGN.search(row["title"])),
            (today - date.fromisoformat(row["date"])).days > 7,
            not bool(STUDY_WEIGHT.search(row["title"])),
            (today - date.fromisoformat(row["date"])).days,
        ))
    shortlist = [buckets[lane][idx] for idx in range(max_per) for lane in RELEVANCE_LANES
                 if idx < len(buckets[lane])]
    return shortlist[:max_total] + nonmedical[:10]


def segments(source):
    pieces = [clean_text(x) for x in SOURCE_SENTENCES.split(source) if x.strip()]
    return [p[:1250] for p in pieces if len(p) > 15][:28]


def number_set(text):
    return {str(Decimal(match.replace(",", "").replace("%", "").replace("−", "-")).normalize())
            + ("%" if match.endswith("%") else "") for match in NUMBER.findall(unicodedata.normalize("NFKC", text))}


def verify_fact(value, source, *, question=False):
    if not isinstance(value, str) or not value.strip() or len(value) > 290:
        return False
    if not number_set(value) <= number_set(source):
        return False
    if BAD_RATIO.search(value) or (not question and OBSERVATION.search(source) and CAUSAL.search(value)):
        return False
    for label, source_label in STATISTICS:
        if re.search(label, value, re.I) and not re.search(source_label, source, re.I):
            return False
    if UNSUPPORTED_ADVICE.search(value) or re.search(r"https?://|\[[^\]]+\]\(", value):
        return False
    return not question or len(value) <= 100


def source_text(card, refs):
    available = segments(card["evidence"])[:20]
    if not isinstance(refs, list) or not refs or len(refs) > 6:
        return ""
    if any(type(i) is not int or not 0 <= i < len(available) for i in refs):
        return ""
    return "\n".join(available[i] for i in refs)


MISSING = "原始摘要未能核实，暂不报告。"
FIELD_LABELS = {
    "question": "研究问题", "design": "设计与样本量", "result": "原文主要结果",
    "limits": "局限", "insight": "科研启发（待验证）",
}


def verify_insight(value):
    return (isinstance(value, str) and 12 <= len(value) <= 105
            and not number_set(value) and not UNSUPPORTED_ADVICE.search(value)
            and bool(re.search(r"验证|比较|检验|测量|评估|随访|预设|队列|分层", value))
            and not re.search(r"https?://|\[[^\]]+\]\(", value))


def audit_items(raw, cards, rejected_by_review=()):
    by_id = {r["id"]: r for r in cards}
    rejected = set(rejected_by_review)
    counts = {"medical": 0, "policy": 0, "world": 0}
    maxima = {"medical": 3, "policy": 1, "world": 2}
    checked, audit = [], []
    used = set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        identifier = item["id"]
        if identifier not in by_id or identifier in used:
            continue
        source = by_id[identifier]
        lane = source["lane"]
        if counts[lane] >= maxima[lane]:
            continue
        counts[lane] += 1
        used.add(identifier)
        row = {"id": identifier, "lane": lane}
        fields = FIELD_LABELS if lane == "medical" else {"summary": "要点"}
        removed = []
        for field in fields:
            val = item.get(field)
            if field == "insight":
                accepted = verify_insight(val)
            elif field == "limits" and isinstance(val, str) and val in (
                "原始摘要未报告关键局限", "观察性设计，仅支持关联",
                "技术验证尚未评价真实临床效果", "题库表现尚未验证临床可用性"):
                accepted = True
            else:
                sources = item.get("refs")
                anchors = source_text(source, sources.get(field, []) if isinstance(sources, dict) else [])
                accepted = bool(anchors and verify_fact(val, anchors, question=field == "question"))
            if (identifier, field) in rejected:
                accepted = False
            if accepted:
                row[field] = clean_text(val)
            else:
                removed.append(field)
        audit.append({"id": identifier, "fields_allowed": sorted(set(fields) - set(removed)),
                      "fields_removed": removed, "retained": bool(row.get("result") or row.get("summary"))})
        if row.get("result") or row.get("summary"):
            checked.append(row)
    return checked, audit
