from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch, Mock

from cache_manager import cleanup_daily_transients
from experiments.delivery import Submission, deliver_once
from experiments.v14.pipeline import prepare_preview, repair_results
from experiments.run import Flash
from test_stage3 import article

from experiments.v15.sources import NEWS, SearchFetch, academic, aossm_calendar, excerpt, on_site


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

    def test_verified_calendar_does_not_announce_too_early(self):
        with patch("requests.get") as get:
            get.return_value.status_code = 200
            get.return_value.text = "<h2>Annual Meeting 2027</h2><p><strong>July 7-10, 2027</strong></p>"
            rows, report = aossm_calendar("2026-11-26")
            self.assertEqual(rows, [])
            self.assertEqual(report["next_meeting"], "2027-07-07")
            rows, _ = aossm_calendar("2027-05-01")
            self.assertEqual(rows[0]["date"], "2027-07-07")


    def test_targeted_repair_can_only_change_failed_supported_result(self):
        source = [{"id": "good", "type": "medical", "title": "Trial",
                   "evidence": {"r": "The risk ratio was 0.80."}},
                  {"id": "bad", "type": "medical", "title": "Trial",
                   "evidence": {"r": "The risk ratio was 0.70."}}]
        proposals = {"good": {"result": "相对风险为0.80。", "refs": {"result": ["r"]}},
                     "bad": {"result": "相对风险为0.90。", "refs": {"result": ["r"]}}}
        provider = Mock(return_value=[{"id": "good", "result": "相对风险为0.60。"},
                                     {"id": "bad", "result": "相对风险为0.70。"}])
        provider.remaining = 200_000
        provider.reservation = Flash.reservation
        self.assertEqual(repair_results(provider, source, proposals, []), ["bad"])
        self.assertIn("0.80", proposals["good"]["result"])
        self.assertIn("0.70", proposals["bad"]["result"])
