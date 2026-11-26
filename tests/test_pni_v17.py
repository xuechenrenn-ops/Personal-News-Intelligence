from datetime import date
from unittest import TestCase
from unittest.mock import Mock

from pni.editorial import clean_repeated_terms, shoulder_trial_result
from pni.presentation import document, measure, plan
from pni.production import deliver_html

DAY = date(2026, 11, 26)
A = "### 研究{}\n**原文主要结果**　{}。\n[来源](https://example.org/{})\n"
E = "## 国内医疗民生\n### 医疗政策\n完整政策正文。\n[来源](https://example.org/policy)"


class V17Tests(TestCase):
    def test_merged_complete_and_sanitized(self):
        result = plan(A.format(1, "<script>abc</script>", "a"), E, DAY)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0].articles), 2)
        self.assertIn("&lt;script&gt;", result[0].html)
        self.assertIn("星期四", result[0].html)
        self.assertEqual(result[0].html.count("今日导读"), 1)
        self.assertIn("viewport", document(result[0].html))

    def test_split_by_section_then_article_without_cutting(self):
        medium = A.format(1, "甲" * 800, "a")
        other = A.format(2, "乙" * 800, "b")
        medium_len = measure(plan(medium, "", DAY)[0].html)["utf8_bytes"]
        with self.assertRaises(ValueError):
            plan(medium, "", DAY, limit=1000)
        items = plan(medium + other, E, DAY, limit=medium_len + 120)
        self.assertEqual([len(row.articles) for row in items], [1, 1, 1])
        self.assertIn("甲" * 800, items[0].html)
        self.assertIn("乙" * 800, items[1].html)
        self.assertTrue(all(measure(row.html)["utf8_bytes"] <= medium_len + 120 for row in items))

    def test_split_sections_only(self):
        pro = A.format(1, "甲" * 300, "a")
        ext = "## 国内医疗民生\n" + A.format(2, "乙" * 300, "b")
        one = measure(plan(pro, "", DAY)[0].html)["utf8_bytes"]
        rows = plan(pro, ext, DAY, limit=one + 500)
        self.assertEqual(len(rows), 2)

    def test_low_impact_relabel_preserves_text(self):
        ext = "## 国内外重大事件\n### 市场监管总局回应食品保质期标注问题\n完整报道正文。"
        item = plan("", ext, DAY)[0]
        self.assertEqual(item.articles[0].section, "民生关注")
        self.assertIn("完整报道正文", item.html)

    def test_original_numbers_required(self):
        evidence = ("At 3 months, the CBPT group had lower activity-related VAS scores than the control group "
                    "(2.8 ± 2.0 vs. 4.4 ± 1.5; p = 0.002), with between-group differences also observed for CMS "
                    "(p = 0.007). At 6 months, neither co-primary outcome differed significantly between groups")
        self.assertIsNone(shoulder_trial_result("43085541", "overall result wasn't better"))
        self.assertIsNone(shoulder_trial_result("other", evidence))
        self.assertIn("2.8±2.0 对 4.4±1.5", shoulder_trial_result("43085541", evidence))
        self.assertEqual(clean_repeated_terms("Constant-Murley评分（Constant–Murley肩关节评分（CMS））"),
                         "Constant–Murley肩关节评分（CMS）")

    def test_rejected_part_does_not_record_history_or_retry_unknown(self):
        rows = plan(A.format(1, "甲" * 800, "a"), E, DAY, limit=6500)
        self.assertEqual(len(rows), 2)
        state = Mock(data={})
        sender = Mock()
        sender.send.side_effect = [({"status": "accepted"}, None), ({"status": "submission_unknown"}, None)]
        batch = {"mode": "daily", "lanes": {}}
        records = [{"url": "https://example.org/a", "title": "a"}, {"url": "https://example.org/policy", "title": "b"}]
        deliver_html(state, batch, DAY.isoformat(), rows, records, [], sender)
        self.assertEqual(batch["state"], "needs_review")
        self.assertEqual(len(state.data["history"]), 1)
        self.assertEqual(sender.send.call_count, 2)
        deliver_html(state, batch, DAY.isoformat(), rows, records, [], sender)
        self.assertEqual(sender.send.call_count, 2)

    def test_confirmed_failure_retries_once_with_same_html(self):
        rows = plan(A.format(1, "结果", "a"), "", DAY)
        state = Mock(data={})
        sender = Mock()
        sender.send.side_effect = [({"status": "accepted", "receipt": "first"}, "first-receipt"),
                                   ({"status": "submission_unknown"}, None)]
        sender.lookup.return_value = {"status": "delivery_failed", "verification": "provider_confirmed"}
        batch = {"mode": "daily", "lanes": {}}
        deliver_html(state, batch, DAY.isoformat(), rows, [], [], sender)
        part = batch["lanes"]["part-1"]
        self.assertEqual(part["status"], "submission_unknown")
        self.assertEqual(part["attempt"], 2)
        self.assertNotIn("receipt", part)
        self.assertNotIn("verification", part)
        self.assertEqual(sender.send.call_count, 2)
        self.assertEqual(sender.send.call_args_list[0], sender.send.call_args_list[1])
        self.assertEqual(sender.send.call_args.kwargs, {"template": "html"})
