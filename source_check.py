import json
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import feedparser
import requests


class LinkCounter(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.count = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            self.count += 1


def main():
    sources = json.loads(Path("sources.json").read_text(encoding="utf-8"))
    meetings = json.loads(Path("meeting_calendar.json").read_text(encoding="utf-8"))
    headers = {"User-Agent": "PersonalNewsIntelligence/1.0"}
    total, ok, failed = 0, 0, 0

    print("=== RSS source connectivity ===")
    for group in ("medical", "general"):
        for source in sources["rss"].get(group, []):
            total += 1
            try:
                response = requests.get(source["url"], headers=headers, timeout=20)
                response.raise_for_status()
                parsed = feedparser.parse(response.content)
                count = len(parsed.entries)
                sample = parsed.entries[0].get("title", "")[:100] if count else "no entries"
                print(f"OK | {source['label']} | HTTP {response.status_code} | entries={count} | {sample}")
                if parsed.bozo:
                    print(f"WARNING | {source['label']} | feed parser reported malformed feed")
                ok += 1
            except Exception as exc:
                failed += 1
                status = getattr(getattr(exc, "response", None), "status_code", None)
                print(f"FAIL | {source['label']} | HTTP={status or 'unknown'} | {type(exc).__name__}: {exc}")

    print("\n=== Official page connectivity ===")
    for source in sources.get("official_pages", []):
        if source.get("enabled", True) is False:
            print(f"SKIP | {source['label']} | {source.get('access_note', 'disabled')}")
            continue
        total += 1
        try:
            response = requests.get(source["url"], headers=headers, timeout=20)
            response.raise_for_status()
            parser = LinkCounter()
            parser.feed(response.text)
            print(f"OK | {source['label']} | HTTP {response.status_code} | content_type={response.headers.get('Content-Type', 'unknown')} | anchors={parser.count}")
            ok += 1
        except Exception as exc:
            failed += 1
            status = getattr(getattr(exc, "response", None), "status_code", None)
            print(f"FAIL | {source['label']} | HTTP={status or 'unknown'} | {type(exc).__name__}: {exc}")

    print("\n=== Academic meeting source connectivity ===")
    for source in meetings.get("watch_sources", []):
        if source.get("enabled", True) is False:
            print(f"SKIP | {source['name']} | {source.get('access_note', 'disabled')}")
            continue
        total += 1
        try:
            response = requests.get(source["url"], headers=headers, timeout=20)
            response.raise_for_status()
            print(f"OK | {source['name']} | HTTP {response.status_code} | final_url={response.url}")
            ok += 1
        except Exception as exc:
            failed += 1
            status = getattr(getattr(exc, "response", None), "status_code", None)
            print(f"FAIL | {source['name']} | HTTP={status or 'unknown'} | {type(exc).__name__}: {exc}")

    print(f"\nSUMMARY | total={total} | ok={ok} | failed={failed}")
    if failed:
        print("WARNING: 部分来源不可访问；请根据单源结果决定保留、替换或降级，不要直接删除全部来源。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
