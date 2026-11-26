from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch, Mock

from cache_manager import cleanup_daily_transients
from experiments.delivery import Submission, deliver_once
from experiments.v14.pipeline import prepare_preview
from test_stage3 import article

from experiments.v15.sources import NEWS, SearchFetch, academic, excerpt, on_site


class V15Tests(unittest.TestCase):
    def test_search_answer_is_a_lead_and_budget_counts_failed_attempt(self):
        def transport(url, headers, payload):
            if url.endswith("/fetch"):
                raise RuntimeError("HTTP_403")
            return {"results": [{"url": "https://pubmed.ncbi.nlm.nih.gov/43058301/"}]}
        tools = SearchFetch({}, 2, transport)
        self.assertEqual(academic(tools, "2026-11-26", lambda lead: None)[0], [])
        self.assertEqual(tools.fetch("https://www.bbc.co.uk/news/article", NEWS), "")
        self.assertEqual(tools.search("next"), [])
        self.assertEqual([c["status"] for c in tools.calls], ["billable", "HTTP_403"])

    def test_fetch_excludes_other_domains_and_sections_without_body(self):
        self.assertFalse(on_site("https://bbc.co.uk.example.org/news", NEWS))
        self.assertFalse(on_site("https://login@bbc.co.uk/news", NEWS))
        self.assertEqual(excerpt("# " + "heading " * 20, "heading"), "")

    def test_cross_date_replay_keeps_history_without_duplicate_then_expires(self):
        sender = Mock(side_effect=RuntimeError("Injected model outage"))
        paper = article(date="2026-11-26", source="Trial journal")
        with TemporaryDirectory() as temp:
            root = Path(temp)
            index = root / "index.json"
            first = prepare_preview(root / "first" / "output.json", sender, history_path=index,
                                    evaluation=("2026-11-26", [paper], [], {}))
            next_day = {**paper, "id": "202", "pmid": "202", "title": "ACL reconstruction and patient mobility",
                        "date": "2026-11-27", "url": "https://pubmed.ncbi.nlm.nih.gov/202/"}
            second = prepare_preview(root / "next" / "output.json", sender, history_path=index,
                                     evaluation=("2026-11-27", [paper, next_day], [], {}))
            self.assertEqual(first["history"], {"restored": 0, "written": 1})
            self.assertEqual(second["history"], {"restored": 1, "written": 1})
            self.assertEqual([row["id"] for row in second["selected"]], ["202"])

    def test_definite_rejection_retries_once_and_cleans_after_both_accepted(self):
        sender = Mock(side_effect=[True, Submission.RETRYABLE_REJECTION, True])
        messages = {"professional": "first", "extended": "second"}
        with TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "delivery.json"
            transient = root / "transient"
            transient.mkdir()
            (transient / "draft.txt").write_text("保留待重试")
            with patch("cache_manager.TRANSIENT_DIR", transient):
                deliver_once(path, "2026-11-26", messages, sender, approved=True, cleanup=cleanup_daily_transients)
                self.assertTrue(transient.exists())
                deliver_once(path, "2026-11-26", messages, sender, approved=True, cleanup=cleanup_daily_transients)
            self.assertFalse(transient.exists())
            self.assertTrue(path.exists())
            self.assertEqual([c.args[0] for c in sender.call_args_list], ["professional", "extended", "extended"])
