"""Measure retrieval by channel and require article identifiers before publication."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta, datetime, timezone
import json
import re
from urllib.parse import quote, urlsplit

import requests

from experiments.discover import medical_card, region_for
from experiments.run import api_json
from news_push import _pubmed_records, _strip_html, fetch_candidates, fetch_pubmed_candidates
from research_scope import research_lane

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
TAVILY = "https://api.tavily.com"
DOI = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)
PMID = re.compile(r"(?:pubmed\.ncbi\.nlm\.nih\.gov/|PMID[:\s]+)(\d{6,10})", re.I)
EU_QUERIES = {
    "ortho-clinical": '(TITLE_ABS:arthroplasty OR TITLE_ABS:fracture OR TITLE_ABS:"rotator cuff" OR TITLE_ABS:"frozen shoulder" OR TITLE_ABS:meniscus OR TITLE_ABS:"anterior cruciate ligament" OR TITLE_ABS:musculoskeletal OR TITLE_ABS:osteotomy OR TITLE_ABS:ACL) AND (TITLE_ABS:randomized OR TITLE_ABS:systematic OR TITLE_ABS:cohort)',
    "ortho-ai": '(TITLE_ABS:orthopaedic OR TITLE_ABS:orthopedic OR TITLE_ABS:fracture OR TITLE_ABS:arthroplasty OR TITLE_ABS:gait OR TITLE_ABS:rehabilitation OR TITLE_ABS:musculoskeletal OR TITLE_ABS:osteotomy OR TITLE_ABS:ACLR) AND (TITLE_ABS:"machine learning" OR TITLE_ABS:"artificial intelligence" OR TITLE_ABS:"deep learning" OR TITLE_ABS:"large language model" OR TITLE_ABS:"wearable sensor")',
    "china-affiliation": '(AFF:China OR AFF:Chinese) AND (TITLE_ABS:arthroplasty OR TITLE_ABS:"adhesive capsulitis" OR TITLE_ABS:orthopaedic OR TITLE_ABS:fracture OR TITLE_ABS:musculoskeletal OR TITLE_ABS:osteotomy OR TITLE_ABS:ACL) AND (TITLE_ABS:randomized OR TITLE_ABS:systematic OR TITLE_ABS:"machine learning")',
    "clinical-ai": '(TITLE_ABS:"electronic health record" OR TITLE_ABS:"clinical decision support") AND (TITLE_ABS:"deep learning" OR TITLE_ABS:"machine learning" OR TITLE_ABS:"large language model")',
}
WEB_TOPICS = (
    ("tavily-en", "Orthopaedic clinical trials sports injury rehabilitation fracture arthritis surgery prediction AI original research PubMed",
     ["pubmed.ncbi.nlm.nih.gov", "link.springer.com", "nature.com", "journals.lww.com", "jamanetwork.com", "bmj.com"], "en"),
    ("tavily-zh-institution", "中国医院 高校 骨科 运动医学 肩关节 骨折 骨科康复 人工智能 最新研究 论文 DOI",
     ["puh3.net.cn", "xiangya.com.cn", "shsmu.edu.cn", "tongji.edu.cn", "zju.edu.cn", "pku.edu.cn", "ncsti.gov.cn"], "zh"),
    ("tavily-zh-media", "骨科 运动医学 肩周炎 骨关节手术 临床试验 人工智能 学术研究 DOI",
     ["news.sciencenet.cn", "ncsti.gov.cn", "health.rednet.cn", "cn-healthcare.com", "chinanews.com.cn"], "zh"),
    ("tavily-wechat", "骨科运动医学 膝关节 肩关节 骨折 人工智能 临床研究 高校医院 发表 论文",
     ["mp.weixin.qq.com"], "zh"),
)
ACCESS = {
    "cn-hospital": "https://www.puh3.net.cn/info/1971/185181.htm",
    "cn-science-media": "https://news.sciencenet.cn/sbhtmlnews/2026/11/392013.shtm?id=392013",
    "cn-public-science": "https://www.ncsti.gov.cn/kjdt/kjrd/yyjk_kjrd/202611/t20261110_262378.html",
    "foreign-journal": "https://www.jmir.org/2026/1/e94936/",
}
ACADEMIC_FEEDS = {
    "science-rss": ("科学网医药论文", "https://www.sciencenet.cn/xml/paper.aspx?di=1"),
    "npj-rss": ("npj Digital Medicine", "https://www.nature.com/npjdigitalmed.rss"),
}
SELECTION_TERMS = re.compile(r"骨科|骨折|肩关节|半月板|关节置换|骨质疏松|骨关节|运动医学|"
                             r"骨科康复|步态|artificial intelligence|external validation|"
                             r"musculoskeletal|orthop[ae]edic|rehabilitation", re.I)
LITERATURE_DOMAINS = {
    "news.sciencenet.cn", "health.rednet.cn", "mp.weixin.qq.com",
    "cn-healthcare.com", "www.puh3.net.cn", "news.pku.edu.cn", "www.xiangya.com.cn",
}
SEARCH_CREDIT_MICRO_CNY = 100_000  # USD 0.008/credit, rounded up with exchange headroom


class SourceHealth:
    def __init__(self, path, now):
        self.path, self.now = path, now.astimezone(timezone.utc)
        try:
            self.rows = json.loads(path.read_text()) if path else {}
        except (OSError, ValueError):
            self.rows = {}

    def status(self, name):
        until = self.rows.get(name, {}).get("until")
        return bool(until and until > self.now.isoformat())

    def record(self, name, success, blocked=False):
        if success:
            self.rows.pop(name, None)
        else:
            count = self.rows.get(name, {}).get("failures", 0) + 1
            self.rows[name] = {"failures": count}
            if blocked or count >= 3:
                self.rows[name]["until"] = (self.now + timedelta(days=3)).isoformat()

    def save(self):
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            pending = self.path.with_suffix(".tmp")
            pending.write_text(json.dumps(self.rows))
            pending.replace(self.path)


def probe(name, url, health):
    if health.status(name):
        return {"status": "cooldown"}
    try:
        response = requests.get(url, timeout=8, stream=True)
        status = response.status_code
        response.raise_for_status()
        ids = set()
        # Probe indexed article metadata and citations only. No retained pages.
        for chunk in response.iter_content(65536):
            for token in DOI.findall(chunk.decode("utf-8", "ignore")):
                ids.add(token.rstrip(".,;").casefold())
            break
        response.close()
        health.record(name, True)
        return {"status": status, "doi_count": len(ids)}
    except requests.RequestException as ex:
        code = getattr(ex.response, "status_code", None)
        health.record(name, False, code in {401, 403, 429})
        return {"status": code or type(ex).__name__}


def europe(query, as_of, lookback=14):
    start = as_of - timedelta(days=lookback)
    response = requests.get(EPMC, params={"query":
        f"({query}) AND SRC:MED AND FIRST_PDATE:[{start} TO {as_of}]",
        "pageSize": 36, "format": "json", "resultType": "lite"},
        timeout=13)
    response.raise_for_status()
    return response.json()["resultList"]["result"]


def tavily_quota(headers):
    data = api_json(TAVILY + "/usage", headers)
    key, acct = data["key"], data["account"]
    counters = [key["usage"], acct["plan_limit"], acct["plan_usage"]]
    if any(type(n) is not int or n < 0 for n in counters):
        raise RuntimeError("Unverified Tavily quota")
    used, allowance, consumed = counters
    room = min(allowance - consumed, 700 - used)
    if type(key.get("limit")) is int:
        room = min(room, key["limit"] - used)
    return {"key_used": used, "plan_used": consumed, "room": room,
            "paygo": acct.get("paygo_usage")}


def search_web(key, days, credit_limit, health, account):
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    results = {}
    try:
        before = tavily_quota(headers)
    except (RuntimeError, KeyError, ValueError) as exc:
        account["quota_failure"] = type(exc).__name__ + ":" + str(exc)[:55]
        account["quota_unavailable"] = True
        return results
    account["available_before"] = before["room"] >= credit_limit + 16
    account["paygo_counter_available"] = type(before["paygo"]) is int
    if not account["available_before"]:
        return results
    for as_of in days:
        for name, query, domains, language in WEB_TOPICS:
            if health.status(name) or account["attempted"] >= credit_limit:
                continue
            try:
                previous = tavily_quota(headers)
            except (RuntimeError, KeyError, ValueError) as exc:
                account["quota_failure"] = type(exc).__name__ + ":" + str(exc)[:55]
                account["quota_unavailable"] = True
                break
            if previous["room"] < 16:
                return results
            account["attempted"] += 1
            print(f"TAVILY_RESERVATION count={account['attempted']} max_credit=1", flush=True)
            try:
                doc = api_json(TAVILY + "/search", headers, {
                    "query": query, "search_depth": "basic", "auto_parameters": False,
                    "max_results": 12, "include_usage": True, "include_domains": domains,
                    "language": language, "start_date": str(as_of - timedelta(days=14)),
                    "end_date": str(as_of), "include_answer": False, "include_raw_content": False,
                })
                actual = (doc.get("usage") or {}).get("credits")
                if type(actual) is not int or not 0 <= actual <= 1:
                    account["unverified"] += 1
                    break
                account["credits"] += actual
                print(f"TAVILY_USAGE credits={actual}", flush=True)
                rows = doc.get("results", [])
                if name == "tavily-wechat":
                    rows = [x for x in rows if urlsplit(x.get("url", "")).hostname == "mp.weixin.qq.com"]
                health.record(name, bool(rows))
                results.setdefault(str(as_of), {})[name] = rows
            except (RuntimeError, KeyError, ValueError):
                health.record(name, False)
                account["unverified"] += 1
                break
        if account["unverified"] or account.get("quota_unavailable"):
            break
    try:
        after = tavily_quota(headers)
    except (RuntimeError, KeyError, ValueError) as exc:
        account["quota_failure"] = type(exc).__name__ + ":" + str(exc)[:55]
        account["quota_unavailable"] = True
        return results
    account["observed_key_credit_change"] = after["key_used"] - before["key_used"]
    account["observed_plan_credit_change"] = after["plan_used"] - before["plan_used"]
    if type(after["paygo"]) is int and type(before["paygo"]) is int:
        account["observed_paygo_change"] = after["paygo"] - before["paygo"]
    return results


def hints(rows):
    by_id = set()
    dois = set()
    for row in rows:
        text = " ".join(str(row.get(k) or "") for k in ("url", "title", "content"))
        for pmid in PMID.findall(text):
            by_id.add(pmid)
        for doi in DOI.findall(text):
            dois.add(doi.rstrip(".,;").casefold())
    return by_id, dois


def scholarly_rss(health):
    """RSS is a discovery source, and every hit still needs bibliographic validation."""
    results = {}
    for name, feed in ACADEMIC_FEEDS.items():
        if health.status(name):
            results[name] = []
            continue
        rows = fetch_candidates([feed], "scholar", 24 * 15)
        health.record(name, bool(rows))
        results[name] = [{"url": row["url"], "title": row["title"], "content": row["summary"],
                          "published_date": row["published_at"]} for row in rows]
    return results


def linked_identifiers(rows, health, limit=2):
    matched_pmids, matched_dois = hints(rows)
    selected = 0
    for row in rows:
        url = row.get("url", "")
        parsed = urlsplit(url)
        hostname = parsed.hostname
        if (selected >= limit or parsed.scheme != "https" or hostname not in LITERATURE_DOMAINS
                or health.status(hostname) or not SELECTION_TERMS.search(row.get("title", ""))):
            continue
        selected += 1
        try:
            with requests.get(url, allow_redirects=False, stream=True, timeout=7) as response:
                if response.status_code != 200:
                    health.record(hostname, False, response.status_code in {401, 403, 429})
                    continue
                data = next(response.iter_content(131072), b"")
        except requests.RequestException:
            health.record(hostname, False)
            continue
        pmids, dois = hints([{"content": data.decode("utf-8", "ignore")}])
        matched_pmids |= pmids
        matched_dois |= dois
        health.record(hostname, True)
    return matched_pmids, matched_dois


def ids_for_dois(dois, maximum=18):
    if not dois:
        return set()
    parts = [f'DOI:"{doi}"' for doi in sorted(dois)[:maximum]]
    response = requests.get(EPMC, params={"query": f"({' OR '.join(parts)}) AND SRC:MED",
                                         "format": "json", "pageSize": maximum}, timeout=10)
    response.raise_for_status()
    return {str(x["pmid"]) for x in response.json()["resultList"]["result"] if x.get("pmid")}


def crossref_card(doi):
    """Verify DOI-only candidates when a registered abstract and exact date exist."""
    if not isinstance(doi, str) or DOI.fullmatch(doi) is None:
        return None
    response = requests.get("https://api.crossref.org/works/" + quote(doi, safe="/"),
                            timeout=8, headers={"User-Agent": "PersonalNewsIntelligence/2.0"})
    response.raise_for_status()
    return crossref_record(doi, response.json()["message"])


def crossref_record(doi, row):
    if (row.get("type") != "journal-article" or row.get("DOI", "").casefold() != doi.casefold()
            or any("retract" in str(item.get("type", "")).casefold() for item in row.get("update-to", []))):
        return None
    published = row.get("published-online") or row.get("published")
    dates = published.get("date-parts", [[]])[0] if published else []
    title = _strip_html((row.get("title") or [""])[0])
    raw_abstract = row.get("abstract") or ""
    raw_abstract = re.sub(r"<(?:jats:)?title[^>]*>(.*?)</(?:jats:)?title>", r" \1: ", raw_abstract, flags=re.I | re.S)
    abstract = _strip_html(raw_abstract)
    journal = (row.get("container-title") or [""])[0]
    if len(dates) != 3 or not title or not abstract or not journal:
        return None
    affiliations = [entry["name"] for author in row.get("author", [])
                    for entry in author.get("affiliation", []) if entry.get("name")]
    return {"id": doi, "lane": "medical", "title": title, "source": journal,
            "date": date(*dates).isoformat(), "url": "https://doi.org/" + doi,
            "doi": doi, "region": region_for(affiliations), "primary": True, "evidence": abstract[:6000]}


CROSSREF_LANES = {
    "crossref-ortho-rehab": "ACL reconstruction anterior knee pain recovery randomized trial",
    "crossref-clinical": "orthopaedic hip fracture randomized rehabilitation prospective clinical trial",
    "crossref-ai": "orthopaedic machine learning external validation fracture surgery prediction clinical study",
}


def crossref_discover(label, query, as_of, health):
    if health.status(label):
        return [], "cooldown"
    params = {
        "filter": f"type:journal-article,from-pub-date:{as_of - timedelta(days=15)},until-pub-date:{as_of}",
        "query.title": query, "rows": 18, "sort": "score",
        "select": "DOI,title,container-title,published,published-online,abstract,type,author,update-to",
    }
    try:
        response = requests.get("https://api.crossref.org/works", params=params, timeout=10,
                                headers={"User-Agent": "PersonalNewsIntelligence/2.0"})
        response.raise_for_status()
        return response.json()["message"]["items"], "ok"
    except (requests.RequestException, ValueError, KeyError) as exc:
        return [], type(exc).__name__


def metadata(rows):
    return {key: value for key, value in rows.items() if key not in {"evidence", "segments"}}


def collect(days, *, key=None, credit_limit=0, state=None, allow_possible=False,
            crossref_search=False):
    health = SourceHealth(state, datetime.now(timezone.utc))
    account = {"attempted": 0, "credits": 0, "unverified": 0}
    web = search_web(key, days, credit_limit, health, account) if key and credit_limit else {}
    stats, ranked = {}, {}
    probes = {name: probe(name, url, health) for name, url in ACCESS.items()}
    wechat = next((row.get("url") for groups in web.values()
                   for row in groups.get("tavily-wechat", []) if row.get("url")), None)
    if wechat and urlsplit(wechat).scheme == "https":
        probes["public-wechat"] = probe("public-wechat", wechat, health)
    rss = scholarly_rss(health)
    for as_of in days:
        with ThreadPoolExecutor(max_workers=5) as pool:
            pubmed = pool.submit(fetch_pubmed_candidates, as_of, 21, True)
            eur = {name: pool.submit(europe, query, as_of) for name, query in EU_QUERIES.items()
                   if not health.status("europe-v2:" + name)}
            cr = {name: pool.submit(crossref_discover, name, query, as_of, health)
                  for name, query in CROSSREF_LANES.items()} if crossref_search else {}
        pm_rows = pubmed.result()
        records = {row["pubmed_record"]["pmid"]: row["pubmed_record"] for row in pm_rows}
        groups = {"pubmed": {"rows": len(pm_rows), "ids": sorted(records)}}
        for name, future in eur.items():
            channel = "europe:" + name
            try:
                rows = future.result()
                groups[channel] = {"rows": len(rows), "ids": sorted({
                    str(x["pmid"]) for x in rows if x.get("pmid")})}
                health.record("europe-v2:" + name, True)
            except (requests.RequestException, KeyError, ValueError) as exc:
                groups[channel] = {"rows": 0, "ids": [], "status": type(exc).__name__}
                code = getattr(getattr(exc, "response", None), "status_code", None)
                groups[channel]["http_status"] = code
                health.record("europe-v2:" + name, False, code in {401, 403, 429})
        web_dois = {}
        for name, rows in {**rss, **web.get(str(as_of), {})}.items():
            pmids, dois = linked_identifiers(rows, health)
            web_dois[name] = dois
            pmids |= {pmid for pmid, record in records.items() if record.get("doi", "").casefold() in dois}
            try:
                pmids |= ids_for_dois(dois)
            except (requests.RequestException, ValueError, KeyError):
                pass
            groups[name] = {"rows": len(rows), "ids": sorted(pmids), "doi_hints": len(dois),
                            "domains": sorted({urlsplit(x.get("url", "")).hostname or "" for x in rows})}
        all_ids = sorted(set.union(*(set(g["ids"]) for g in groups.values())))
        extra = sorted(set(all_ids) - records.keys())
        for offset in range(0, len(extra), 50):
            try:
                for row in _pubmed_records(extra[offset:offset+50]):
                    records[row["pmid"]] = row
            except (requests.RequestException, ValueError):
                break
        cards = {}
        for pmid, row in records.items():
            card = medical_card({"title": row["title"], "pubmed_record": row, "id": pmid},
                                allow_possible=allow_possible)
            if not card:
                continue
            published = date.fromisoformat(card["date"])
            if not as_of - timedelta(days=30) <= published <= as_of:
                continue
            card["evidence"] = row["abstract"]
            card["channels"] = [name for name, group in groups.items() if pmid in group["ids"]]
            cards[pmid] = card
        known_dois = {c.get("doi", "").casefold() for c in cards.values()}
        unindexed = sorted(set.union(set(), *web_dois.values()) - known_dois)[:8]
        with ThreadPoolExecutor(max_workers=3) as pool:
            crossref = {doi: pool.submit(crossref_card, doi) for doi in unindexed}
        for doi, future in crossref.items():
            try:
                card = future.result()
                if card and as_of - timedelta(days=30) <= date.fromisoformat(card["date"]) <= as_of:
                    card["channels"] = [name for name, dois in web_dois.items() if doi in dois]
                    cards[doi] = card
            except (requests.RequestException, ValueError, KeyError):
                continue
        indexed = {row.get("doi", "").casefold(): identifier for identifier, row in cards.items()}
        for channel, future in cr.items():
            rows, status = future.result()
            found = set()
            if status != "cooldown":
                health.record(channel, status == "ok")
            for row in rows:
                doi = str(row.get("DOI", "")).casefold()
                if doi in indexed and doi:
                    identifier = indexed[doi]
                    found.add(identifier)
                    if channel not in cards[identifier]["channels"]:
                        cards[identifier]["channels"].append(channel)
                    continue
                try:
                    card = crossref_record(doi, row)
                except (KeyError, TypeError, ValueError):
                    continue
                if (not card or not as_of - timedelta(days=30) <= date.fromisoformat(card["date"]) <= as_of
                        or not research_lane(card["title"])):
                    continue
                card["channels"] = [channel]
                cards[doi] = card
                indexed[doi] = doi
                found.add(doi)
            groups[channel] = {"rows": len(rows), "ids": sorted(found), "status": status}
        for name, group in groups.items():
            group["verified"] = sorted(set(group["ids"]) & set(cards)
                                       | web_dois.get(name, set()) & set(cards))
        stats[str(as_of)] = groups
        ranked[str(as_of)] = list(cards.values())
    health.save()
    return ranked, {"per_window": stats, "access_probes": probes, "tavily": account}
