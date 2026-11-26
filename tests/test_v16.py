from copy import deepcopy
import unittest

from experiments.v16.compare import audit_leads, parse_evidence
from experiments.v16.editorial import consistency, expand_abbreviations, prefer_variety, routine_event


class FinalEditorialTests(unittest.TestCase):
    def test_routine_meetings_do_not_take_world_slot(self):
        self.assertTrue(routine_event({"lane": "world", "title": "语言文化国际研讨会在埃塞俄比亚举行"}))
        self.assertFalse(routine_event({"lane": "world", "title": "国际研讨会正式发布全国医疗政策"}))
        self.assertFalse(routine_event({"lane": "medical", "title": "学术研讨会"}))

    def test_design_uses_reported_sample_and_keeps_english_quote(self):
        card = {"title": "External validation: systematic review", "evidence": "RESULTS: 16 studies met eligibility."}
        item = {"lane": "medical", "result": "AUC 0.70。结果段原文短引 “AUC 0.70”"}
        consistency(item, card)
        expand_abbreviations(item)
        self.assertIn("16项研究", item["design"])
        self.assertEqual(item["result"], "曲线下面积（AUC） 0.70。结果段原文短引 “AUC 0.70”")

    def test_rehab_rr_is_not_a_risk_ratio(self):
        item = {"result": "RR 和 MAT 的排名较高。"}
        expand_abbreviations(item)
        self.assertEqual(item["result"], "RR 和 MAT 的排名较高。")

    def test_variety_only_when_the_original_study_is_comparable(self):
        cards = [{"id": str(i), "title": "Systematic review"} for i in range(4)] + [
            {"id": "4", "title": "Multicentre external validation"}]
        scores = {str(i): {"weighted": 80} for i in range(5)}
        self.assertEqual(prefer_variety(cards, scores)[-1]["id"], "4")
        scores["4"]["weighted"] = 60
        self.assertEqual(prefer_variety(cards, scores)[-1]["id"], "3")

    def test_primary_source_is_required_and_target_is_not_incremental(self):
        target = {"id": "43058301", "pmid": "43058301", "title": "Hip fracture",
                  "url": "https://pubmed.ncbi.nlm.nih.gov/43058301/", "date": "2026-11-10",
                  "lane": "medical", "evidence": "A CT-based deep learning cohort for proximal femoral fractures."}
        other = {**deepcopy(target), "id": "43058302", "pmid": "43058302", "title": "Independent CT hip fracture study",
                 "url": "https://pubmed.ncbi.nlm.nih.gov/43058302/"}
        admitted, trace = audit_leads(parse_evidence('{"items":[{"pmid":"43058301"},{"pmid":"43058302"},{"pmid":"43058303"}]}'),
                                      target, "2026-11-26", verify=lambda lead: {"43058301": target, "43058302": other}.get(lead["pmid"]))
        self.assertEqual(len(admitted), 1)
        self.assertEqual([row["status"] for row in trace],
                         ["duplicate_or_target", "verified_and_relevant", "unverified"])
