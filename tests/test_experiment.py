import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from experiments.run import Flash, compare, eligible, normalize, peak_cost, read_dispatch

FIXTURE = json.loads(Path("experiments/fixtures/2026-11-26.json").read_text())
REF = "refs/heads/upgrade/news-pipeline-v1"


class ExperimentTest(unittest.TestCase):
    def test_fixed_and_holdout_filters_reject_unverifiable_old_duplicates(self):
        for split, identifiers in [
            ("development", {"m1", "m2", "m3", "n1", "n2", "n3"}),
            ("holdout", {"h1"}),
        ]:
            rows = eligible(FIXTURE[split], FIXTURE["as_of"])
            self.assertEqual({r["id"] for r in rows}, identifiers)

    def test_mock_comparison_uses_one_request_and_enforces_section_caps(self):
        ask = Mock(return_value=[{"id": i} for i in ("m2", "m2", "m6", "n1", "n2", "n3")])
        rows = compare("candidate", FIXTURE["development"], FIXTURE["as_of"], ask)
        self.assertEqual([r["id"] for r in rows], ["m2", "n1", "n2", "n3"])
        self.assertEqual(ask.call_count, 1)
        self.assertIsNone(ask.call_args.args[2][0].get("url"))

    def test_empty_input_has_no_request(self):
        ask = Mock()
        self.assertEqual(compare("candidate", [], FIXTURE["as_of"], ask), [])
        ask.assert_not_called()
        with patch("experiments.run.api_json") as network:
            self.assertEqual(Flash("fixture", 40_000)("empty", "", []), [])
            network.assert_not_called()

    def test_failed_request_consumes_reservation_and_prevents_unfunded_retry(self):
        flash = Flash("fixture", 20_000)
        with patch("experiments.run.api_json", side_effect=RuntimeError("HTTP_429")) as network:
            with self.assertRaisesRegex(RuntimeError, "HTTP_429"):
                flash("test", "example", [{"id": "sample"}])
            with self.assertRaisesRegex(RuntimeError, "reservation exhausted"):
                flash("test", "example", [{"id": "sample"}])
        self.assertEqual(network.call_count, 1)

    def test_usage_requires_reported_counters_and_refuses_overrun(self):
        self.assertEqual(peak_cost({"prompt_tokens": 10, "completion_tokens": 3}, 20, 5), 44)
        for usage in ({}, {"prompt_tokens": 20, "completion_tokens": 6}):
            with self.assertRaises(RuntimeError):
                peak_cost(usage, 20, 5)

    def test_dispatch_requires_new_unique_bounded_manifest_and_reopens(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "experiments"
            directory = root / "dispatch"
            directory.mkdir(parents=True)
            file = directory / "trial.json"
            file.write_text('{"phase":"candidate","reserve_micro_cny":200000}')
            event = {"after": "test"}
            added = ["experiments/dispatch/trial.json"]
            self.assertEqual(read_dispatch(event, REF, "test", added, root)[0], "trial")
            self.assertEqual(read_dispatch(event, REF, "test", added, root)[1]["reserve_micro_cny"], 200000)
            for bad_ref, bad_event in [(REF, {**event, "forced": True}), ("refs/heads/main", event)]:
                with self.assertRaises(RuntimeError):
                    read_dispatch(bad_event, bad_ref, "test", added, root)
            file.write_text('{"phase":"candidate","reserve_micro_cny":200001}')
            with self.assertRaises(RuntimeError):
                read_dispatch(event, REF, "test", added, root)

    def test_normalizer_rejects_unknown_ids_and_caps_policy(self):
        cards = FIXTURE["development"]
        output = normalize([{"id": "missing"}, {"id": "n1"}, {"id": "n5"}], cards)
        self.assertEqual([r["id"] for r in output], ["n1"])


class PreviewTest(unittest.TestCase):
    def test_preview_uses_source_metadata_and_never_renders_unverified_items(self):
        from experiments.preview import render_messages
        items = [{"id": "m1", "date": "2099-01-01", "url": "https://example.org/fake"},
                 {"id": "m6", "headline": "厂商广告"}, {"id": "n1", "summary": "已落地"}]
        professional, extended = render_messages(items, FIXTURE["development"], FIXTURE["as_of"])
        self.assertIn("43058301", professional)
        self.assertIn("样本量与结果未核实", professional)
        self.assertNotIn("2099", professional)
        self.assertNotIn("厂商广告", professional)
        self.assertIn("国家医保局", extended)
        self.assertIn("本次候选", extended)


class FreeDiscoveryTest(unittest.TestCase):
    def test_academic_card_needs_primary_metadata_and_matching_scope(self):
        from experiments.discover import medical_card
        row = {"id": "a", "title": "Fracture prognosis by machine learning",
               "pubmed_record": {"pmid": "100", "journal": "Journal",
                                 "online_date": "2026-11-25", "publication_date": "2027 Jan",
                                 "abstract": "Patient fracture data.",
                                 "affiliations": ["Beijing, China", "Tokyo, Japan"]}}
        card = medical_card(row)
        self.assertEqual(card["date"], "2026-11-25")
        self.assertIn("国际合作", card["region"])
        self.assertIsNone(medical_card({**row, "title": "Machine learning for battery materials"}))
        self.assertIsNone(medical_card({**row, "pubmed_record": {**row["pubmed_record"],
                                                               "abstract": ""}}))

    def test_old_and_unverified_policy_articles_are_excluded(self):
        from datetime import datetime
        from experiments.discover import CST, news_card
        now = datetime(2026, 11, 26, 19, tzinfo=CST)
        row = {"id": "n", "url": "https://www.nhsa.gov.cn/news", "title": "医保公告",
               "source": "国家医保局", "published_at": "2026-11-25T12:00:00+08:00",
               "evidence_status": "detail_fetched", "summary": "正文"}
        self.assertIsNotNone(news_card(row, "policy", now))
        self.assertIsNone(news_card({**row, "evidence_status": "listing_only"}, "policy", now))
        self.assertIsNone(news_card({**row, "published_at": "2026-11-23"}, "policy", now))


    def test_fallback_requires_explicit_authority_and_marks_secondary_source(self):
        from datetime import datetime
        from experiments.discover import CST, secondary_policy_cards
        from experiments.preview import render_messages
        now = datetime(2026, 11, 26, 19, tzinfo=CST)
        row = {"id": "n", "url": "https://www.chinanews.com.cn/jk/example",
               "title": "连续用药管理功能上线", "source": "中新网健康",
               "published_at": "2026-11-26T08:00:00+08:00",
               "summary": "据国家医保局微信公众号消息，新功能上线。"}
        cards = list(secondary_policy_cards([row, {**row, "summary": "网上传闻"}], now))
        self.assertEqual(len(cards), 1)
        _, extended = render_messages([{"id": cards[0]["id"]}], cards, "2026-11-26")
        self.assertIn("官方原文未核验", extended)
        self.assertIn("报道来源", extended)


class DeliveryTest(unittest.TestCase):
    def test_preview_never_sends_and_partial_delivery_survives_restart(self):
        from experiments.delivery import deliver_once
        messages = {"professional": "专业", "extended": "拓展"}
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "delivery.json"
            sender = Mock(side_effect=[True, TimeoutError()])
            self.assertEqual(deliver_once(state, "2026-11-26", messages, sender),
                             {"status": "preview"})
            sender.assert_not_called()
            outcome = deliver_once(state, "2026-11-26", messages, sender, approved=True)
            self.assertEqual(outcome, {"professional": "accepted", "extended": "submission_unknown"})
            sender.assert_called()
            restarted = Mock()
            self.assertEqual(deliver_once(state, "2026-11-26", messages, restarted, approved=True),
                             outcome)
            restarted.assert_not_called()

    def test_changed_content_cannot_reuse_delivery_identifier(self):
        from experiments.delivery import deliver_once
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "delivery.json"
            sender = Mock(return_value=True)
            messages = {"professional": "专业", "extended": "拓展"}
            deliver_once(state, "batch", messages, sender, approved=True)
            with self.assertRaises(ValueError):
                deliver_once(state, "batch", {**messages, "extended": "不同"}, sender, approved=True)
            self.assertEqual(sender.call_count, 2)


class FullReplayTest(unittest.TestCase):
    def test_offline_normal_and_degraded_runs_render_two_messages(self):
        from experiments.preview import render_messages
        for name, ids in [
            ("normal", ("m1", "m2", "n1", "n2")),
            ("degraded", ()),
        ]:
            with self.subTest(mode=name):
                cards = FIXTURE["development"] if ids else [FIXTURE["development"][5]]
                ask = Mock(return_value=[{"id": key} for key in ids])
                selected = compare("grounded", cards, FIXTURE["as_of"], ask)
                professional, extended = render_messages(selected, cards, FIXTURE["as_of"])
                self.assertTrue(professional.startswith("# 专业情报"))
                self.assertTrue(extended.startswith("# 拓展阅读"))
                if ids:
                    self.assertIn("PMID 43058301", professional)
                    self.assertIn("国家医保局", extended)
                else:
                    self.assertIn("暂无达到", professional)
                    self.assertIn("暂无达到", extended)
                    ask.assert_not_called()

    def test_reviewed_live_previews_are_reproducible(self):
        from experiments.preview import render_messages
        directory = Path("experiments/results")
        data = json.loads((directory / "2026-11-26-reviewed.json").read_text())
        rendered = render_messages(data["items"], data["sources"], data["as_of"])
        for file, body in zip(("professional-preview.md", "extended-preview.md"), rendered):
            self.assertEqual((directory / file).read_text(), body)


if __name__ == "__main__":
    unittest.main()
