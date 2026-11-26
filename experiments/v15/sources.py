"""Bounded DeepSeek tools. Search results are leads and Fetch is limited to public sources."""
from datetime import date, timedelta
import re
from urllib.parse import urlsplit

from experiments.run import api_json
from experiments.stage2.channels import hints

TOOL_PRICE = 5_000
MAX_TOOLS = 5
NEWS = ("chinanews.com.cn", "bbc.co.uk", "bbc.com", "aljazeera.com", "nhsa.gov.cn")
FUNDING = ("nsfc.gov.cn",)
MEETINGS = ("sportsmed.org", "aaos.org", "esska.org", "coa.org.cn")
NOTICE = re.compile(r"申报|指南|征集|submission|registration|annual meeting|congress|会议|年会", re.I)
FUNDING_SCOPE = re.compile(r"骨科|运动系统|医学科学|医学人工智能|H06|临床医学", re.I)


def on_site(url, sites):
    if not isinstance(url, str):
        return False
    try:
        u = urlsplit(url)
        host = u.hostname or ""
        return u.scheme == "https" and not u.username and not u.password and u.port in (None, 443) and any(
            host == site or host.endswith("." + site) for site in sites)
    except ValueError:
        return False


class SearchFetch:
    def __init__(self, headers, requests, transport=api_json):
        if type(requests) is not int or not 0 <= requests <= MAX_TOOLS:
            raise ValueError("Invalid tool allotment")
        self.headers = headers
        self.limit = requests
        self.transport = transport
        self.calls = []

    def call(self, name, payload):
        if len(self.calls) >= self.limit:
            return {}
        entry = {"tool": name, "max_micro_cny": TOOL_PRICE, "status": "uncertain"}
        self.calls.append(entry)
        try:
            result = self.transport("https://api.deepseek.com/v1/tools/" + name, self.headers, payload)
        except RuntimeError as exc:
            entry["status"] = str(exc) if re.fullmatch(r"HTTP_\d+", str(exc)) else "transport_error"
            return {}
        if not isinstance(result, dict):
            return {}
        useful = result.get("results") if name == "search" else result.get("content")
        entry["status"] = "billable" if useful else "empty"
        entry["observed_peak_micro_cny"] = TOOL_PRICE if useful else 0
        return result

    def search(self, query):
        return self.call("search", {"query": query, "count": 8, "need_content": False}).get("results", [])

    def fetch(self, url, sites):
        if not on_site(url, sites):
            return ""
        result = self.call("fetch", {"url": url})
        return excerpt(result.get("content", ""), result.get("title", ""))


def excerpt(content, title):
    if not isinstance(content, str):
        return ""
    lines = [
        re.sub(r"\[([^\]]+)\]\(https?://[^)]+\)", r"\1", line.strip())
        for line in content.splitlines()
        if len(line.strip()) >= 55 and not line.lstrip().startswith(("#", "![", "[", "* "))
    ]
    return "\n".join(line for line in lines if line.strip() != title)[:2200]


def academic(tools, as_of, verify):
    lower = date.fromisoformat(as_of) - timedelta(days=21)
    rows = tools.search(f"骨科 运动医学 ACL 截骨 术后康复 外部验证 randomized clinical trial lang:zh,en after:{lower}")
    pmids, dois = hints([{"url": row.get("url"), "title": row.get("title"),
                         "content": row.get("snippet")} for row in rows if isinstance(row, dict)])
    cards = []
    for key, values in (("pmid", pmids), ("doi", dois)):
        for value in sorted(values)[:4]:
            try:
                item = verify({key: value})
            except (OSError, RuntimeError, TypeError, ValueError, KeyError):
                continue
            if item and lower <= date.fromisoformat(item["date"]) <= date.fromisoformat(as_of):
                cards.append({**item, "channels": ["deepseek-search"]})
    return cards, {"results": len(rows), "identifiers": len(pmids) + len(dois),
                   "verified": [card["id"] for card in cards]}


def enrich_news(tools, cards):
    trace = []
    for card in cards:
        url = card["url"]
        data = tools.fetch(url, NEWS)
        if len(data) >= 160:
            card["evidence"] = data
            card["source_scope"] = "报道正文节选"
        trace.append({"id": card["id"], "body_chars": len(data)})
    return trace


def alerts(tools, as_of, old):
    """Separate the two discovery queries to retain domestic and society coverage."""
    day = date.fromisoformat(as_of)
    lower = day - timedelta(days=21)
    collections = (
        ("国自然动态", FUNDING, tools.search(f"医学科学部 项目指南 申报 site:nsfc.gov.cn after:{lower}")),
        ("会议公告", MEETINGS, tools.search("orthopaedic sports medicine annual meeting registration site:sportsmed.org")),
    )
    matches = []
    for kind, sites, rows in collections:
        for row in rows:
            if not isinstance(row, dict):
                continue
            title, url = row.get("title"), row.get("url")
            if (not isinstance(title, str) or not NOTICE.search(title) or not on_site(url, sites)
                    or "url:" + url.casefold() in old):
                continue
            stamp = str(row.get("date") or "")[:10]
            try:
                published = date.fromisoformat(stamp)
            except ValueError:
                continue
            if lower <= published <= day and (kind != "国自然动态" or FUNDING_SCOPE.search(title)):
                matches.append({"title": title, "url": url, "date": stamp, "kind": kind})
    return matches[:2], sum(len(rows) for _, _, rows in collections)
