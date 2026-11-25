import tempfile, unittest, os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import cache_manager

class CacheLifecycleTests(unittest.TestCase):
    def test_daily_cleanup_only_removes_transient(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/".cache"; t=root/"transient"/"nested"; t.mkdir(parents=True)
            (t/"temp.txt").write_text("temp"); state=root/"news_state.json"; state.write_text("{}")
            with patch.object(cache_manager,"TRANSIENT_DIR",root/"transient"):
                result=cache_manager.cleanup_daily_transients()
            self.assertEqual(result["files_removed"],1); self.assertFalse((root/"transient").exists()); self.assertTrue(state.exists())
    def test_weekly_cleanup_is_gated_and_allowlisted(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/".cache"; d=root/"search_results"; d.mkdir(parents=True)
            old=d/"old"; old.write_text("old"); stamp=(datetime.now(timezone.utc)-timedelta(days=8)).timestamp(); os.utime(old,(stamp,stamp))
            protected=root/"news_state.json"; protected.write_text("{}"); now=datetime.now(timezone.utc)
            with patch.object(cache_manager,"CACHE_ROOT",root),patch.object(cache_manager,"STATE_PATH",root/"maintenance.json"):
                first=cache_manager.run_weekly_cache_cleanup(now); second=cache_manager.run_weekly_cache_cleanup(now+timedelta(days=1))
            self.assertEqual(first["files_removed"],1); self.assertFalse(old.exists()); self.assertTrue(protected.exists()); self.assertFalse(second["ran"])
    def test_tavily_credit_ledger(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/".cache"; now=datetime(2026,11,25,tzinfo=timezone.utc)
            with patch.object(cache_manager,"CACHE_ROOT",root),patch.object(cache_manager,"STATE_PATH",root/"maintenance.json"):
                self.assertEqual(cache_manager.monthly_tavily_credits(now),0)
                self.assertEqual(cache_manager.record_tavily_credits(2,now),2)
                self.assertEqual(cache_manager.monthly_tavily_credits(now),2)

if __name__=="__main__": unittest.main()
