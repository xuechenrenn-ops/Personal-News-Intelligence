from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments.v14.pipeline import active_cards, formatted_copy, identities, read_index, score_rows, store_index, with_evidence
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

    def test_baichuan_leads_require_direct_evidence_and_no_self_citation(self):
        candidate = article()
        other = article(id="10000002", pmid="10000002", title="Other ACL paper",
                        url="https://pubmed.ncbi.nlm.nih.gov/10000002/")
        with patch("experiments.v14.pipeline.verify_citation", side_effect=[candidate, None, other]):
            evidence, trace = with_evidence([candidate], lambda row: [{}, {}, {}])
        self.assertEqual(trace, {"state": "ran", "accepted": 1, "rejected": 2})
        self.assertEqual(evidence[candidate["id"]][0]["title"], other["title"])
