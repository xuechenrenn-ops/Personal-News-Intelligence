import unittest

from news_push import (
    _medical_candidate_lane,
    annotate_policy_source,
    balance_medical_candidates,
    filter_nsfc_candidates,\n    filter_general_news_candidates,
)


class MedicalCandidateBalanceTests(unittest.TestCase):
    def test_detects_four_research_lanes(self):
        self.assertEqual(_medical_candidate_lane({
            "title": "Anterior cruciate ligament reconstruction outcomes",
            "source_category": "骨科临床研究",
        }), "骨科临床")
        self.assertEqual(_medical_candidate_lane({
            "title": "Machine learning prediction after ACL reconstruction",
            "source_category": "骨科AI研究",
        }), "骨科AI")
        self.assertEqual(_medical_candidate_lane({
            "title": "Large language models for clinical decision support",
            "source_category": "医疗AI交叉研究",
        }), "其他医疗AI")
        self.assertEqual(_medical_candidate_lane({
            "title": "A randomized trial of a new analgesic",
            "source_category": "综合医学",
        }), "其他医学研究")

    def test_round_robin_preserves_lane_representation_and_deduplicates(self):
        candidates = []
        for i in range(5):
            candidates.append({"id": f"c{i}", "url": f"https://example.org/c{i}",
                               "title": "ACL reconstruction outcomes", "source_category": "骨科临床研究"})
            candidates.append({"id": f"a{i}", "url": f"https://example.org/a{i}",
                               "title": "AI model for ACL injury prediction", "source_category": "骨科AI研究"})
            candidates.append({"id": f"m{i}", "url": f"https://example.org/m{i}",
                               "title": "Machine learning for clinical diagnosis", "source_category": "医疗AI交叉研究"})
            candidates.append({"id": f"o{i}", "url": f"https://example.org/o{i}",
                               "title": "A clinical cohort study", "source_category": "综合医学"})
        candidates.append({"id": "duplicate", "url": "https://example.org/c0",
                           "title": "ACL duplicate", "source_category": "骨科临床研究"})
        selected = balance_medical_candidates(candidates, max_per_lane=2, max_total=8)
        self.assertEqual(len(selected), 8)
        self.assertEqual(len({item["url"] for item in selected}), 8)
        lanes = [_medical_candidate_lane(item) for item in selected]
        self.assertEqual({lane: lanes.count(lane) for lane in set(lanes)}, {
            "骨科临床": 2, "骨科AI": 2, "其他医疗AI": 2, "其他医学研究": 2
        })



class GeneralNewsQualityTests(unittest.TestCase):
    def test_filters_low_relevance_school_procurement_and_keeps_relevant_news(self):
        candidates = [
            {"id": "school", "title": "江西集中整治教辅材料、校服订购和校园餐问题",
             "url": "https://example.org/school", "summary": "地方专项整治工作"},
            {"id": "trade", "title": "美国公布对全球贸易伙伴的新关税计划",
             "url": "https://example.org/trade", "summary": "可能影响国际贸易和供应链"},
            {"id": "health", "title": "国家医保局发布基层医疗卫生服务政策",
             "url": "https://example.org/health", "summary": "涉及医保支付与医疗服务"},
        ]
        result = filter_general_news_candidates(candidates)
        self.assertEqual({item["id"] for item in result}, {"trade", "health"})

    def test_collapses_duplicate_headlines_across_sources(self):
        candidates = [
            {"id": "a", "title": "美国公布全球贸易伙伴新关税计划",
             "url": "https://example.org/a", "summary": "美国贸易政策"},
            {"id": "b", "title": "美国公布全球贸易伙伴新关税计划",
             "url": "https://other.org/b", "summary": "同一事件不同来源"},
        ]
        result = filter_general_news_candidates(candidates)
        self.assertEqual(len(result), 1)


class NSFCQualityGateTests(unittest.TestCase):
    def test_only_relevant_official_funding_notices_survive(self):
        candidates = [
            {"id": "guide", "title": "2027年度国家自然科学基金项目指南", "url": "https://www.nsfc.gov.cn/guide"},
            {"id": "suggest", "title": "征集2027年度项目指南建议", "url": "https://nsfc.gov.cn/suggest"},
            {"id": "funded", "title": "国家自然科学基金已资助项目名单", "url": "https://www.nsfc.gov.cn/list"},
            {"id": "hire", "title": "医学科学部招聘运动系统流动项目主任", "url": "https://www.nsfc.gov.cn/jobs"},
            {"id": "external", "title": "国家自然科学基金项目指南", "url": "https://example.org/nsfc"},
            {"id": "unrelated", "title": "基金委工作会议召开", "url": "https://www.nsfc.gov.cn/meeting"},
        ]
        result = filter_nsfc_candidates(candidates)
        self.assertEqual([item["id"] for item in result], ["guide", "suggest", "funded"])
        self.assertEqual([item["nsfc_type"] for item in result], [
            "正式指南/项目通知", "指南建议征集", "已资助项目"
        ])

    def test_third_party_policy_is_explicitly_flagged(self):
        third_party = {"headline": "医保支持基层医疗卫生服务政策解读",
                       "_source": {"url": "https://example.org/policy"}}
        official = {"headline": "国家医保局发布医保政策",
                    "_source": {"url": "https://www.nhsa.gov.cn/policy"}}
        result = annotate_policy_source([third_party, official])
        self.assertIn("尚未核验官方原文", result[0]["verification_note"])
        self.assertNotIn("verification_note", result[1])


if __name__ == "__main__":
    unittest.main()
