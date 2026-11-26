"""Run the existing free collectors independently of model and delivery credentials."""
from concurrent.futures import ThreadPoolExecutor
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

from news_push import (GENERAL_FEEDS, fetch_candidates, fetch_pubmed_candidates,
                       balance_medical_candidates, filter_general_news_candidates)


def main(output):
    print(f"RUN_STARTED={datetime.now(timezone.utc).isoformat()}", flush=True)
    feeds = [feed for feed in GENERAL_FEEDS if feed[0] in {"中国新闻网-即时", "BBC-World", "Al Jazeera"}]
    with ThreadPoolExecutor(max_workers=4) as pool:
        academic = pool.submit(fetch_pubmed_candidates)
        news = [pool.submit(fetch_candidates, [feed], f"G{idx}-", 48)
                for idx, feed in enumerate(feeds)]
    medical_raw = academic.result()
    news_raw = [item for future in news for item in future.result()]
    medical = balance_medical_candidates(
        [item for item in medical_raw if (item.get("pubmed_record") or {}).get("abstract")],
        max_per_lane=4, max_total=12,
    )
    general = filter_general_news_candidates(news_raw, max_candidates=12)
    data = {"academic_total": len(medical_raw), "news_total": len(news_raw), "candidates": [{
        "title": item["title"], "source": item["source"], "url": item["url"],
        "published_at": item["published_at"]
    } for item in medical + general]}
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"FREE_DISCOVERY academic={len(medical_raw)} news={len(news_raw)} "
          f"academic_shortlist={len(medical)} news_shortlist={len(general)}", flush=True)
    print("PAID_REQUESTS=0 TAVILY_CREDITS=0 WECHAT_REQUESTS=0", flush=True)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
