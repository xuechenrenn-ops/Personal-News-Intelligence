import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from experiments.stage2.channels import SourceHealth, hints
from experiments.stage2.quality import audit_items, choose_pool, verify_fact


class StageTwoQualityTest(unittest.TestCase):
    def test_fails_unsupported_number_and_detects_or_as_risk(self):
        evidence = "In a retrospective cohort, the odds ratio (OR) was 0.42 in 92 patients."
        self.assertTrue(verify_fact("回顾性队列纳入92例，OR 为0.42。", evidence))
        self.assertFalse(verify_fact("收集93例", evidence))
        self.assertFalse(verify_fact("风险降低，OR=0.42", evidence))
        self.assertFalse(verify_fact("危险比（HR）为0.42", evidence))

    def test_fractional_p_values_are_not_confused_with_counts(self):
        self.assertTrue(verify_fact("结果 P＝0.003。", "The comparison had P=.003."))
        self.assertFalse(verify_fact("结果纳入3例。", "The comparison had P=.003."))
        self.assertTrue(verify_fact("结果为１２％。", "The result was 12%."))

    def test_drops_unsupported_result_and_strips_editor_urls(self):
        row = {"id": "paper", "lane": "medical", "evidence": "The study included 92 patients."}
        proposed = [{"id": "paper", "result": "纳入93例。", "refs": {"result": [0]}}]
        output, audit = audit_items(proposed, [row])
        self.assertEqual(output, [])
        self.assertFalse(audit[0]["retained"])
        proposed[0]["result"] = "原文纳入92例。"
        self.assertEqual(len(audit_items(proposed, [row])[0]), 1)
        self.assertEqual(audit_items(proposed, [row], [("paper", "result")])[0], [])
        self.assertFalse(verify_fact("查看 https://other.site", row["evidence"]))

    def test_ordinary_accident_does_not_fill_world_lane(self):
        row = {"id": "n1", "lane": "world", "date": "2026-11-26", "primary": True,
               "url": "https://example.com/1", "title": "Fuel tanker crashes"}
        self.assertEqual(choose_pool([row], "2026-11-26"), [])

    def test_cooldown_survives_reload_after_block(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "health.json"
            now = datetime.now(timezone.utc)
            health = SourceHealth(path, now)
            health.record("rss", False, True)
            health.save()
            reloaded = SourceHealth(path, now)
            self.assertTrue(reloaded.status("rss"))

    def test_search_hints_never_assigned_an_unverified_publication(self):
        row = {"url": "https://example.org/articles",
               "content": "DOI:10.2196/94936; PMID 43038553"}
        self.assertEqual(hints([row]), ({"43038553"}, {"10.2196/94936"}))

class AutomatedEditorialTest(unittest.TestCase):
    def test_rank_and_editor_share_bounded_identifiers_and_block_false_result(self):
        from unittest.mock import Mock
        from experiments.stage2.pipeline import run_editor
        card = {"id": "x", "lane": "medical", "title": "Orthopaedic recovery", "date": "2026-11-26",
                "evidence": "The cohort included 92 patients. No causal inference was possible."}
        news = {**card, "id": "n", "lane": "world"}
        provider = Mock(side_effect=[
            [{"id": "x"}, {"id": "unknown"}],
            [{"id": "x", "result": "纳入92例。", "refs": {"result": [0]}}],
            [{"id": "x", "field": "result", "reason": "artificial veto"}],
        ])
        _, output, audit, veto = run_editor(provider, "B", [card, news], review=True, use_ranker=True)
        self.assertEqual(output, [])
        self.assertFalse(audit[0]["retained"])
        self.assertEqual(veto, [("x", "result")])
        self.assertEqual([row["id"] for row in provider.call_args_list[1].args[2]], ["x"])
