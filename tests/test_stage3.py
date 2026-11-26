import json
from pathlib import Path
import tempfile
import unittest

from experiments.run import read_dispatch
from experiments.stage3.evidence import choose_result, medical_digest, paper_card
from experiments.stage3.pipeline import localized_titles, public_digest
from research_scope import research_lane


def article(**changes):
    return {
        "id": "10000001", "pmid": "10000001", "url": "https://pubmed.ncbi.nlm.nih.gov/10000001/",
        "lane": "medical", "title": "ACL reconstruction after sports injury", "date": "2026-11-18",
        "primary": True, "evidence": ("METHODS: We enrolled 80 patients. This was a randomized trial. "
            "RESULTS: The intervention improved knee function at six months (RR 1.25, 95% CI 1.03 to 1.52). "
            "CONCLUSIONS: Further research is warranted."),
        **changes,
    }


class Stage3Tests(unittest.TestCase):
    def test_acl_osteotomy_and_mesh_reach_evidence_screen_without_dental_leak(self):
        self.assertEqual(research_lane("Neural network for high tibial osteotomy"), "ortho-ai")
        self.assertEqual(research_lane("Return to direction changes following ACLR"), "clinical")
        self.assertEqual(research_lane("Functional recovery at one year", ["Osteotomy"]), "clinical")
        self.assertIsNone(research_lane("Orthodontic osteotomy"))

    def test_brief_quotes_original_statistic_and_does_not_invent_sample(self):
        selected, trace = paper_card(article())
        self.assertEqual(trace["status"], "eligible")
        self.assertIn("80", selected["design"])
        self.assertIn("RR 1.25, 95% CI 1.03 to 1.52", selected["result"])
        self.assertNotIn("危险比", selected["result"])
        no_n = article(evidence=article()["evidence"].replace("We enrolled 80 patients. ", ""))
        self.assertIn("总样本量未", paper_card(no_n)[0]["design"])

    def test_unverified_hypothesis_and_partial_result_stay_out_with_reason(self):
        self.assertEqual(paper_card(article(primary=False))[1]["status"], "missing_primary_identifier")
        self.assertEqual(paper_card(article(title="Trial protocol for ACL rehabilitation"))[1]["status"],
                         "protocol_or_non_outcome")
        self.assertEqual(paper_card(article(evidence="BACKGROUND: Improved patient function is important."))[1]["status"],
                         "no_complete_outcome_excerpt")

    def test_dedup_and_ancient_paper(self):
        one = article(doi="10.1234/example")
        duplicate = article(id="10000002", pmid="10000002", doi="10.1234/EXAMPLE")
        old = article(id="10000003", pmid="10000003", date="2026-08-26")
        selected, audit = medical_digest([one, duplicate, old], "2026-11-26")
        self.assertEqual(len(selected), 1)
        self.assertIn("outside_date_window", [row["status"] for row in audit])

    def test_significance_stays_with_the_intended_cohort(self):
        results = ("Mean age was 72.8 years, and 20 patients were male. "
                   "Major wound complications did not differ (16.1% vs 20.7%, P = 0.557). "
                   "Absolute risk reduction for revision surgery was 12.6%.")
        self.assertIn("did not differ", choose_result(results, "major wound complications"))

    def test_extract_cannot_break_parenthetical_subgroups(self):
        result = choose_result("DR repair reduced full-thickness retear (40 studies; RR, 0.69 "
                               "[95% CI, 0.55-0.86]; NNT, 21) and partial-thickness retear "
                               "(12 studies; RR, 0.57 [0.43-0.75]; NNT, 10).")
        self.assertIn("full-thickness retear", result)
        self.assertIn("0.69", result)
        self.assertNotIn("partial-thickness", result)

    def test_tariff_reaction_is_deduplicated_across_publishers(self):
        cards = [
            {"id": "a", "lane": "world", "primary": True, "title": "US announces new tariffs on China",
             "source": "BBC News"},
            {"id": "b", "lane": "world", "primary": True, "title": "Brazil blasts new US tariff proposals",
             "source": "Al Jazeera"},
            {"id": "c", "lane": "world", "primary": True, "title": "议会通过重组央行决策委员会的法案",
             "source": "中国新闻网"},
        ]
        selected = {row["id"] for row in public_digest(cards)}
        self.assertIn("c", selected)
        self.assertEqual(len(selected & {"a", "b"}), 1)

    def test_translation_cannot_change_the_body_part_and_design(self):
        row = article(title="Rotator cuff repair: an external validation")
        def unsafe(*args, **kwargs):
            return [{"id": row["id"], "title": "前交叉韧带修复随机试验"}]
        def accepted(*args, **kwargs):
            return [{"id": row["id"], "title": "肩袖修复的外部验证研究"}]
        self.assertEqual(localized_titles(unsafe, [row], [row]), {})
        self.assertEqual(localized_titles(accepted, [row], [row]), {row["id"]: "肩袖修复的外部验证研究"})

    def test_budget_counts_rounds_not_number_of_free_launches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "experiments"
            dispatch = root / "dispatch"
            dispatch.mkdir(parents=True)
            for n in range(22):
                (dispatch / f"free-{n}.json").write_text('{"phase":"s3-preview","reserve_micro_cny":0}')
            plan = {"phase":"s3-benchmark", "round":6, "reserve_micro_cny":0}
            latest = dispatch / "new.json"
            latest.write_text(json.dumps(plan))
            ref = "refs/heads/upgrade/news-pipeline-v1"
            added = ["experiments/dispatch/new.json"]
            self.assertEqual(read_dispatch({"after":"tip"}, ref, "tip", added, root)[1], plan)
            latest.write_text(json.dumps({**plan,"round":31}))
            with self.assertRaises(RuntimeError):
                read_dispatch({"after":"tip"}, ref, "tip", added, root)
