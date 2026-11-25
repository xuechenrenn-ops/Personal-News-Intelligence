import json
import unittest
from datetime import date
from pathlib import Path

from meeting_tracker import build_meeting_brief


ROOT = Path(__file__).resolve().parents[1]


class PipelineConfigTests(unittest.TestCase):
    def test_source_registry_has_required_sections(self):
        data = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
        self.assertTrue(data["rss"]["medical"])
        self.assertTrue(data["rss"]["general"])
        self.assertTrue(data["official_pages"])
        for section in ("medical", "general"):
            for source in data["rss"][section]:
                self.assertTrue(source["url"].startswith(("http://", "https://")))
                self.assertTrue(source["label"])

    def test_meeting_dates_are_valid(self):
        data = json.loads((ROOT / "meeting_calendar.json").read_text(encoding="utf-8"))
        self.assertTrue(data["events"])
        for event in data["events"]:
            start = date.fromisoformat(event["start_date"])
            end = date.fromisoformat(event["end_date"])
            self.assertLessEqual(start, end)
            self.assertTrue(event["url"].startswith("https://"))

    def test_meeting_reminder_window(self):
        output = build_meeting_brief(today=date(2027, 2, 25))
        self.assertIn("AAOS Annual Meeting 2027", output)
        self.assertIn("重要提醒", output)
        self.assertNotIn("AOSSM Annual Meeting 2027", output)


if __name__ == "__main__":
    unittest.main()
