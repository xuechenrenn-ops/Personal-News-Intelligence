"""Regression tests for the ranking response merge (evening fix, 2026-11-27).

Uses the REAL round3 raw model responses that prove the defect: the model
returns event scores under a "world" key while Flash.__call__ used to read
only "items". The tests verify:

1. Both arrays are merged so the 8 scored events reach later filtering
2. Malformed rows, illegal ids and duplicates are rejected
3. Non-ranking responses (no "world" key) keep their historical behaviour
4. Morning stays untouched (no world rows for the morning edition)

No unconditional mock: the fixtures are the actual model output from
references/live-run/round3/evening/run-summary.json.
"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

ROUND3_RAW_0, ROUND3_RAW_1 = json.loads(Path(__file__).with_name("fixtures").joinpath(
    "cloud_regressions.json").read_text(encoding="utf-8"))["round3_rank"]

# The 8 events the model actually scored 4-5 in round3.
EXPECTED_SCORED_IDS = {"W-1-O1", "G3-2", "G4-2", "G0-4",
                       "G2-7", "G1-7", "G4-5", "G3-5"}


class FakeFlash:
    """Minimal Flash stand-in returning a canned raw response."""

    def __init__(self, raw):
        self.raw = raw
        self.calls = []

    def __call__(self, phase, prompt, candidates, **kwargs):
        return self._parse()

    def _parse(self):
        import experiments.run as R
        # Reuse the production parsing logic by building a real Flash and
        # monkeypatching only the network layer would be heavy; instead the
        # test drives the merge through Flash.__call__ with a stubbed api_json.
        raise NotImplementedError


def parse_raw(raw):
    """Drive the production parsing path with a stubbed HTTP layer."""
    from experiments.run import Flash
    recorded = {}

    def fake_api(url, headers, payload):
        recorded["payload"] = payload
        return {"choices": [{"finish_reason": "stop",
                             "message": {"content": raw}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    with patch("experiments.run.api_json", side_effect=fake_api):
        flash = Flash("unused", 100_000)
        rows = flash("v14-score", "prompt", {"papers": [], "world": []},
                     max_tokens=100)
    return rows, flash.calls[-1]


class MergeFixtures(TestCase):
    def test_round3_batch0_events_reach_filtering(self):
        """The 8 scored events from the real response must be returned."""
        rows, _ = parse_raw(ROUND3_RAW_0)
        ids = {r["id"] for r in rows}
        self.assertIn("W-1-O1", ids, "tech event with news_value=5 must survive")
        self.assertTrue({"G3-2", "G4-2", "G0-4"} <= ids,
                        "batch-0 scored events must survive")
        self.assertEqual(len(rows), 4, "items=[] + world=[4] = 4 rows")

    def test_round3_batch1_events_reach_filtering(self):
        rows, _ = parse_raw(ROUND3_RAW_1)
        ids = {r["id"] for r in rows}
        self.assertTrue({"G2-7", "G1-7", "G4-5", "G3-5"} <= ids,
                        "batch-1 scored events must survive")
        self.assertEqual(len(rows), 4)

    def test_all_eight_round3_events_pass(self):
        all_ids = set()
        for raw in (ROUND3_RAW_0, ROUND3_RAW_1):
            rows, _ = parse_raw(raw)
            all_ids |= {r["id"] for r in rows}
        self.assertEqual(all_ids, EXPECTED_SCORED_IDS,
                         "exactly the 8 round3 scored events must be returned")

    def test_events_enter_news_value_filter(self):
        """The merged rows must satisfy the downstream news_value gate."""
        from experiments.v14.pipeline import _normalized_title, WORLD_EVENTS
        scored = []
        for raw in (ROUND3_RAW_0, ROUND3_RAW_1):
            rows, _ = parse_raw(raw)
            for row in rows:
                if not isinstance(row.get("news_value"), int) or not 4 <= row["news_value"] <= 5:
                    continue
                if not row.get("event"):
                    continue
                scored.append(row["id"])
        self.assertEqual(set(scored), EXPECTED_SCORED_IDS,
                         "all 8 must pass the news_value 4-5 gate")

    def test_rejects_non_dict_rows(self):
        rows, _ = parse_raw(json.dumps(
            {"items": [], "world": ["not-a-dict", {"id": "E1", "event": "x", "news_value": 5}]}))
        self.assertEqual([r["id"] for r in rows], ["E1"])

    def test_rejects_illegal_ids(self):
        rows, _ = parse_raw(json.dumps(
            {"items": [], "world": [{"id": "", "event": "x", "news_value": 5},
                                    {"event": "no-id", "news_value": 5},
                                    {"id": 7, "event": "int-id", "news_value": 5},
                                    {"id": "OK", "event": "valid", "news_value": 5}]}))
        self.assertEqual([r["id"] for r in rows], ["OK"])

    def test_rejects_duplicate_ids(self):
        rows, _ = parse_raw(json.dumps(
            {"items": [{"id": "A", "scores": [1, 1, 1, 1, 1, 1]}],
             "world": [{"id": "A", "event": "dup", "news_value": 5},
                       {"id": "B", "event": "new", "news_value": 5}]}))
        self.assertEqual([r["id"] for r in rows], ["A", "B"])
        self.assertNotIn("dup", [r.get("event") for r in rows],
                         "duplicate id must keep only the first row")


class NonRankingUnchanged(TestCase):
    def test_items_only_response_returns_items_verbatim(self):
        """generate/verify responses carry no world key: behaviour must not change."""
        payload = [{"id": "p1", "summary": "发生了什么。", "refs": {"summary": ["s"]}}]
        rows, _ = parse_raw(json.dumps({"items": payload}))
        self.assertEqual(rows, payload)

    def test_missing_items_still_raises(self):
        with self.assertRaises(Exception):
            parse_raw(json.dumps({"world": [{"id": "E", "event": "x", "news_value": 5}]}))


class MorningProtection(TestCase):
    def test_morning_rank_response_has_no_world_to_merge(self):
        """Morning passes no news cards, so the model returns only items."""
        rows, _ = parse_raw(json.dumps(
            {"items": [{"id": "43069377", "scores": [4, 4, 4, 4, 4, 4]}]}))
        self.assertEqual(len(rows), 1)
        self.assertNotIn("event", rows[0], "morning rows are papers, not events")

    def test_morning_replay_unchanged(self):
        """Full morning replay must still publish and keep the abstract-only rule."""
        from tests.test_editions import replay
        with TemporaryDirectory() as t:
            result, _ = replay(Path(t), "morning")
        self.assertEqual(result["counts"]["medical"], 3)
        audit = result["run_audit"]
        for row in audit["medical"].values():
            self.assertEqual(set(row["evidence"]), {"u"},
                             "morning evidence must remain the full abstract")
        self.assertFalse(result["selection"]["missing_professional"],
                         "morning professional slots must stay filled")
