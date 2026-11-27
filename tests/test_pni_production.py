import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from pni.production import DAILY, MONTHLY, GitHubState, PushPlus, claim, daily_enabled, deliver, execute, trial_age


class Ledger:
    def __init__(self):
        self.data = {"version": 1}

    def save(self):
        pass


class ProductionTests(unittest.TestCase):
    def test_schedule_requires_send_token_before_claim(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "code", "PUSHPLUS_TOKEN": ""}, clear=True), \
                patch("pni.production.GitHubState") as state:
            with self.assertRaisesRegex(RuntimeError, "PushPlus token is missing"):
                execute("daily", Path("."), "evening")
            state.assert_not_called()

    def test_log_expiry_keeps_audit_window_for_both_editions(self):
        state = Mock(spec=GitHubState)
        state.base = "https://api.github.com/repos/owner/repo"
        state.headers = {}
        state.data = {"runs": {f"2026-11-{day}:{edition}": {"run_id": str(day * 10 + index)}
                               for day in (19, 20, 21) for index, edition in enumerate(("morning", "evening"))}}
        state.transport = Mock()
        state.transport.delete.return_value.status_code = 204
        GitHubState.purge_logs(state, "2026-11-27")
        self.assertEqual(state.transport.delete.call_count, 4)
        self.assertTrue(state.data["runs"]["2026-11-20:morning"]["logs_expired"])
        self.assertNotIn("logs_expired", state.data["runs"]["2026-11-21:evening"])
        state.save.assert_called_once()

    def test_dedup_and_monthly_limit(self):
        ledger = Ledger()
        self.assertIsNotNone(claim(ledger, "2026-11-26", "acceptance", "11"))
        self.assertIsNone(claim(ledger, "2026-11-26", "daily", "12"))
        self.assertIsNone(claim(ledger, "2026-11-27", "acceptance", "13"))
        self.assertEqual(ledger.data["budgets"]["2026-11"]["reserved"], DAILY)
        ledger.data["budgets"]["2026-11"]["reserved"] = MONTHLY - DAILY + 1
        with self.assertRaises(RuntimeError):
            claim(ledger, "2026-11-27", "daily", "14")

    def test_acceptance_does_not_retry_and_preserves_unknown(self):
        ledger = Ledger()
        batch = claim(ledger, "2026-11-26", "acceptance", "11")
        sender = Mock()
        sender.send.side_effect = [({"status":"accepted", "receipt":"xxxx…yyyy"}, "secretshortcode"),
                                   ({"status":"submission_unknown"}, None)]
        sender.lookup.return_value = {"status":"delivery_failed"}
        deliver(ledger, batch, "2026-11-26", {"professional":"a", "extended":"b"}, [], [], sender)
        self.assertEqual(sender.send.call_count, 2)
        self.assertEqual(batch["state"], "needs_review")
        self.assertNotIn("secretshortcode", str(ledger.data))

    def test_provider_confirmation_and_masked_receipt(self):
        http = Mock()
        reply = http.post.return_value
        reply.status_code = 200
        reply.json.return_value = {"code":200, "data":"1234567890123456"}
        api = PushPlus("not-a-key", "access", transport=http)
        result, short = api.send("title", "message")
        self.assertEqual(result["receipt"], "1234…3456")
        http.get.return_value.ok = True
        http.get.return_value.json.return_value = {"code":200, "data":{"status":2}}
        self.assertEqual(api.lookup(short)["status"], "sent")

    def test_daily_not_enabled_before_approved_start(self):
        self.assertFalse(daily_enabled("2026-11-27", None))
        self.assertFalse(daily_enabled("2026-11-27", "invalid"))
        self.assertFalse(daily_enabled("2026-11-26", "2026-11-27"))
        self.assertTrue(daily_enabled("2026-11-27", "2026-11-27"))

    def test_trial_counts_seven_days_and_then_keeps_daily_running(self):
        self.assertEqual(trial_age("2026-12-03", "2026-11-27"), 6)
        self.assertEqual(trial_age("2026-12-04", "2026-11-27"), 7)
        self.assertTrue(daily_enabled("2026-12-04", "2026-11-27"))
