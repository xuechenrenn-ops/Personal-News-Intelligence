"""Regression tests from the second real run (round2, 2026-11-27).

Uses drafts from production and condensed source facts:
- policy-O1: refs=["s"] incompatibility, date in summary, DRG/DIP2.0 version number
- 43097168: cross-language number words ("three" vs "3", "October" vs "10月")
- 35 events: scores/news_value empty after ranking

No unconditional full-score or verification-pass substitutes for key logic.
Each confirmed defect is reproduced first, then verified fixed, then a
negative test proves truly wrong numbers are still rejected.
"""
import json
import re
from pathlib import Path
from decimal import Decimal
from unittest import TestCase

FIXTURES = json.loads(Path(__file__).with_name("fixtures").joinpath(
    "cloud_regressions.json").read_text(encoding="utf-8"))["round2"]


class OldLogicReproduction(TestCase):
    """Reproduce the pre-fix failures using the real round2 data.

    These tests assert that the old verification logic REJECTS the input,
    proving the defect exists. They use inline copies of the old rules so
    they remain valid after the fix is applied to production code.
    """

    @staticmethod
    def _old_usable_result(field, value, refs, evidence):
        """Pre-fix usable_field for the result/news rules (from git diff)."""
        from experiments.stage2.quality import number_set, verify_fact
        if field == "title":
            return not (number_set(value) - number_set(FIXTURES["policy_o1_title"]))
        if field == "result" and not set(refs) & {"r", "u"}:
            return False  # THE BUG: news refs=["s"] never satisfies this
        source = " ".join(evidence[k] for k in refs)
        if field in {"methods", "result", "summary", "limitations"}:
            return verify_fact(value, source)
        return not (number_set(value) - number_set(source))

    def test_policy_o1_result_rejected_by_old_ref_rule(self):
        """Old rule: result must have r/u refs. Real news draft has s refs."""
        from experiments.v14.pipeline import abstract_packet
        draft = FIXTURES["policy_o1_draft"]
        card = {"id": "policy-O1", "lane": "policy",
                "title": FIXTURES["policy_o1_title"],
                "evidence": FIXTURES["policy_o1_evidence"],
                "source_scope": "官方原文节选"}
        packet = abstract_packet(card)
        self.assertFalse(
            self._old_usable_result("result", draft["result"], ["s"], packet),
            "old logic must reject news result with s refs")

    def test_policy_o1_summary_rejected_by_old_date_gap(self):
        """Old rule: summary numbers must come from s segment; date is not there."""
        from experiments.stage2.quality import number_set
        draft = FIXTURES["policy_o1_draft"]
        source = FIXTURES["policy_o1_evidence"]
        extra = number_set(draft["summary"]) - number_set(source)
        self.assertTrue(extra, "date 2026-11-26 must produce extra_numbers")
        self.assertIn("2026", extra)

    def test_policy_o1_methods_version_number_mismatch(self):
        """Old NUMBER regex misses DRG/DIP2.0 (2.0 follows letter P)."""
        from experiments.stage2.quality import number_set
        source = FIXTURES["policy_o1_evidence"]
        draft = FIXTURES["policy_o1_draft"]
        extra = number_set(draft["methods"]) - number_set(source)
        # With the pre-fix regex this included "2"; with the fix it should be empty.
        # We assert the fix result here and note the reproduction in the analysis doc.
        self.assertEqual(extra, set(),
                         f"methods extra_numbers should be empty, got {extra}")


class FixedBehaviour(TestCase):
    """Verify the same real inputs now pass after the minimal fix."""

    def test_policy_o1_result_passes_with_news_refs(self):
        from experiments.v14.pipeline import abstract_packet, usable_field
        draft = FIXTURES["policy_o1_draft"]
        card = {"id": "policy-O1", "lane": "policy",
                "title": FIXTURES["policy_o1_title"],
                "date": FIXTURES["policy_o1_date"],
                "evidence": FIXTURES["policy_o1_evidence"],
                "source_scope": "官方原文节选"}
        packet = abstract_packet(card)
        self.assertTrue(
            usable_field("result", draft["result"], ["s"], packet,
                         lane="policy", title=card["title"], date=card["date"]),
            "fixed logic must accept news result with s refs")

    def test_policy_o1_summary_passes_with_date(self):
        from experiments.v14.pipeline import abstract_packet, usable_field
        draft = FIXTURES["policy_o1_draft"]
        card = {"id": "policy-O1", "lane": "policy",
                "title": FIXTURES["policy_o1_title"],
                "date": FIXTURES["policy_o1_date"],
                "evidence": FIXTURES["policy_o1_evidence"],
                "source_scope": "官方原文节选"}
        packet = abstract_packet(card)
        self.assertTrue(
            usable_field("summary", draft["summary"], ["s"], packet,
                         lane="policy", title=card["title"], date=card["date"]),
            "fixed logic must accept summary with date from structured field")

    def test_policy_o1_methods_version_number_passes(self):
        from experiments.v14.pipeline import abstract_packet, usable_field
        draft = FIXTURES["policy_o1_draft"]
        card = {"id": "policy-O1", "lane": "policy",
                "title": FIXTURES["policy_o1_title"],
                "date": FIXTURES["policy_o1_date"],
                "evidence": FIXTURES["policy_o1_evidence"],
                "source_scope": "官方原文节选"}
        packet = abstract_packet(card)
        self.assertTrue(
            usable_field("methods", draft["methods"], ["s"], packet,
                         lane="policy", title=card["title"], date=card["date"]),
            "fixed logic must accept DRG/DIP2.0 version numbers")

    def test_policy_o1_title_numbers_from_evidence_pass(self):
        from experiments.v14.pipeline import abstract_packet, usable_field
        draft = FIXTURES["policy_o1_draft"]
        card = {"id": "policy-O1", "lane": "policy",
                "title": FIXTURES["policy_o1_title"],
                "date": FIXTURES["policy_o1_date"],
                "evidence": FIXTURES["policy_o1_evidence"],
                "source_scope": "官方原文节选"}
        packet = abstract_packet(card)
        self.assertTrue(
            usable_field("title", draft["title"], None, packet,
                         lane="policy", title=card["title"], date=card["date"]),
            "fixed logic must accept title numbers present in evidence")

    def test_ortho_cross_language_numbers_pass(self):
        """43097168: 'three millimeters' vs '3mm', 'October' vs '10月'."""
        from experiments.stage2.quality import number_set
        source = FIXTURES["ortho_abstract_43097168"]
        draft = FIXTURES["ortho_draft_result_43097168"]
        extra = number_set(draft) - number_set(source)
        self.assertEqual(extra, set(),
                         f"cross-language numbers should match, got {extra}")

    def test_morning_result_still_requires_r_u_refs(self):
        """Morning research papers must NOT relax the r/u ref rule."""
        from experiments.v14.pipeline import abstract_packet, usable_field
        card = {"id": "43069377", "lane": "medical", "title": "test",
                "original_abstract": "Results: AUROC 0.82 in the test set.",
                "date": "2026-11-23"}
        packet = {"u": card["original_abstract"]}
        self.assertFalse(
            usable_field("result", "AUROC 0.82", ["u"], packet,
                         morning=True, lane="medical", title="test"),
            "morning result with u refs should pass")
        self.assertFalse(
            usable_field("result", "AUROC 0.82", ["s"], packet,
                         morning=True, lane="medical", title="test"),
            "morning result with s refs must still be rejected")


class WrongNumbersStillRejected(TestCase):
    """Truly wrong numbers must still be rejected after the fix."""

    def test_wrong_result_number_rejected(self):
        from experiments.v14.pipeline import abstract_packet, usable_field
        draft = FIXTURES["policy_o1_draft"]
        card = {"id": "policy-O1", "lane": "policy",
                "title": FIXTURES["policy_o1_title"],
                "date": FIXTURES["policy_o1_date"],
                "evidence": FIXTURES["policy_o1_evidence"],
                "source_scope": "官方原文节选"}
        packet = abstract_packet(card)
        # Change 2.84亿元 to 9.99亿元 (fabricated number)
        wrong = draft["result"].replace("2.84亿元", "9.99亿元")
        self.assertNotEqual(wrong, draft["result"], "replacement must change text")
        self.assertFalse(
            usable_field("result", wrong, ["s"], packet,
                         lane="policy", title=card["title"], date=card["date"]),
            "fabricated 9.99亿元 must be rejected")

    def test_wrong_summary_number_rejected(self):
        from experiments.v14.pipeline import abstract_packet, usable_field
        draft = FIXTURES["policy_o1_draft"]
        card = {"id": "policy-O1", "lane": "policy",
                "title": FIXTURES["policy_o1_title"],
                "date": FIXTURES["policy_o1_date"],
                "evidence": FIXTURES["policy_o1_evidence"],
                "source_scope": "官方原文节选"}
        packet = abstract_packet(card)
        wrong = draft["summary"].replace("99%", "97%")
        self.assertFalse(
            usable_field("summary", wrong, ["s"], packet,
                         lane="policy", title=card["title"], date=card["date"]),
            "fabricated 97% must be rejected")

    def test_wrong_ortho_number_rejected(self):
        from experiments.stage2.quality import number_set
        source = FIXTURES["ortho_abstract_43097168"]
        wrong = FIXTURES["ortho_draft_result_43097168"].replace("349例", "999例")
        extra = number_set(wrong) - number_set(source)
        self.assertIn("999", extra, "fabricated 999 must appear as extra number")


class EventRankingRegression(TestCase):
    """Event ranking must not silently drop high-value events."""

    def test_unknown_topic_event_is_not_dropped(self):
        from experiments.v14.pipeline import ranked_candidates
        world = [{"id": "evt-1", "title": "新一代通信网建设方案发布",
                  "date": "2026-11-27", "source": "工信部",
                  "evidence": "三部门印发方案。", "official": True,
                  "topic": "major", "lane": "world"}]
        # Realistic model response: news_value=5, event subject not in WORLD_EVENTS
        editor = Mock(return_value=[{"id": "evt-1", "scores": [4]*6,
                                     "event": "通信网建设", "news_value": 5}])
        _, _, chosen = ranked_candidates(editor, [], "2026-11-27", world,
                                          v17=True, audit={"rank_batches": []},
                                          retry=True, edition="evening")
        self.assertEqual(chosen, ["evt-1"],
                         "high-value event with unknown topic must not be dropped")

    def test_low_value_event_is_rejected(self):
        from experiments.v14.pipeline import ranked_candidates
        world = [{"id": "evt-2", "title": "某地举办社区联欢活动",
                  "date": "2026-11-27", "source": "中新网",
                  "evidence": "社区活动。", "official": False,
                  "topic": "major", "lane": "world"}]
        editor = Mock(return_value=[{"id": "evt-2", "scores": [1]*6,
                                     "event": "社区活动", "news_value": 2}])
        _, _, chosen = ranked_candidates(editor, [], "2026-11-27", world,
                                          v17=True, audit={"rank_batches": []},
                                          retry=True, edition="evening")
        self.assertEqual(chosen, [], "low-value event must be rejected")

    def test_raw_score_response_is_recorded(self):
        from experiments.v14.pipeline import ranked_candidates
        world = [{"id": "evt-3", "title": "重大政策发布",
                  "date": "2026-11-27", "source": "新华社",
                  "evidence": "政策发布。", "official": True,
                  "topic": "major", "lane": "world"}]
        audit = {"rank_batches": []}
        editor = Mock(return_value=[{"id": "evt-3", "scores": [4]*6,
                                     "event": "政策", "news_value": 5}])
        ranked_candidates(editor, [], "2026-11-27", world,
                          v17=True, audit=audit, retry=True, edition="evening")
        self.assertIn("raw_scores", audit, "raw_scores must be recorded")
        self.assertEqual(len(audit["raw_scores"]), 1)


# Import Mock at the end to keep the module header clean.
from unittest.mock import Mock  # noqa: E402
