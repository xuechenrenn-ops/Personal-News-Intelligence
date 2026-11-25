import unittest
from datetime import datetime, timedelta, timezone

import news_push


class NewsDeduplicationTests(unittest.TestCase):
    def test_records_older_than_30_days_are_pruned(self):
        now = datetime.now(timezone.utc)
        records = [
            {"url": "https://example.org/recent", "last_seen_at": (now - timedelta(days=29)).isoformat(), "pmid": "111"},
            {"url": "https://example.org/old", "last_seen_at": (now - timedelta(days=31)).isoformat(), "pmid": "222"},
        ]
        kept = news_push._normalize_seen_records({"records": records}, now=now)
        self.assertEqual([item["url"] for item in kept], ["https://example.org/recent"])
        self.assertEqual(kept[0]["pmid"], "111")

    def test_legacy_seen_urls_migrate_with_30_day_timestamp(self):
        now = datetime.now(timezone.utc)
        old_update = (now - timedelta(days=31)).isoformat()
        kept = news_push._normalize_seen_records(
            {"updated_at": old_update, "seen_urls": ["https://example.org/legacy"]},
            now=now,
        )
        self.assertEqual(kept, [])

    def test_url_pmid_and_doi_all_prevent_repeat_selection(self):
        candidates = [
            {"url": "https://example.org/url"},
            {"url": "https://example.org/pmid", "pubmed_record": {"pmid": "123"}},
            {"url": "https://example.org/doi", "pubmed_record": {"doi": "10.1000/ABC"}},
            {"url": "https://example.org/new"},
        ]
        filtered = news_push.remove_previously_pushed(
            candidates,
            {"https://example.org/url"},
            {"123"},
            {"10.1000/abc"},
        )
        self.assertEqual([item["url"] for item in filtered], ["https://example.org/new"])


if __name__ == "__main__":
    unittest.main()

# Executed by the branch validation workflow.
