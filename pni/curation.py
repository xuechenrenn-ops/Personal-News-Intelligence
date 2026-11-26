"""V1.7 topic priorities, separate from the six evidence-quality scores."""
from collections import Counter
from datetime import date, timedelta
import re

from research_scope import CLINICAL_CONTEXT, ORTHOPAEDIC, OUTSIDE_SCOPE, research_lane

AI = re.compile(
    r"artificial[- ]intelligence|machine[- ]learning|deep[- ]learning|neural network|"
    r"language model|\b(?:LLMs?|GPT(?:-\d+)?|XGBoost|random forests?)\b|"
    r"AI[- ](?:based|assisted|powered|enabled|agent)|robot[- ]assisted|"
    r"comput(?:er|ational)[- ](?:navigation|vision)|智能(?:手术|康复|诊断|导航)|"
    r"人工智能|机器学习|深度学习|大模型|智能体", re.I)
GUIDELINE = re.compile(r"practice guideline|clinical guideline|consensus (?:statement|recommendation)|"
                       r"临床实践指南|专家共识|诊疗指南", re.I)
LOW_IMPACT = re.compile(r"保质期标注|食品标签|瓶身标注|社区活动|一般研讨会|\bconsumer complaint\b", re.I)
PUBLIC_IMPACT = re.compile(r"全国实施|全国性召回|国家标准正式生效|全国推广|公共卫生应急|"
                            r"法案通过|正式签署|nationwide recall|entered into force", re.I)
THEMES = (
    ("shoulder", r"rotator[- ]cuff|shoulder|肩袖|肩关节"),
    ("knee", r"\bACL(?:R)?\b|menisc|knee|patellofemoral|膝关节|半月板|前交叉韧带"),
    ("spine", r"spine|spinal|lumbar|cervical|脊柱|腰椎|颈椎"),
    ("hip", r"\bhip\b|acetabul|髋关节"),
)
ORDER = ("ortho-ai", "medical-ai", "guideline", "clinical", "possible")


def field(card):
    title = card["title"]
    if OUTSIDE_SCOPE.search(title):
        return "possible"
    abstract = card.get("evidence", "")[:5000]
    base = research_lane(title, card.get("mesh", []))
    semantic = title + " " + abstract
    ortho = base in {"clinical", "ortho-ai"} or bool(ORTHOPAEDIC.search(title))
    if (ortho or ORTHOPAEDIC.search(abstract)) and GUIDELINE.search(
            title + " " + " ".join(card.get("publication_types", []))):
        return "guideline"
    if AI.search(title) or (AI.search(abstract) and re.search(
            r"predict|validat|model|automat|classif|detect|模型|验证|预测|自动|分类", title, re.I)):
        if ortho or ORTHOPAEDIC.search(abstract):
            return "ortho-ai"
        if CLINICAL_CONTEXT.search(semantic):
            return "medical-ai"
    return "clinical" if ortho else "possible"


def world_allowed(title, evidence=""):
    return not LOW_IMPACT.search(title) or bool(PUBLIC_IMPACT.search(evidence[:1500]))


def pools(cards):
    counts = Counter(map(field, cards))
    return {key: counts[key] for key in ORDER}


def candidates(ranked, scores, as_of, limit=4, *, reserve=True):
    """AI-first among viable papers, with recency and topic diversity within tiers."""
    groups = {key: [] for key in ORDER}
    today = date.fromisoformat(as_of)
    for card in ranked:
        lane = field(card)
        age = (today - date.fromisoformat(card["date"])).days
        if age > 30 and lane not in {"ortho-ai", "medical-ai"}:
            continue
        groups[lane].append(card)
    for group in groups.values():
        group.sort(key=lambda card: (
            (today - date.fromisoformat(card["date"])).days > 30,
            card["id"] not in scores,
            -scores.get(card["id"], {}).get("weighted", 0),
        ))
    ortho, other = groups["ortho-ai"], groups["medical-ai"]
    # One orthopedic AI lead if available. The second AI slot may also be orthopedic.
    streams = [*ortho[:1], *other[:1], *ortho[1:], *other[1:]]
    if reserve and limit > 2:
        clinical = groups["guideline"][:1] or groups["clinical"][:1]
        streams[2:2] = clinical
    streams.extend(groups["guideline"] + groups["clinical"] + groups["possible"])
    result, deferred = [], []
    seen, themes = set(), set()
    for card in streams:
        key = card["id"]
        if key in seen:
            continue
        seen.add(key)
        theme = next((name for name, pattern in THEMES if re.search(pattern, card["title"], re.I)), None)
        if theme and theme in themes:
            deferred.append(card)
            continue
        if theme:
            themes.add(theme)
        result.append(card)
    return (result + deferred)[:limit]


def widen_ai(as_of, remaining):
    """Free, bounded Europe PMC fallback, with original records resolved by PubMed."""
    from experiments.discover import medical_card
    from experiments.stage2.channels import EU_QUERIES, europe, _pubmed_records

    today = date.fromisoformat(as_of)
    extra, trace, seen = [], {}, {row.get("pmid") for row in remaining}
    for name in ("ortho-ai", "clinical-ai"):
        if any(field(row) == ("ortho-ai" if name == "ortho-ai" else "medical-ai") for row in remaining):
            continue
        for days in (30, 90):
            try:
                imports = [str(row["pmid"]) for row in europe(EU_QUERIES[name], today, days)
                           if row.get("pmid") and str(row["pmid"]) not in seen][:24]
                found = []
                for row in _pubmed_records(imports) if imports else ():
                    card = medical_card({"id": row["pmid"], "title": row["title"], "pubmed_record": row},
                                        allow_possible=True)
                    if (card and (today - timedelta(days=days)).isoformat() <= card["date"] <= as_of
                            and field(card) in {"ortho-ai", "medical-ai"}):
                        found.append({**card, "evidence": row["abstract"], "channels": [f"europe:{name}:{days}d"]})
                extra.extend(found)
                seen.update(x["pmid"] for x in found)
                trace[f"{name}:{days}d"] = {"ids": len(imports), "verified": len(found)}
                if found:
                    break
            except (OSError, ValueError, KeyError) as exc:
                trace[f"{name}:{days}d"] = {"error": type(exc).__name__}
    return extra, trace


def enrich_open_results(cards):
    """Use one legally open original Results section when the abstract is thin."""
    import xml.etree.ElementTree as ET

    import requests

    from experiments.stage2.channels import EPMC

    trace = []
    for card in cards:
        if not card.get("pmid"):
            continue
        record = {"id": card["id"], "status": "unavailable"}
        trace.append(record)
        try:
            response = requests.get(EPMC, params={"query": f'SRC:MED AND EXT_ID:{card["pmid"]}',
                                                  "format": "json", "resultType": "core"}, timeout=8)
            response.raise_for_status()
            results = response.json()["resultList"]["result"]
            match = next((row for row in results if str(row.get("pmid")) == card["pmid"]
                          and row.get("isOpenAccess") == "Y"), None)
            pmcid = match.get("pmcid", "") if match else ""
            if not re.fullmatch(r"PMC\d+", pmcid):
                continue
            response = requests.get(f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML",
                                    timeout=10, stream=True)
            with response:
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_content(65536):
                    body.extend(chunk)
                    if len(body) > 2_000_000:
                        break
            if len(body) > 2_000_000:
                continue
            document = ET.fromstring(body)
            for section in document.findall(".//body/sec"):
                if not re.match(r"results?\b", section.findtext("title", ""), re.I):
                    continue
                paragraphs = [" ".join("".join(node.itertext()).split()) for node in section.findall("p")]
                evidence = []
                for clean in paragraphs:
                    if len(" ".join(evidence)) + len(clean) > 1450:
                        break
                    evidence.append(clean)
                if evidence:
                    from experiments.stage3.evidence import sections
                    parsed = sections(card["evidence"])
                    prior = card["evidence"] if parsed else "Background: " + card["evidence"]
                    card["evidence"] = prior + "\nResults: " + " ".join(evidence)
                    card["evidence_scope"] = f"原始摘要与开放全文结果节选（{pmcid}）"
                    record.update(status="used", pmcid=pmcid)
                break
        except (OSError, ValueError, KeyError, ET.ParseError) as exc:
            record["status"] = type(exc).__name__
    return trace
