import json
import unittest
import tempfile
from datetime import date
from pathlib import Path

from meeting_tracker import (build_meeting_brief, get_due_meeting_reminders, mark_meeting_reminders_sent)


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

    def test_separate_meeting_alert_is_sent_once(self):
        calendar = {
            "urgent_days": 7,
            "events": [{
                "id": "TEST-MEETING", "name": "Test meeting",
                "start_date": "2027-03-03", "end_date": "2027-03-04",
                "url": "https://example.org/meeting"
            }]
        }
        with tempfile.TemporaryDirectory() as folder:
            calendar_path = Path(folder) / "calendar.json"
            state_path = Path(folder) / "state.json"
            calendar_path.write_text(json.dumps(calendar), encoding="utf-8")
            due = get_due_meeting_reminders(
                calendar_path=str(calendar_path), state_path=str(state_path),
                today=date(2027, 2, 28)
            )
            self.assertEqual(len(due), 1)
            mark_meeting_reminders_sent(due, state_path=str(state_path))
            due_again = get_due_meeting_reminders(
                calendar_path=str(calendar_path), state_path=str(state_path),
                today=date(2027, 2, 28)
            )
            self.assertEqual(due_again, [])


if __name__ == "__main__":
    unittest.main()
