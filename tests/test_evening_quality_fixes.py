"""Regression tests for the evening content-quality fixes (2026-11-27).

Driven by the REAL round4-evening artifacts that exposed the defects:
- 3 of 6 generated rows were the input packet echoed back (core_field_missing)
- an HTTP 404 Fetch was billed CNY 0.005 although only successful requests are
  billed ("每 1,000 次成功请求 5元")
- channel_contribution was empty for news (no `channels` field)
- generation raw content was not persisted

Nothing here calls the network: model and tool layers are stubbed with the
real response shapes taken from references/live-run/round4-evening/.
"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from experiments.v14.pipeline import is_input_echo
from experiments.v15.sources import SearchFetch, TOOL_PRICE

DRAFTS = json.loads(Path(__file__).with_name("fixtures").joinpath(
    "cloud_regressions.json").read_text(encoding="utf-8"))["round4_drafts"]

# Real round4 rows: input echoes vs real drafts.
ECHO_W1O1 = DRAFTS["W-1-O1"]
ECHO_G22 = DRAFTS["G2-2"]
ECHO_G42 = DRAFTS["G4-2"]
OK_G27 = DRAFTS["G2-7"]
OK_POLICY = DRAFTS["policy-O1"]


class EchoDetection(TestCase):
    def test_round4_echo_rows_are_detected(self):
        """The 3 real round4 input echoes must be recognised."""
        for row in (ECHO_W1O1, ECHO_G22, ECHO_G42):
            self.assertTrue(is_input_echo(row),
                            "round4 echo must be detected: %s" % sorted(row))

    def test_round4_real_drafts_are_not_flagged(self):
        for row in (OK_G27, OK_POLICY):
            self.assertFalse(is_input_echo(row),
                             "real draft must not be flagged: %s" % sorted(row))

    def test_requires_evidence_and_type_and_no_output_field(self):
        self.assertTrue(is_input_echo({"id": "x", "type": "world",
                                       "evidence": {"s": "y"}, "title": "t"}))
        self.assertFalse(is_input_echo({"id": "x", "type": "world",
                                        "evidence": {"s": "y"}, "summary": "有内容"}))
        self.assertFalse(is_input_echo({"id": "x", "summary": "s"}))
        self.assertFalse(is_input_echo("not-a-dict"))


class EchoRetry(TestCase):
    def _run_generate_with_echo(self, retried):
        """Drive write_verified with a scripted provider that echoes first."""
        from experiments.v14.pipeline import write_verified

        class Scripted:
            def __init__(self):
                self.calls = []
            def __call__(self, phase, prompt, candidates, *, thinking=False,
                         max_tokens=0):
                self.calls.append((phase, [c["id"] for c in candidates]))
                if phase == "v14-generate":
                    return [dict(ECHO_G22), dict(OK_G27)]
                return retried

        card_a = {"id": "G2-2", "lane": "world", "title": "配备星链终端", "date": "2026-11-27",
                  "evidence": "中新网11月27日电 乌克兰无人艇袭击俄方舰船。"}
        card_b = {"id": "G2-7", "lane": "world", "title": "欧盟货物贸易", "date": "2026-11-27",
                  "evidence": "今年三季度欧盟货物贸易进口总额7300亿欧元。"}
        provider = Scripted()
        data, proposals, failures = write_verified(
            provider, [card_a, card_b], v17=True, edition="evening")
        return data, proposals, failures, provider

    def test_echo_is_retried_once_and_recovered(self):
        retried = [{"id": "G2-2", "summary": "发生了什么。", "why": "值得关注。"
                     , "impact": "实际影响。", "refs": {"summary": ["s"]}}]
        data, proposals, failures, provider = self._run_generate_with_echo(retried)
        self.assertIn("G2-2", proposals, "recovered row must be kept")
        self.assertEqual(proposals["G2-2"].get("summary"), "发生了什么。")
        phases = [p for p, _ in provider.calls]
        self.assertIn("v14-generate-echo-retry", phases, "retry must run once")
        retry_calls = [ids for p, ids in provider.calls if p == "v14-generate-echo-retry"]
        self.assertEqual(retry_calls, [["G2-2"]], "retry must target only the echoed ids")

    def test_unrecovered_echo_is_dropped_not_treated_as_draft(self):
        retried = [dict(ECHO_G22)]  # echoes again
        data, proposals, failures, provider = self._run_generate_with_echo(retried)
        self.assertNotIn("G2-2", proposals,
                         "still-echoed row must not become a draft")
        self.assertIn("G2-7", proposals)

    def test_no_retry_when_no_echo(self):
        from experiments.v14.pipeline import write_verified

        class Scripted:
            def __init__(self):
                self.calls = []
            def __call__(self, phase, prompt, candidates, **kwargs):
                self.calls.append(phase)
                return [{"id": c["id"], "summary": "s", "why": "w", "impact": "i",
                         "refs": {"summary": ["s"]}} for c in candidates]

        card = {"id": "G1", "lane": "world", "title": "t", "date": "2026-11-27",
                "evidence": "e"}
        provider = Scripted()
        write_verified(provider, [card], v17=True, edition="evening")
        self.assertNotIn("v14-generate-echo-retry", provider.calls,
                         "retry must not run when the first pass is clean")


class FetchBilling(TestCase):
    def test_http_error_is_not_billed(self):
        """Official rule: only successful requests are billed."""
        def fail(url, headers, payload):
            raise RuntimeError("HTTP_404")
        tools = SearchFetch({"Authorization": "Bearer x"}, 5, transport=fail)
        tools.fetch("https://www.aljazeera.com/a", ("aljazeera.com",))
        entry = tools.calls[-1]
        self.assertEqual(entry["status"], "HTTP_404")
        self.assertEqual(entry["observed_peak_micro_cny"], 0,
                         "a failed request must not be billed")

    def test_transport_error_is_not_billed(self):
        def fail(url, headers, payload):
            raise RuntimeError("boom")
        tools = SearchFetch({"Authorization": "Bearer x"}, 5, transport=fail)
        tools.fetch("https://www.aljazeera.com/a", ("aljazeera.com",))
        self.assertEqual(tools.calls[-1]["observed_peak_micro_cny"], 0)

    def test_successful_request_is_billed(self):
        def ok(url, headers, payload):
            return {"content": "x" * 200, "title": "t"}
        tools = SearchFetch({"Authorization": "Bearer x"}, 5, transport=ok)
        tools.fetch("https://www.aljazeera.com/a", ("aljazeera.com",))
        entry = tools.calls[-1]
        self.assertEqual(entry["status"], "billable")
        self.assertEqual(entry["observed_peak_micro_cny"], TOOL_PRICE)

    def test_empty_200_is_still_a_successful_request(self):
        def empty(url, headers, payload):
            return {"content": "", "title": ""}
        tools = SearchFetch({"Authorization": "Bearer x"}, 5, transport=empty)
        tools.fetch("https://www.aljazeera.com/a", ("aljazeera.com",))
        entry = tools.calls[-1]
        self.assertEqual(entry["status"], "empty")
        self.assertEqual(entry["observed_peak_micro_cny"], TOOL_PRICE,
                         "a 200 response is a successful request")

    def test_off_site_fetch_is_never_sent(self):
        def ok(url, headers, payload):
            return {"content": "x" * 200}
        tools = SearchFetch({"Authorization": "Bearer x"}, 5, transport=ok)
        self.assertEqual(tools.fetch("https://evil.example.com/a", ("aljazeera.com",)), "")
        self.assertEqual(tools.calls, [], "off-site url must not consume a call")


class ChannelContributionAudit(TestCase):
    def test_news_without_channels_uses_source(self):
        from tests.test_editions import replay
        with TemporaryDirectory() as t:
            result, _ = replay(Path(t), "evening")
        contribution = result["run_audit"]["channel_contribution"]
        self.assertTrue(contribution, "news channels must be recorded via source")
        self.assertTrue(any(k.startswith("source:") for k in contribution),
                        "source-based channels must appear")

    def test_raw_calls_are_persisted(self):
        from tests.test_editions import replay
        with TemporaryDirectory() as t:
            result, _ = replay(Path(t), "evening")
        raw_calls = result["run_audit"].get("raw_calls")
        self.assertTrue(raw_calls, "generation/verification raw responses must persist")
        self.assertTrue(all("phase" in c and "raw_content" in c for c in raw_calls))


class EveningPromptContract(TestCase):
    def test_evening_write_demands_chinese_title_and_no_echo(self):
        from experiments.v14.pipeline import EVENING_WRITE
        self.assertIn("中文题名", EVENING_WRITE, "title translation must be requested")
        self.assertIn("不编造", EVENING_WRITE, "causality must not be fabricated")
        self.assertIn("不得回传输入", EVENING_WRITE, "input echo must be forbidden")
        self.assertIn("refs", EVENING_WRITE)


class RssCooldown(TestCase):
    def test_failed_feed_records_cooldown_and_is_skipped(self):
        import news_push
        recorded = {}

        class FakeHealth:
            def __init__(self):
                self._cool = {}
            def status(self, key):
                return self._cool.get(key, False)
            def record(self, key, ok, cooldown=False):
                recorded[key] = ok
                if not ok:
                    self._cool[key] = True

        health = FakeHealth()
        trace = []
        with patch("news_push.requests.get", side_effect=Exception("SSLError")):
            rows = news_push.fetch_candidates([("BBC-World", "https://x")], "G0-", 48,
                                              trace=trace, health=health)
        self.assertEqual(rows, [])
        self.assertEqual(trace[0]["outcome"], "fetch_error")
        self.assertIs(recorded.get("rss:BBC-World"), False, "failure must be recorded")
        # Second call must be skipped by cooldown.
        trace.clear()
        rows2 = news_push.fetch_candidates([("BBC-World", "https://x")], "G0-", 48,
                                           trace=trace, health=health)
        self.assertEqual(rows2, [])
        self.assertEqual(trace[0]["outcome"], "cooldown")


class MorningStillFrozen(TestCase):
    def test_morning_replay_unchanged(self):
        from tests.test_editions import replay
        with TemporaryDirectory() as t:
            result, _ = replay(Path(t), "morning")
        self.assertEqual(result["counts"]["medical"], 3)
        audit = result["run_audit"]
        for row in audit["medical"].values():
            self.assertEqual(set(row["evidence"]), {"u"},
                             "morning evidence must remain the full abstract")
        self.assertFalse(result["selection"]["missing_professional"])
