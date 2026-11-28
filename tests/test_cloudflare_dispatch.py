import os
import unittest
from datetime import datetime
from unittest.mock import patch

from pni.production import main


class CloudflareDispatchTests(unittest.TestCase):
    def test_only_enabled_valid_current_slot_runs_production(self):
        environment = {
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REF": "refs/heads/main",
            "RUNNER_TEMP": ".",
            "PNI_TRIAL_START": "2026-11-28",
            "PNI_SCHEDULE_SOURCE": "cloudflare",
        }
        cases = (
            ("2026-11-30T06:30:12+08:00", "2026-11-30T06:30:00+08:00", "morning", True),
            ("2026-12-01T19:30:10+08:00", "2026-12-01T19:30:00+08:00", "evening", True),
            ("2026-12-01T06:31:00+08:00", "2026-11-30T06:30:00+08:00", "morning", False),
            ("2026-12-01T06:29:59+08:00", "2026-12-01T06:30:00+08:00", "morning", False),
            ("2026-12-01T21:30:00+08:00", "2026-12-01T19:30:00+08:00", "evening", False),
        )
        with patch("pni.production.execute") as execute, patch("pni.production.datetime", wraps=datetime) as clock:
            for now, slot, edition, expected in cases:
                with self.subTest(now=now, edition=edition):
                    clock.now.return_value = datetime.fromisoformat(now)
                    execute.reset_mock()
                    with patch.dict(os.environ, {
                        **environment, "PNI_DISPATCH_DATE": slot[:10],
                        "PNI_DISPATCH_EDITION": edition, "PNI_DISPATCH_SCHEDULED_BEIJING": slot,
                    }, clear=True):
                        main()
                    self.assertEqual(execute.called, expected)
                    if expected:
                        self.assertEqual(execute.call_args.args[2], edition)

    def test_disabled_or_invalid_dispatch_never_uses_preview_or_production(self):
        environment = {
            "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/heads/main", "RUNNER_TEMP": ".",
            "PNI_DISPATCH_DATE": "2026-12-01", "PNI_DISPATCH_EDITION": "evening",
            "PNI_DISPATCH_SCHEDULED_BEIJING": "2026-12-01T06:30:00+08:00",
        }
        with patch("pni.production.execute") as execute, patch("pni.production.archived_preview") as preview:
            with patch.dict(os.environ, environment, clear=True):
                main()
                with patch.dict(os.environ, {"PNI_SCHEDULE_SOURCE": "cloudflare"}):
                    with self.assertRaisesRegex(ValueError, "does not match"):
                        main()
                with patch.dict(os.environ, {"PNI_MORNING_CATCH_UP_DATE": "2026-12-01"}):
                    with self.assertRaisesRegex(ValueError, "mutually exclusive"):
                        main()
            execute.assert_not_called()
            preview.assert_not_called()
