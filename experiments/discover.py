"""Collect public-source candidates; this module never uses a service secret."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

from research_scope import OUTSIDE_SCOPE as EXCLUDED, research_lane

from news_push import (
    GENERAL_FEEDS, _medical_candidate_lane, _normalized_title, _strip_html,
    balance_medical_candidates, fetch_candidates, fetch_official_page_candidates,
    fetch_pubmed_candidates,
)

CST = timezone(timedelta(hours=8))
HEALTH_RSS = ("中国新闻网-健康", "https://www.chinanews.com.cn/rss/health.xml")
POLICY_MEDIA_LABEL = "媒体援引主管部门，官方原文未核验"
POLICY_AUTHORITY = re.compile(r"国家(?:医疗保障局|医保局|卫生健康委|药监局)|国家卫生健康委员会")
POLICY_SOURCE = {
    "label": "国家医保局", "url": "https://www.nhsa.gov.cn/",
    "path_pattern": r"^/art/20\d{2}/\d{1,2}/\d{1,2}/art_14_", "fresh_only": True,
    "encoding": "utf-8",
    "keywords": ["医保", "用药", "药学", "医疗服务"], "max_items": 4,
}
WORLD_SOURCE = {
    "label": "中国载人航天工程办公室", "url": "https://www.cmse.gov.cn/xwzx/zhxw/",
    "path_pattern": r"^/xwzx/\d{6}/t\d{8}_\d+\.html$",
    "path_date": r"/t(20\d{2})(\d{2})(\d{2})_", "max_age_days": 3,
    "fresh_only": True, "encoding": "utf-8", "max_items": 3,
    "timeout": 8,
}
TECH_SOURCE = {
    "label": "工业和信息化部", "url": "https://www.miit.gov.cn/xbymdz/zwgk/index.html",
    "path_pattern": r"^/(?:zwgk|jgsj)/.+/art/20\d{2}/art_[\da-f]+\.html$",
    "detail_date": r"发布时间\s*[:：]\s*(20\d{2})-(\d{2})-(\d{2})(?:\s+(\d{2}):(\d{2}))?",
    "max_age_days": 3, "fresh_only": True, "encoding": "utf-8", "max_items": 3, "timeout": 8,
    "keywords": ["通信网", "工业互联网", "算力", "半导体", "人工智能", "智能制造", "芯片", "标准", "新能源"],
}
NON_ORIGINAL = {"Editorial", "News", "Comment", "Published Erratum",
                "Retracted Publication", "Retraction of Publication"}
COUNTRIES = {"中国": r"\bChina\b", "日本": r"\bJapan\b", "韩国": r"\b(?:South Korea|Republic of Korea)\b",
             "美国": r"\b(?:USA|United States)\b", "英国": r"\b(?:UK|United Kingdom)\b",
             "荷兰": r"\bNetherlands\b", "澳大利亚": r"\bAustralia\b",
             "德国": r"\bGermany\b", "加拿大": r"\bCanada\b", "法国": r"\bFrance\b",
             "意大利": r"\bItaly\b", "瑞士": r"\bSwitzerland\b", "奥地利": r"\bAustria\b",
             "西班牙": r"\bSpain\b", "丹麦": r"\bDenmark\b", "瑞典": r"\bSweden\b",
             "挪威": r"\bNorway\b", "比利时": r"\bBelgium\b", "新加坡": r"\bSingapore\b",
             "印度": r"\bIndia\b", "埃及": r"\bEgypt\b", "巴西": r"\bBrazil\b", "波兰": r"\bPoland\b",
             "土耳其": r"\b(?:Turkey|Türkiye)\b", "新西兰": r"\bNew Zealand\b"}


def full_date(raw):
    """Accept an explicit complete date and preserve the source's calendar day."""
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        for style in ("%Y %b %d", "%Y %B %d"):
            try:
                return datetime.strptime(raw, style).date().isoformat()
            except ValueError:
                continue
    return ""


def region_for(affiliations):
    names = [group for group, pattern in COUNTRIES.items()
             if any(re.search(pattern, text, re.I) for text in affiliations)]
    return "、".join(names) if len(names) <= 1 and names else (
        "国际合作（" + "、".join(names) + "）" if names else "研究机构地域未核实"
    )


def medical_card(item, *, allow_possible=False):
    title = _strip_html(item["title"])
    rec = item.get("pubmed_record") or {}
    abstract = _strip_html(rec.get("abstract", ""))
    pmid, journal = rec.get("pmid"), rec.get("journal")
    types = set(rec.get("publication_types", []))
    if (not pmid or not journal or not abstract or EXCLUDED.search(title)
            or types & NON_ORIGINAL
            or (not allow_possible and not research_lane(title, rec.get("mesh", [])))):
        return None
    published = full_date(rec.get("online_date") or rec.get("publication_date") or "")
    if not published:
        return None
    card = {"id": item["id"], "lane": "medical", "title": title, "source": journal,
            "date": published, "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            "pmid": pmid, "region": region_for(rec.get("affiliations", [])),
            "primary": True, "evidence": abstract[:2000],
            "original_abstract": (rec.get("original_abstract") or rec.get("abstract") or "").strip(),
            "mesh": rec.get("mesh", []), "publication_types": sorted(types)}
    if rec.get("doi"):
        card["doi"] = rec["doi"]
    return card


def news_card(item, lane, now, *, secondary=False, hours=48):
    stamp = item["published_at"]
    full = full_date(stamp)
    if not full:
        return None
    parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CST)
    if not timedelta(0) <= now - parsed <= timedelta(hours=hours):
        return None
    url = item["url"]
    if urlsplit(url).scheme != "https":
        return None
    if lane == "policy" and not secondary and item.get("evidence_status") != "detail_fetched":
        return None
    evidence_scope = {"source_scope": "官方原文节选", "official": True} if (
        item.get("evidence_status") == "detail_fetched" and lane == "world") else {}
    return {"id": ("policy-" if lane == "policy" else "") + item["id"],
            "lane": lane, "title": _strip_html(item["title"]), "source": item["source"],
            "date": parsed.astimezone(CST).date().isoformat(), "url": url,
            "primary": True, "evidence": _strip_html(item.get("summary", ""))[:(1250 if evidence_scope else 720)],
            **evidence_scope}


def secondary_policy_cards(rows, now):
    for item in rows:
        summary = _strip_html(item.get("summary", ""))
        if not POLICY_AUTHORITY.search(item["title"] + " " + summary):
            continue
        if summary and (card := news_card(item, "policy", now, secondary=True)):
            card["verification_note"] = POLICY_MEDIA_LABEL
            yield card


def collect_live_cards(skip_official=False, *, include_academic=True, wider=False,
                       major_official=False, technology_official=False, discovery=None,
                       health=None):
    now = datetime.now(CST)
    print(f"RUN_STARTED={now.isoformat()}", flush=True)
    labels = {"中国新闻网-即时", "BBC-World", "Al Jazeera"}
    if wider:
        labels.update({"中国新闻网-国际", "中国新闻网-财经"})
    feeds = [feed for feed in GENERAL_FEEDS if feed[0] in labels]
    reporting = {"trace": discovery} if discovery is not None else {}
    # health applies to the RSS reader only: fetch_official_page_candidates does
    # not accept it, so keep the two kwargs sets separate.
    rss_kwargs = dict(reporting)
    if health is not None:
        rss_kwargs["health"] = health
    with ThreadPoolExecutor(max_workers=5) as pool:
        academic = pool.submit(fetch_pubmed_candidates) if include_academic else None
        policy = None if skip_official else pool.submit(fetch_official_page_candidates, [POLICY_SOURCE])
        news = [pool.submit(fetch_candidates, [feed], f"G{idx}-", 72 if major_official else 48, **rss_kwargs)
                for idx, feed in enumerate(feeds)]
        official = [pool.submit(fetch_official_page_candidates, [source], **reporting)
                    for source in ([WORLD_SOURCE] if major_official else []) +
                    ([TECH_SOURCE] if technology_official else [])]
    academic_raw = [item for item in academic.result() if medical_card(item)] if academic else []
    academic_ranked = balance_medical_candidates(academic_raw, max_per_lane=4, max_total=12)
    # Keep both clinical and AI lanes while using fresh publications first within each.
    shortlisted = []
    lane_counts = {}
    for item in academic_ranked:
        lane = _medical_candidate_lane(item)
        if lane_counts.get(lane, 0) < 2:
            shortlisted.append(medical_card(item))
            lane_counts[lane] = lane_counts.get(lane, 0) + 1
    policy_raw = policy.result() if policy else []
    policy_rows = [card for item in policy_raw if (card := news_card(item, "policy", now))]
    if not policy_rows:
        fallback = fetch_candidates([HEALTH_RSS], "NH", 48)
        policy_rows = list(secondary_policy_cards(fallback, now))[:3]
        print(f"POLICY_FALLBACK candidates={len(policy_rows)}", flush=True)
    world = []
    seen = set()
    official_rows = [[{**item, "id": f"W-{idx}-" + item["id"]} for item in future.result()]
                     for idx, future in enumerate(official)]
    rows_by_source = official_rows + [future.result() for future in news]
    for rows in rows_by_source:
        for index, item in enumerate(rows):
            reason = None
            card = news_card(item, "world", now, hours=72 if major_official else 48)
            if index >= (7 if wider else 3):
                reason = "source_cap"
            elif item["id"].startswith("W-") and item.get("evidence_status") != "detail_fetched":
                reason = "official_detail_unavailable"
            elif card:
                key = _normalized_title(card["title"])
                if key and key not in seen:
                    world.append(card)
                    seen.add(key)
                else:
                    reason = "duplicate_title"
            else:
                reason = "date_or_url"
            if discovery is not None:
                discovery.append({k: item[k] for k in ("id", "title", "source", "url")} |
                                 {"outcome": reason or "collected"})
    cards = shortlisted + policy_rows + world
    print(f"FREE_DISCOVERY academic_verified={len(academic_raw)} "
          f"academic_shortlist={len(shortlisted)} policy={len(policy_rows)} world={len(world)}",
          flush=True)
    return cards, now.date().isoformat()


def main(output, skip_official=False):
    cards, as_of = collect_live_cards(skip_official)
    data = {"as_of": as_of, "candidates": [
        {key: value for key, value in card.items() if key != "evidence"} for card in cards
    ]}
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("PAID_REQUESTS=0 TAVILY_CREDITS=0 WECHAT_REQUESTS=0", flush=True)


if __name__ == "__main__":
    main(Path(sys.argv[1]), "--fallback-only" in sys.argv[2:])
