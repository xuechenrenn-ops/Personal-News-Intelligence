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
            # Official pricing: "Search/Fetch API 每 1,000 次成功请求 5元".
            # A failed request (HTTP error / transport error) is not a successful
            # request and is therefore not billed.
            entry["status"] = str(exc) if re.fullmatch(r"HTTP_\d+", str(exc)) else "transport_error"
            entry["observed_peak_micro_cny"] = 0
            return {}
        if not isinstance(result, dict):
            entry["status"] = "malformed_response"
            entry["observed_peak_micro_cny"] = 0
            return {}
        useful = result.get("results") if name == "search" else result.get("content")
        entry["status"] = "billable" if useful else "empty"
        # A 200 response is a successful request and is billed per the official
        # per-successful-request rule, whether or not the body carries content.
        entry["observed_peak_micro_cny"] = TOOL_PRICE
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
    rows = tools.search(f"骨科 骨折 脊柱 手术规划 智能康复 machine learning prediction OR multimodal OR 外部验证 lang:zh,en after:{lower}")
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


def evening_events(tools, as_of, seen_urls):
    """Bounded gap-fill for evening major events. One search, no repeat queries.

    Triggered only when the collected world pool is thin. Returns world-lane
    cards shaped like news_card output, deduped against already-known URLs.
    """
    day = date.fromisoformat(as_of)
    rows = tools.search(
        f"重大政策 公共卫生 科技突破 重要决定 lang:zh,en after:{day - timedelta(days=3)}")
    cards = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title, url = row.get("title"), row.get("url")
        if not isinstance(title, str) or not isinstance(url, str) or url in seen_urls:
            continue
        if not on_site(url, NEWS + ("gov.cn", "miit.gov.cn", "cmse.gov.cn")):
            continue
        stamp = str(row.get("date") or "")[:10]
        if stamp != as_of:
            continue
        cards.append({"id": "EVT-" + re.sub(r"\W+", "", url)[-12:], "lane": "world",
                      "title": title, "source": urlsplit(url).hostname or "",
                      "date": as_of, "url": url, "primary": True,
                      "evidence": excerpt(str(row.get("content") or row.get("snippet") or ""),
                                          title)[:720],
                      "source_scope": "DeepSeek Search 节选"})
    return cards, {"results": len(rows), "added": len(cards)}


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


AOSSM_CALENDAR = "https://events.sportsmed.org/am2023/about-the-meeting/future-meeting-dates"
AOSSM_ENTRY = re.compile(
    r"<h[23][^>]*>\s*Annual Meeting (20\d{2})\s*</h[23]>\s*"
    r"<p[^>]*>\s*(?:<strong>)?([A-Z][a-z]+)\s+(\d{1,2})[-–]\d{1,2},\s*\1",
    re.I,
)


def aossm_calendar(as_of, *, state=None):
    """Read the society's official meeting dates. Announce only near-term events."""
    from datetime import datetime
    import requests

    source = "calendar:aossm"
    if state and state.status(source):
        return [], {"status": "cooldown"}
    try:
        response = requests.get(AOSSM_CALENDAR, timeout=8, allow_redirects=False)
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError("Unsupported response")
        found = []
        for year, month, day in AOSSM_ENTRY.findall(response.text):
            stamp = datetime.strptime(f"{month} {day} {year}", "%B %d %Y").date()
            found.append(stamp)
        if state:
            state.record(source, bool(found))
        current = date.fromisoformat(as_of)
        upcoming = [{"title": "AOSSM 年度会议", "url": AOSSM_CALENDAR, "date": d.isoformat(), "kind": "会议提醒"}
                    for d in sorted(set(found)) if 0 <= (d - current).days <= 90]
        return upcoming, {"status": "ok", "dated_entries": len(found),
                          "next_meeting": min((d.isoformat() for d in found if d >= current), default=None)}
    except (requests.RequestException, ValueError) as exc:
        code = getattr(getattr(exc, "response", None), "status_code", None)
        if state:
            state.record(source, False, code in {401, 403, 429})
        return [], {"status": code or type(exc).__name__}
