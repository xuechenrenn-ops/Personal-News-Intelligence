"""Measure retrieval by channel and require article identifiers before publication."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta, datetime, timezone
import json
import re
from urllib.parse import urlsplit

import requests

from experiments.discover import medical_card
from experiments.run import api_json
from news_push import _pubmed_records, fetch_pubmed_candidates

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
TAVILY = "https://api.tavily.com"
DOI = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)
PMID = re.compile(r"(?:pubmed\.ncbi\.nlm\.nih\.gov/|PMID[:\s]+)(\d{6,10})", re.I)
EU_QUERIES = {
    "ortho-clinical": '(TITLE_ABS:arthroplasty OR TITLE_ABS:fracture OR TITLE_ABS:"rotator cuff" OR TITLE_ABS:"frozen shoulder" OR TITLE_ABS:meniscus OR TITLE_ABS:"anterior cruciate ligament" OR TITLE_ABS:musculoskeletal) AND (TITLE_ABS:randomized OR TITLE_ABS:systematic OR TITLE_ABS:cohort)',
    "ortho-ai": '(TITLE_ABS:orthopaedic OR TITLE_ABS:orthopedic OR TITLE_ABS:fracture OR TITLE_ABS:arthroplasty OR TITLE_ABS:gait OR TITLE_ABS:rehabilitation OR TITLE_ABS:musculoskeletal) AND (TITLE_ABS:"machine learning" OR TITLE_ABS:"artificial intelligence" OR TITLE_ABS:"deep learning" OR TITLE_ABS:"large language model" OR TITLE_ABS:"wearable sensor")',
    "china-affiliation": '(AFF:China OR AFF:Chinese) AND (TITLE_ABS:arthroplasty OR TITLE_ABS:"adhesive capsulitis" OR TITLE_ABS:orthopaedic OR TITLE_ABS:fracture OR TITLE_ABS:musculoskeletal) AND (TITLE_ABS:randomized OR TITLE_ABS:systematic OR TITLE_ABS:"machine learning")',
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
SEARCH_CREDIT_MICRO_CNY = 100_000  # USD 0.008/credit, rounded up with exchange headroom


def key_for(row):
    if row.get("doi"):
        return row["doi"].casefold()
    return row.get("pmid") or row["url"]


class SourceHealth:
    def __init__(self, path, now):
        self.path, self.now = path, now
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
        "pageSize": 32, "sort": "FIRST_PDATE desc", "format": "json", "resultType": "lite"},
        timeout=12)
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
    except (RuntimeError, KeyError, ValueError):
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
            except (RuntimeError, KeyError, ValueError):
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
    except (RuntimeError, KeyError, ValueError):
        account["quota_unavailable"] = True
        return results
    account["observed_key_credit_change"] = after["key_used"] - before["key_used"]
    account["observed_plan_credit_change"] = after["plan_used"] - before["plan_used"]
    if type(after["paygo"]) is int and type(before["paygo"]) is int:
        account["observed_paygo_change"] = after["paygo"] - before["paygo"]
    return results


def hints(rows):
    by_id = {}
    dois = set()
    for row in rows:
        text = " ".join(str(row.get(k) or "") for k in ("url", "title", "content"))
        for pmid in PMID.findall(text):
            by_id[pmid] = None
        for doi in DOI.findall(text):
            dois.add(doi.rstrip(".,;").casefold())
    return set(by_id), dois


def ids_for_dois(dois, maximum=18):
    if not dois:
        return set()
    parts = [f'DOI:"{doi}"' for doi in sorted(dois)[:maximum]]
    response = requests.get(EPMC, params={"query": f"({' OR '.join(parts)}) AND SRC:MED",
                                         "format": "json", "pageSize": maximum}, timeout=10)
    response.raise_for_status()
    return {str(x["pmid"]) for x in response.json()["resultList"]["result"] if x.get("pmid")}


def metadata(rows):
    return {key: value for key, value in rows.items() if key not in {"evidence", "segments"}}


def collect(days, *, key=None, credit_limit=0, state=None):
    health = SourceHealth(state, datetime.now(timezone.utc))
    account = {"attempted": 0, "credits": 0, "unverified": 0}
    web = search_web(key, days, credit_limit, health, account) if key and credit_limit else {}
    stats, ranked = {}, {}
    probes = {name: probe(name, url, health) for name, url in ACCESS.items()}
    wechat = next((row.get("url") for groups in web.values()
                   for row in groups.get("tavily-wechat", []) if row.get("url")), None)
    if wechat and urlsplit(wechat).scheme == "https":
        probes["public-wechat"] = probe("public-wechat", wechat, health)
    for as_of in days:
        with ThreadPoolExecutor(max_workers=5) as pool:
            pubmed = pool.submit(fetch_pubmed_candidates, as_of, 14)
            eur = {name: pool.submit(europe, query, as_of) for name, query in EU_QUERIES.items()
                   if not health.status("europe:" + name)}
        pm_rows = pubmed.result()
        records = {row["pubmed_record"]["pmid"]: row["pubmed_record"] for row in pm_rows}
        groups = {"pubmed": {"rows": len(pm_rows), "ids": sorted(records)}}
        for name, future in eur.items():
            channel = "europe:" + name
            try:
                rows = future.result()
                groups[channel] = {"rows": len(rows), "ids": sorted({
                    str(x["pmid"]) for x in rows if x.get("pmid")})}
                health.record(channel, True)
            except (requests.RequestException, KeyError, ValueError) as exc:
                groups[channel] = {"rows": 0, "ids": [], "status": type(exc).__name__}
                health.record(channel, False)
        for name, rows in web.get(str(as_of), {}).items():
            pmids, dois = hints(rows)
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
            card = medical_card({"title": row["title"], "pubmed_record": row, "id": pmid})
            if not card:
                continue
            published = date.fromisoformat(card["date"])
            if not as_of - timedelta(days=30) <= published <= as_of:
                continue
            card["evidence"] = row["abstract"]
            card["channels"] = [name for name, group in groups.items() if pmid in group["ids"]]
            cards[pmid] = card
        for group in groups.values():
            group["verified"] = sorted(set(group["ids"]) & set(cards))
        stats[str(as_of)] = groups
        ranked[str(as_of)] = list(cards.values())
    health.save()
    return ranked, {"per_window": stats, "access_probes": probes, "tavily": account}
