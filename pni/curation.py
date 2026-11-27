"""V1.7 topic priorities, separate from the six evidence-quality scores."""
from collections import Counter
from datetime import date, timedelta
import re

from research_scope import CLINICAL_CONTEXT, ORTHOPAEDIC, OUTSIDE_SCOPE, research_lane

AI = re.compile(
    r"artificial[- ]intelligence|machine[- ]learning|deep[- ]learning|neural network|"
    r"language model|\b(?:LLMs?|GPT(?:-\d+)?|XGBoost|random forests?)\b|"
    r"AI[- ](?:based|assisted|powered|enabled|agent)|"
    r"comput(?:er|ational)[- ]vision|"
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
SLOT = {"guideline": "clinical", "clinical": "clinical",
        "ortho-ai": "ortho-ai", "medical-ai": "medical-ai"}
DIAGNOSTIC_CONTEXT = re.compile(r"physicians?|radiologists?|patholog(?:y|ist)|endoscop\w*|biops\w*", re.I)


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
    method = re.search(r"(?:materials and )?methods?:\s*(.+?)(?=\s+(?:results?|findings?):|$)",
                       abstract, re.I | re.S)
    if AI.search(title) or (
            method and AI.search(method[1])
            and re.search(r"predict|validat|automat|classif|模型|验证|预测|自动|分类", title, re.I)):
        if ortho or ORTHOPAEDIC.search(abstract):
            return "ortho-ai"
        if CLINICAL_CONTEXT.search(semantic) or DIAGNOSTIC_CONTEXT.search(semantic):
            return "medical-ai"
    return "clinical" if ortho else "possible"


def world_allowed(title, evidence=""):
    return not LOW_IMPACT.search(title) or bool(PUBLIC_IMPACT.search(evidence[:1500]))


def pools(cards):
    counts = Counter(map(field, cards))
    return {key: counts[key] for key in ORDER}


def missing_slots(cards):
    return set(SLOT.values()) - {SLOT.get(field(card)) for card in cards}


def candidates(ranked, scores, as_of, limit=4, *, reserve=True):
    """AI-first among viable papers, with recency and topic diversity within tiers."""
    groups = {key: [] for key in ORDER}
    today = date.fromisoformat(as_of)
    for card in ranked:
        lane = field(card)
        age = (today - date.fromisoformat(card["date"])).days
        if not 0 <= age <= (90 if reserve or lane in {"ortho-ai", "medical-ai"} else 30):
            continue
        groups[lane].append(card)
    for group in groups.values():
        group.sort(key=lambda card: (
            (today - date.fromisoformat(card["date"])).days > 30,
            card["id"] not in scores,
            -scores.get(card["id"], {}).get("weighted", 0),
        ))
    ortho, other = groups["ortho-ai"], groups["medical-ai"]
    if reserve:
        clinical = (groups["guideline"] + groups["clinical"])[:1]
        lead = [*ortho[:1], *other[:1], *clinical][:limit]
        if limit > len(lead) and len(ortho) > 1:
            first_theme = next((p for _, p in THEMES if re.search(p, ortho[0]["title"], re.I)), None)
            second = next((c for c in ortho[1:] if not first_theme or
                           not re.search(first_theme, c["title"], re.I)), ortho[1])
            lead.append(second)
        return lead
    # One orthopedic AI lead if available. The second AI slot may also be orthopedic.
    streams = [*ortho[:1], *other[:1], *ortho[1:], *other[1:]]
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


def widen(as_of, remaining, missing):
    """Free, bounded Europe PMC fallback, with original records resolved by PubMed."""
    from experiments.discover import medical_card
    from experiments.stage2.channels import EU_QUERIES, europe, _pubmed_records

    today = date.fromisoformat(as_of)
    extra, trace, seen = [], {}, {row.get("pmid") for row in remaining}
    for slot, names in (("ortho-ai", ("ortho-ai",)), ("medical-ai", ("clinical-ai",)),
                        ("clinical", ("guidelines", "ortho-clinical"))):
        if slot not in missing:
            continue
        for days in (30, 90):
            try:
                query = " OR ".join(f"({EU_QUERIES[name]})" for name in names)
                imports = list(dict.fromkeys(str(row["pmid"]) for row in europe(query, today, days)
                                             if row.get("pmid") and str(row["pmid"]) not in seen))[:24]
                found = []
                for row in _pubmed_records(imports) if imports else ():
                    card = medical_card({"id": row["pmid"], "title": row["title"], "pubmed_record": row},
                                        allow_possible=True)
                    if (card and (today - timedelta(days=days)).isoformat() <= card["date"] <= as_of
                            and SLOT.get(field(card)) == slot):
                        found.append({**card, "evidence": row["abstract"], "channels": [f"europe:{slot}:{days}d"]})
                extra.extend(found)
                seen.update(x["pmid"] for x in found)
                trace[f"{slot}:{days}d"] = {"ids": len(imports), "verified": len(found)}
                if found:
                    break
            except (OSError, ValueError, KeyError) as exc:
                trace[f"{slot}:{days}d"] = {"error": type(exc).__name__}
    return extra, trace


def enrich_open_results(cards, *, deep=False):
    """Add bounded original excerpts from publicly accessible article XML."""
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
            if deep:
                blocks = {}
                for section in document.findall(".//body/sec"):
                    heading = section.findtext("title", "")
                    kind = ("m" if re.search(r"\bmethods?|design|materials\b", heading, re.I) else
                            "r" if re.search(r"\bresults?|findings\b", heading, re.I) else None)
                    if not kind:
                        continue
                    text = " ".join(" ".join(node.itertext()) for node in section.findall(".//p"))
                    text = " ".join(text.split())[:2200]
                    if text:
                        blocks.setdefault(kind, text)
                if blocks:
                    card["open_sections"] = blocks
                    card["evidence_scope"] = f"原始摘要与公开全文节选（{pmcid}）"
                    record.update(status="used", pmcid=pmcid, sections=sorted(blocks))
                continue
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
