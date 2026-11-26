from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments.v14.pipeline import active_cards, formatted_copy, identities, prepare_preview, read_index, score_rows, store_index, with_evidence
from test_stage3 import article


class V14Tests(unittest.TestCase):
    def test_six_scores_are_soft_and_weighted(self):
        result = score_rows([{"id": "1", "scores": [5, 4, 3, 2, 1, 0]},
                             {"id": "2", "scores": [True, 2, 3, 4, 5, 0]}], {"1", "2"})
        self.assertEqual(result["1"]["weighted"], 65)
        self.assertNotIn("2", result)

    def test_hard_gate_accepts_source_number_and_rejects_invented_number(self):
        proposal = {"result": "复发率为15%。", "clinical": "证实可常规应用。", "methods": "纳入80名患者。",
                    "refs": {"result": ["r"], "clinical": ["c"], "methods": ["m"]}}
        accepted = {"result", "clinical", "methods"}
        source = {"r": "Recurrence was 12%.", "m": "Study enrolled 80 patients.", "c": "Needs replication."}
        checked, trace = formatted_copy(article(), proposal, accepted, source)
        self.assertEqual(checked, {"methods": "纳入80名患者。"})
        self.assertEqual(trace["result"], "source_or_numeric_mismatch")

    def test_possible_scope_passes_digital_gate_and_history_expires(self):
        card = article(title="Treatment selection after postoperative symptoms.")
        self.assertEqual(active_cards([card], "2026-11-26"), [card])
        with tempfile.TemporaryDirectory() as dirname:
            path = Path(dirname) / "index.json"
            today = date(2026, 11, 26)
            store_index(path, [], [card], today)
            self.assertEqual(set(read_index(path, today)[0]["keys"]), identities(card))
            self.assertEqual(read_index(path, date(2026, 12, 27)), [])

    def test_provider_outage_preserves_raw_brief_and_restart_suppresses_duplicates(self):
        def fail(*args, **kwargs):
            raise RuntimeError("Injected provider outage")
        row = article(source="Trial journal", title="ACL injury outcomes after reconstruction",
                      region="中国", doi="10.1234/example")
        with tempfile.TemporaryDirectory() as dirname:
            base = Path(dirname)
            index = base / ".cache" / "state.json"
            source = ("2026-11-26", [row], [], {})
            first = prepare_preview(base / "first" / "experiment.json", fail, history_path=index, evaluation=source)
            second = prepare_preview(base / "second" / "experiment.json", fail, history_path=index, evaluation=source)
            self.assertEqual(first["counts"]["medical"], 1)
            self.assertEqual(second["counts"]["medical"], 0)
            first_output = base / "first" / "news-preview" / "professional-automatic.md"
            self.assertIn("RR 1.25", first_output.read_text())
            self.assertEqual(second["history"], {"restored": 1, "written": 0})

    def test_supplement_never_substitutes_for_primary_results(self):
        source = {"r": "Absolute risk ratio 0.80.", "x1": "External trial risk ratio 0.60."}
        proposal = {"result": "相对风险为0.60。", "supplement": "另有外部试验报告相对风险0.60。",
                    "refs": {"result": ["r", "x1"], "supplement": ["x1"]}}
        verified, _ = formatted_copy(article(), proposal, {"result", "supplement"}, source)
        self.assertNotIn("result", verified)
        self.assertIn("supplement", verified)

    def test_unlabelled_rct_draft_maps_to_original_abstract_without_waiving_accuracy(self):
        proposal = {"result": "主要结局差异为1.69分。", "methods": "随机分组纳入380人。",
                    "refs": {"result": ["r"], "methods": ["m"]}}
        cohort = {"u": "A randomized trial included 380 participants. Primary effect difference was 1.69 points."}
        checked, _ = formatted_copy(article(), proposal, {"result", "methods"}, cohort)
        self.assertEqual(checked["result"], "主要结局差异为1.69分。")
        self.assertIn("380", checked["methods"])

    def test_meta_analysis_sample_uses_reported_pooled_denominator(self):
        from experiments.stage3.evidence import paper_card
        study = article(title="ACL reconstruction in children: a meta-analysis",
                        evidence="METHODS: A systematic search was conducted. "
                                 "RESULTS: Thirteen studies were included, comprising 436 patients: "
                                 "Overall growth disturbance occurred in 8 of 436 patients (1.8%).")
        self.assertIn("436", paper_card(study)[0]["design"])

    def test_baichuan_leads_require_direct_evidence_and_no_self_citation(self):
        candidate = article()
        other = article(id="10000002", pmid="10000002", title="Other ACL paper",
                        url="https://pubmed.ncbi.nlm.nih.gov/10000002/")
        with patch("experiments.v14.pipeline.verify_citation", side_effect=[candidate, None, other]):
            evidence, trace = with_evidence([candidate], lambda row: [{}, {}, {}])
        self.assertEqual(trace, {"state": "ran", "accepted": 1, "rejected": 2})
        self.assertEqual(evidence[candidate["id"]][0]["title"], other["title"])
