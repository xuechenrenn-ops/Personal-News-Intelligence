"""Regression tests for restored retrieval features (2026-11-27).

Covers the functions restored after the completeness audit:
- Tavily free-quota call chain (key + credit_limit wiring, paygo guard)
- DeepSeek academic() medical gap-fill for the morning edition
- Evening major-event gap-fill (trigger condition + URL dedupe)
- source_report / tool_calls / channel_contribution run audit
- Evening "科技与产业" empty-section placeholder

Tests use realistic inputs and assert real logic (guards, triggers, dedupe).
No unconditional pass/full-score substitutes.
"""
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from experiments.v15.sources import evening_events
from experiments.stage2 import channels


class FakeTools:
    """Minimal SearchFetch stand-in recording calls and returning canned rows."""

    def __init__(self, rows=None, limit=5):
        self.rows = rows or []
        self.limit = limit
        self.calls = []

    def search(self, query):
        self.calls.append(("search", query))
        return self.rows

    def fetch(self, url, sites):
        self.calls.append(("fetch", url))
        return ""


def _run_prepare(tools, edition, *, headlines=None, cards=None, env=None):
    """Run prepare_preview with all network helpers stubbed out."""
    from experiments.v14 import pipeline as P

    collected = {"key": "unset", "credit_limit": "unset"}

    def fake_collect(days, *, key=None, credit_limit=0, state=None,
                     allow_possible=False, crossref_search=False):
        collected["key"] = key
        collected["credit_limit"] = credit_limit
        return {days[0].isoformat(): list(cards or [])}, {}

    patches = [
        patch.object(P, "collect", side_effect=fake_collect),
        patch.object(P, "SourceHealth"),
        patch.object(P, "collect_live_cards",
                     return_value=(list(headlines or []), "2026-11-27")),
        patch("pni.curation.widen", return_value=([], {})),
        patch("experiments.v15.sources.academic", return_value=([], {})),
        patch("experiments.v15.sources.alerts", return_value=([], 0)),
        patch("experiments.v15.sources.aossm_calendar",
              return_value=([], {"status": "patched"})),
        patch("experiments.v15.sources.enrich_news", return_value=[]),
    ]
    if env is not None:
        patches.append(patch.dict("os.environ", env))
    result = {}
    for p in patches:
        p.start()
    try:
        result = P.prepare_preview(Path("x"), tools, web=tools, edition=edition)
    except Exception as exc:  # noqa: BLE001 - surface the error to the assertion
        result["__error__"] = f"{type(exc).__name__}: {exc}"
    finally:
        for p in patches:
            p.stop()
    return result, collected


class TavilyFreeQuotaGuard(TestCase):
    def test_paygo_active_blocks_all_search(self):
        """Paid territory must not be touched: paygo>0 => zero search attempts."""
        account = {}
        payload = {"key": {"usage": 700, "limit": 700},
                   "account": {"plan_limit": 700, "plan_usage": 700, "paygo_usage": 5}}
        with patch("experiments.stage2.channels.api_json", return_value=payload):
            result = channels.search_web("key", ["2026-11-27"], 4,
                                         channels.SourceHealth(None, datetime.now()), account)
        self.assertEqual(result, {}, "paygo active must block all Tavily searches")
        self.assertTrue(account.get("quota_unavailable"))
        self.assertIn("paygo_active", account.get("quota_failure", ""))

    def test_free_room_must_exceed_reservation(self):
        """Insufficient free room must refuse before any search."""
        account = {}
        payload = {"key": {"usage": 695, "limit": 700},
                   "account": {"plan_limit": 700, "plan_usage": 700, "paygo_usage": 0}}
        with patch("experiments.stage2.channels.api_json", return_value=payload):
            result = channels.search_web("key", ["2026-11-27"], 4,
                                         channels.SourceHealth(None, datetime.now()), account)
        self.assertEqual(result, {}, "no room must block search")
        self.assertFalse(account.get("available_before"))

    def test_collect_receives_key_and_bounded_credit_limit(self):
        """prepare_preview must pass TAVILY_KEY and a bounded credit limit to collect()."""
        _, collected = _run_prepare(FakeTools(), "morning", env={"TAVILY_KEY": "free-key"})
        self.assertEqual(collected["key"], "free-key")
        self.assertEqual(collected["credit_limit"], 4, "credit limit must be bounded")

    def test_no_key_means_no_credit_limit(self):
        """Without TAVILY_KEY the call chain must stay off."""
        _, collected = _run_prepare(FakeTools(), "morning", env={"TAVILY_KEY": ""})
        self.assertIsNone(collected["key"])
        self.assertEqual(collected["credit_limit"], 0)


class AcademicGapFill(TestCase):
    def test_morning_edition_runs_academic_search(self):
        """academic() must run for the morning edition (was gated to edition is None)."""
        from experiments.v14.pipeline import prepare_preview
        tools = FakeTools(rows=[])
        captured = {}

        def fake_academic(web, as_of, verify):
            captured["called"] = True
            return [], {}

        patches = [
            patch("experiments.v14.pipeline.collect",
                  return_value=({"2026-11-27": []}, {})),
            patch("experiments.v14.pipeline.SourceHealth"),
            patch("experiments.v14.pipeline.collect_live_cards",
                  return_value=([], "2026-11-27")),
            patch("pni.curation.widen", return_value=([], {})),
            patch("experiments.v15.sources.academic", side_effect=fake_academic),
            patch("experiments.v15.sources.alerts", return_value=([], 0)),
            patch("experiments.v15.sources.aossm_calendar",
                  return_value=([], {"status": "patched"})),
        ]
        for p in patches:
            p.start()
        try:
            prepare_preview(Path("x"), tools, web=tools, edition="morning")
        except Exception:
            pass
        finally:
            for p in patches:
                p.stop()
        self.assertTrue(captured.get("called"), "academic() must run for the morning edition")


class EveningEventGapFill(TestCase):
    def test_thin_coverage_triggers_gap_fill(self):
        """No official major event in the pool => gap-fill search must run."""
        world_thin = [{"id": "G0-1", "lane": "world", "title": "普通报道",
                       "source": "chinanews", "date": "2026-11-27",
                       "url": "https://www.chinanews.com.cn/a/1",
                       "primary": True, "evidence": "内容"}]
        tools = FakeTools(rows=[{"title": "重大政策发布", "url": "https://www.gov.cn/x/1",
                                 "date": "2026-11-27", "snippet": "国务院发布重要政策。"}])
        _, collected = _run_prepare(tools, "evening", headlines=world_thin)
        self.assertTrue(any("重大" in q for kind, q in tools.calls),
                        f"evening gap-fill query must run; calls={tools.calls}")

    def test_strong_coverage_skips_gap_fill(self):
        """An official major event already in the pool => do not search again."""
        world_strong = [{"id": "W-1-O1", "lane": "world", "title": "三部门发布方案",
                         "source": "工信部", "date": "2026-11-27",
                         "url": "https://www.miit.gov.cn/a/1", "primary": True,
                         "official": True, "evidence": "方案内容"}]
        tools = FakeTools(rows=[])
        _run_prepare(tools, "evening", headlines=world_strong)
        self.assertFalse(any("重大" in q for kind, q in tools.calls),
                         f"gap-fill must not repeat when coverage is strong; calls={tools.calls}")

    def test_evening_events_dedupes_known_urls(self):
        seen = {"https://www.gov.cn/x/1"}
        tools = FakeTools(rows=[{"title": "重大政策发布", "url": "https://www.gov.cn/x/1",
                                 "date": "2026-11-27", "snippet": "内容"},
                                {"title": "另一重大决定", "url": "https://www.gov.cn/x/2",
                                 "date": "2026-11-27", "snippet": "内容"}])
        cards, trace = evening_events(tools, "2026-11-27", seen)
        self.assertEqual(len(cards), 1, "already-known URL must be deduped")
        self.assertEqual(trace["added"], 1)
        self.assertEqual(cards[0]["url"], "https://www.gov.cn/x/2")

    def test_evening_events_rejects_wrong_date(self):
        tools = FakeTools(rows=[{"title": "旧闻", "url": "https://www.gov.cn/x/9",
                                 "date": "2026-11-20", "snippet": "内容"}])
        cards, trace = evening_events(tools, "2026-11-27", set())
        self.assertEqual(cards, [], "off-date events must be rejected")
        self.assertEqual(trace["added"], 0)


class SourceReportAudit(TestCase):
    def test_run_audit_carries_source_report_and_channel_contribution(self):
        from tests.test_editions import replay
        with TemporaryDirectory() as t:
            result, _ = replay(Path(t), "morning")
        audit = result["run_audit"]
        self.assertIn("source_report", audit, "source_report must be persisted")
        self.assertIn("tool_calls", audit)
        self.assertIn("channel_contribution", audit)
        self.assertIsInstance(audit["source_report"], dict)
        self.assertIsInstance(audit["channel_contribution"], dict)

    def test_channel_contribution_counts_candidates_and_published(self):
        """Real-shaped cards carry channel tags; the audit must count them."""
        cards = [{"id": "43069377", "lane": "medical", "topic": "ortho-ai", "pmid": "43069377",
                  "title": "骨科影像自监督预训练能否提高骨折分类性能？",
                  "original_abstract": "BACKGROUND: x. METHODS: y. RESULTS: 0.82. CONCLUSIONS: c.",
                  "date": "2026-11-23", "source": "European spine journal",
                  "url": "https://pubmed.ncbi.nlm.nih.gov/43069377/", "primary": True,
                  "channels": ["pubmed", "europe:ortho-ai"]}]
        result, _ = _run_prepare(FakeTools(), "morning", cards=cards)
        contribution = result["run_audit"]["channel_contribution"]
        self.assertTrue(contribution, "at least one channel must be recorded")
        self.assertIn("pubmed", contribution)
        self.assertEqual(contribution["pubmed"]["candidates"], 1)
        self.assertGreaterEqual(contribution["pubmed"]["candidates"], contribution["pubmed"]["published"])
        self.assertIn("europe:ortho-ai", contribution)


class EveningSectionPlaceholder(TestCase):
    def test_technology_section_renders_placeholder_when_empty(self):
        from experiments.stage2.pipeline import render
        card = {"id": "p1", "lane": "policy", "title": "政策",
                "summary": "发生了什么。", "why": "原因。", "impact": "影响。",
                "source": "国家医保局", "date": "2026-11-27", "url": "https://x"}
        messages = render([card], [card], "2026-11-27")
        extended = messages[1]
        self.assertIn("## 科技与产业", extended)
        self.assertIn("本次暂无达到来源和质量条件的资讯。", extended)


class ResearchAbstractPrinciple(TestCase):
    def test_morning_research_uses_full_original_abstract(self):
        """Research editing must keep using only the complete original Abstract."""
        from experiments.v14.pipeline import copy_packet
        card = {"id": "43069377", "lane": "medical", "title": "test", "date": "2026-11-23",
                "original_abstract": "BACKGROUND: x. METHODS: y. RESULTS: z. CONCLUSIONS: c."}
        packet = copy_packet(card, morning=True)
        self.assertEqual(set(packet["evidence"]), {"u"}, "morning evidence must be the full abstract")
        self.assertIn("RESULTS: z.", packet["evidence"]["u"])
