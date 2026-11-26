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
            event = {"after": "test", "head_commit": {"added": ["experiments/dispatch/trial.json"]}}
            self.assertEqual(read_dispatch(event, REF, "test", root)[0], "trial")
            self.assertEqual(read_dispatch(event, REF, "test", root)[1]["reserve_micro_cny"], 200000)
            for bad_ref, bad_event in [(REF, {**event, "forced": True}), ("refs/heads/main", event)]:
                with self.assertRaises(RuntimeError):
                    read_dispatch(bad_event, bad_ref, "test", root)
            file.write_text('{"phase":"candidate","reserve_micro_cny":200001}')
            with self.assertRaises(RuntimeError):
                read_dispatch(event, REF, "test", root)

    def test_normalizer_rejects_unknown_ids_and_caps_policy(self):
        cards = FIXTURE["development"]
        output = normalize([{"id": "missing"}, {"id": "n1"}, {"id": "n5"}], cards)
        self.assertEqual([r["id"] for r in output], ["n1"])


if __name__ == "__main__":
    unittest.main()
