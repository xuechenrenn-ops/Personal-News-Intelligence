"""Offline end-to-end integration test through the real production entry.

Runs pni.production.execute("preview", outdir, "evening") - the exact entry
used by the live launcher - with the ENTIRE internal call chain intact.
Only the external network/HTTP layer is replaced:

  * urllib.request.urlopen  -> canned DeepSeek model/tool JSON
  * requests.Session.request -> canned RSS/HTML bodies

Nothing between those boundaries is stubbed: fetch_candidates,
fetch_official_page_candidates, collect_live_cards, ranked_candidates,
write_verified, is_input_echo, enrich_news, SearchFetch, prepare_preview and
the HTML renderer all execute for real. This is what catches call-signature
breakage like the `health` kwarg that unit tests missed.

The fixtures are shaped like the real round4-evening traffic (RSS XML,
official HTML, DeepSeek chat/tools JSON).
"""
from base64 import b64decode, b64encode
import io
import json
import urllib.error
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from pni.production import execute

DAY = "2026-11-27"


def _recent_rfc822(hours_ago=2):
    """A publication stamp inside the acceptance window regardless of the clock."""
    import email.utils
    from datetime import datetime, timedelta, timezone
    return email.utils.format_datetime(datetime.now(timezone.utc) - timedelta(hours=hours_ago))


RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>{title}</title>
<item><title>{t1}</title><link>https://www.chinanews.com.cn/gj/2026/11-27/10738559.shtml</link>
<description>{d1}</description><pubDate>{pub}</pubDate></item>
<item><title>{t2}</title><link>https://www.chinanews.com.cn/gj/2026/11-27/10738560.shtml</link>
<description>{d2}</description><pubDate>{pub}</pubDate></item>
</channel></rss>"""

EVIDENCE_TEXT = ("2026年11月27日，湖北医保部门通报按疾病诊断相关分组DRG/DIP医保智能监管体系建设进展："
                 "全省现场检查3.5万家定点医药机构；截至8月底事前提醒接入率达99%；"
                 "事前拦截避免基金损失2.84亿元；新增8大类智能监管规则。")

PUBMED_SEARCH = {"esearchresult": {"idlist": ["43069377"], "count": "1"}}

PUBMED_XML = """<?xml version="1.0"?>
<PubmedArticleSet><PubmedArticle>
<MedlineCitation><PMID>43069377</PMID>
<Article><Journal><JournalIssue><PubDate><Year>2026</Year><Month>Nov</Month><Day>23</Day></PubDate></JournalIssue>
<Title>European spine journal</Title></Journal>
<ArticleTitle>Supervised pretraining improves fracture classification accuracy.</ArticleTitle>
<Abstract><AbstractText Label="BACKGROUND">Self-supervised pretraining is explored for fracture classification.</AbstractText>
<AbstractText Label="METHODS">A cohort of 436 patients was evaluated with three readers.</AbstractText>
<AbstractText Label="RESULTS">Accuracy reached 92 percent with agreement of 0.82.</AbstractText>
<AbstractText Label="CONCLUSIONS">Pretraining improved classification in this cohort.</AbstractText></Abstract>
</Article>
<PubmedData><ArticleIdList><ArticleId IdType="doi">10.1000/test.doi.43069377</ArticleId></ArticleIdList></PubmedData>
</MedlineCitation></PubmedArticle></PubmedArticleSet>"""

ESEARCH_EMPTY = {"esearchresult": {"idlist": [], "count": "0"}}
EFETCH_EMPTY = '<?xml version="1.0"?><PubmedArticleSet></PubmedArticleSet>'

OFFICIAL_LIST = """<html><head><title>list</title></head><body>
<ul>
<li><a href="/zwgk/zcwj/wjfb/tz/art/2026/art_808db1814bb74a0e87965d66e74f5b4b.html">三部门关于印发《新一代通信网建设实施方案》的通知</a><span>2026-11-26</span></li>
<li><a href="/zwgk/zcjd/art/2026/art_333ab7eca49147868c23d0ed4eecaa4d.html">一图读懂《新一代通信网建设实施方案》</a><span>2026-11-25</span></li>
</ul></body></html>"""

OFFICIAL_DETAIL = """<html><head><title>三部门关于印发《新一代通信网建设实施方案》的通知</title></head>
<body><div class="article">
工信部联通信〔2026〕267号各省、自治区、直辖市人民政府，国务院各部委、各直属机构及有关中央企业：
《新一代通信网建设实施方案》已经国务院同意，现印发给你们，请认真贯彻执行。
</div></body></html>"""

NHSA_LIST = """<html><head><title>医保局</title></head><body><ul>
<li><a href="/art/2026/11/26/art_14_23532.html">湖北健全DRG/DIP医保智能监管体系持续筑牢监管防线</a><span>2026-11-26</span></li>
<li><a href="/art/2026/11/26/art_14_23533.html">连续用药管理功能上线</a><span>2026-11-26</span></li>
</ul></body></html>"""

NHSA_DETAIL = """<html><head><title>湖北健全DRG/DIP医保智能监管体系持续筑牢监管防线</title></head>
<body><div class="TRS_Editor">2026年11月26日，湖北省医保部门通报按疾病诊断相关分组（DRG）/按病种分值付费（DIP）医保智能监管体系建设进展：全省现场检查3.5万家定点医药机构；截至8月底事前提醒接入率达99%，事前拦截避免基金损失2.84亿元；新增8大类智能监管规则，智能审核拒付违规费用1.4亿元；36类大数据反欺诈模型累计下发疑点线索28760条。</div></body></html>"""

# --- DeepSeek chat completions: routed on the payload, echoing real ids ----
def _ids_in(obj):
    """Collect candidate ids from a request payload (papers/world/record)."""
    found = []
    if isinstance(obj, dict):
        for key in ("papers", "world"):
            for row in obj.get(key) or []:
                if isinstance(row, dict) and isinstance(row.get("id"), str):
                    found.append(row["id"])
        if isinstance(obj.get("id"), str):
            found.append(obj["id"])
        for key in ("record", "proposed"):
            if isinstance(obj.get(key), dict) and isinstance(obj[key].get("id"), str):
                found.append(obj[key]["id"])
    elif isinstance(obj, list):
        for row in obj:
            found.extend(_ids_in(row))
    return found


def _model_response(payload):
    """Route a DeepSeek chat payload to a canned JSON body that echoes the ids.

    Routing is structural (shape of the user turn), not keyword-based, so a
    prompt that happens to contain words like "候选" cannot hijack the branch.
    """
    content = ""
    ids = []
    user_blob = ""
    for message in (payload.get("messages") or []) if isinstance(payload, dict) else []:
        blob = message.get("content")
        if isinstance(blob, str):
            content += blob
            if message.get("role") == "user":
                user_blob = blob
            if blob[:1] in "{[":
                try:
                    ids.extend(_ids_in(json.loads(blob)))
                except ValueError:
                    pass
        elif isinstance(blob, (dict, list)):
            ids.extend(_ids_in(blob))
    ids = list(dict.fromkeys(i for i in ids if i))

    try:
        parsed = json.loads(user_blob) if user_blob[:1] in "{[" else None
    except ValueError:
        parsed = None

    if isinstance(parsed, dict) and ("papers" in parsed or "world" in parsed):
        paper_ids = [r["id"] for r in (parsed.get("papers") or [])
                     if isinstance(r, dict) and isinstance(r.get("id"), str)]
        world_ids = [r["id"] for r in (parsed.get("world") or [])
                     if isinstance(r, dict) and isinstance(r.get("id"), str)]
        body = {"items": [{"id": i, "scores": [4, 4, 4, 4, 4, 4], "reason": "可选"} for i in paper_ids],
                "world": [{"id": i, "event": "事件主题" + i, "news_value": 5 if i.endswith("1") else 4}
                          for i in world_ids]}
    elif isinstance(parsed, list) and parsed and isinstance(parsed[0], dict) and "record" in parsed[0]:
        body = {"items": [{"id": i, "valid": ["title", "question", "result", "conclusion",
                                              "evaluation", "lead", "summary", "why", "impact"]}
                          for i in ids]}
    else:
        # Numbers quoted here MUST appear in the evidence or the fact gate
        # rejects them - that is exactly what the gate is for. Medical
        # (morning) rows use the research fields, world (evening) rows use the
        # three evening fields.
        kinds = [r.get("type") for r in parsed] if isinstance(parsed, list) else []
        research = any(k == "medical" for k in kinds)
        if research:
            body = {"items": [
                {"id": i, "title": "中文科研题名",
                 "question": "探讨自监督预训练能否提高骨折识别准确性。",
                 "result": "研究者纳入436例患者，准确率达92，一致性为0.82。",
                 "conclusion": "预训练在该队列中提高了分类准确性。",
                 "evaluation": "结果限于该队列，尚不能推断外部验证表现。",
                 "lead": "自监督预训练能否提高骨折识别准确性？该队列纳入436例患者。",
                 "refs": {"question": ["u"], "result": ["u"], "conclusion": ["u"], "evaluation": ["u"]}}
                for i in ids]}
        else:
            body = {"items": [
                {"id": i, "title": "中文题名",
                 "summary": "2026年11月27日，湖北医保部门通报DRG/DIP医保智能监管体系建设进展，现场检查3.5万家定点医药机构。",
                 "why": "监管从按项目向按病种延伸，与原有做法的差异在于新增全病历数据采集与病案编码前置校验。",
                 "impact": "受影响方为定点医药机构；已观察到事前提醒接入率达99%。",
                 "refs": {"summary": ["s"], "why": ["s"], "impact": ["s"]}}
                for i in ids]}
    return {"id": "chatcmpl", "choices": [{"finish_reason": "stop",
                                           "message": {"content": json.dumps(body, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 1200, "completion_tokens": 300,
                      "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 1200}}


BALANCE = {"is_available": True,
           "balance_infos": [{"currency": "CNY", "total_balance": "8.55", "granted_balance": "0.00",
                              "topped_up_balance": "8.55"}]}

TOOLS_SEARCH = {"results": [{"title": "t", "url": "https://www.miit.gov.cn/a", "snippet": "s"}], "total": 1}
TOOLS_FETCH = {"content": EVIDENCE_TEXT * 3,
               "title": "国家医保局发布DRG/DIP医保智能监管标准"}


class FakeHTTPResponse:
    def __init__(self, body, status=200):
        self._body = body.encode("utf-8") if isinstance(body, str) else body
        self.status_code = status
        self.ok = status < 400
        self.headers = {"content-type": "text/html"}
        self.text = self._body.decode("utf-8", "replace")
        self.content = self._body

    def read(self, *a, **k):
        return self._body

    def json(self):
        return json.loads(self._body.decode("utf-8"))

    def iter_content(self, chunk_size=1):
        yield self._body

    def close(self):
        return None

    def raise_for_status(self):
        if self.status_code >= 400:
            raise urllib.error.HTTPError("url", self.status_code, "err", {}, None)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def requests_router(url, **kwargs):
    u = url
    pub = _recent_rfc822()
    if "chinanews.com.cn/rss" in u:
        return FakeHTTPResponse(RSS.format(title="中新网", pub=pub,
                                           t1="国家医保局发布DRG/DIP医保智能监管标准",
                                           d1=EVIDENCE_TEXT,
                                           t2="欧盟三季度货物贸易数据发布 中国为最大进口来源国",
                                           d2="今年三季度欧盟货物贸易进口总额7300亿欧元，中国是欧盟货物贸易最大进口来源国。"))
    if "feeds.bbci.co.uk" in u or "aljazeera.com" in u:
        return FakeHTTPResponse(RSS.format(title="World", pub=pub,
                                           t1="Government announces international treaty ruling",
                                           d1="The government published an international treaty ruling today.",
                                           t2="Central bank warns of trade uncertainty",
                                           d2="The central bank warned of trade uncertainty."))
    if "miit.gov.cn" in u and "art" not in u:
        return FakeHTTPResponse(OFFICIAL_LIST)
    if "miit.gov.cn" in u:
        return FakeHTTPResponse(OFFICIAL_DETAIL)
    if "nhsa.gov.cn" in u and "art" not in u:
        return FakeHTTPResponse(NHSA_LIST)
    if "nhsa.gov.cn" in u:
        return FakeHTTPResponse(NHSA_DETAIL)
    if "esearch.fcgi" in u:
        return FakeHTTPResponse(json.dumps(PUBMED_SEARCH))
    if "efetch.fcgi" in u:
        return FakeHTTPResponse(PUBMED_XML)
    return FakeHTTPResponse("<html><body>ok</body></html>")


def urllib_router(url, data=None, timeout=None, **kwargs):
    u = url
    if u.endswith("/user/balance"):
        return FakeHTTPResponse(json.dumps(BALANCE))
    if "/chat/completions" in u:
        payload = json.loads(data.decode("utf-8")) if data else {}
        return FakeHTTPResponse(json.dumps(_model_response(payload), ensure_ascii=False))
    if "/tools/search" in u:
        return FakeHTTPResponse(json.dumps(TOOLS_SEARCH))
    if "/tools/fetch" in u:
        return FakeHTTPResponse(json.dumps(TOOLS_FETCH))
    raise RuntimeError("unexpected url " + u)


class EveningEndToEnd(TestCase):
    """Full retrieval -> HTML run through the real production entry."""

    def test_scheduled_production_delivers_once_with_persistent_state(self):
        self.check_scheduled_delivery(status_key="offline-access")

    def test_scheduled_token_only_submission_is_held_without_retry(self):
        self.check_scheduled_delivery(status_key="")

    def check_scheduled_delivery(self, *, status_key):
        from pni.editions import EDITIONS
        from pni.production import exclusion_history, main

        ledger = {"version": 1}
        deliveries = []

        def http(method, url, **kwargs):
            if "api.github.com/" in url:
                if method == "get":
                    return FakeHTTPResponse(json.dumps({
                        "sha": "mock-sha", "content": b64encode(json.dumps(ledger).encode()).decode()}))
                if method == "put":
                    self.assertEqual(kwargs["json"]["branch"], "pni-production-state")
                    ledger.update(json.loads(b64decode(kwargs["json"]["content"])))
                    return FakeHTTPResponse('{"content":{"sha":"mock-sha"}}')
            if url.endswith("/send"):
                deliveries.append(kwargs["json"])
                return FakeHTTPResponse('{"code":200,"data":"receipt-test-only"}')
            if url.endswith("/sendMessageResult"):
                self.assertTrue(status_key, "Token-only delivery must skip the open API")
                return FakeHTTPResponse('{"code":200,"data":{"status":2}}')
            self.assertNotIn("www.pushplus.plus", url, "Unexpected PushPlus request")
            return requests_router(url, **kwargs)

        with TemporaryDirectory() as tmp:
            env = {"GITHUB_EVENT_NAME": "schedule", "GITHUB_REF": "refs/heads/main",
                   "GITHUB_REPOSITORY": "owner/repo", "GITHUB_RUN_ID": "123",
                   "DEEPSEEK_API_KEY": "offline-key", "PUSHPLUS_TOKEN": "offline-token",
                   "GH_TOKEN": "offline-github",
                   "RUNNER_TEMP": tmp, "PNI_TRIAL_START": "2000-01-01",
                   "PNI_SCHEDULE_CRON": EDITIONS["evening"]["cron"]}
            if status_key:
                env["PUSHPLUS_ACCESS_KEY"] = status_key
            with patch.dict("os.environ", env, clear=True), \
                    patch("requests.sessions.Session.request", side_effect=http), \
                    patch("urllib.request.urlopen",
                          side_effect=lambda req, timeout=0: urllib_router(req.full_url, data=req.data)) as deepseek:
                main()
                paid_boundary_count = deepseek.call_count
                main()
                self.assertEqual(deepseek.call_count, paid_boundary_count)

            summary = json.loads((Path(tmp) / "pni/run-summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["edition"], "evening")
            self.assertEqual(summary["reserved_micro_cny"], 1_000_000)
            self.assertEqual(summary["delivery"]["state"],
                             "sent" if status_key else "accepted_waiting_status")
            self.assertEqual(len(deliveries), 1)
            self.assertEqual(deliveries[0]["template"], "html")
            self.assertIn(summary["day"] + ":evening", ledger["runs"])
            self.assertEqual(bool(ledger.get("history")), bool(status_key))
            self.assertTrue(exclusion_history(ledger))
            if not status_key:
                part = summary["delivery"]["lanes"]["part-1"]
                self.assertEqual(part["status"], "accepted")
                self.assertEqual(part["verification"], "status_access_key_missing")
                self.assertTrue(part["pending_keys"])

    def test_evening_production_entry_completes_offline(self):
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "evening"
            env = {"DEEPSEEK_API_KEY": "offline-key", "TAVILY_KEY": ""}
            with patch.dict("os.environ", env, clear=False), \
                    patch("requests.sessions.Session.request",
                          side_effect=lambda method, url, **kw: requests_router(url, **kw)), \
                    patch("urllib.request.urlopen",
                          side_effect=lambda req, timeout=0: urllib_router(
                              req.full_url if hasattr(req, "full_url") else req,
                              data=getattr(req, "data", None), timeout=timeout)):
                execute("preview", out, "evening")

            # HTML deliverable must exist and carry rendered content.
            html_files = list(out.glob("news-preview/part-*.html"))
            self.assertTrue(html_files, "part HTML must be produced")
            self.assertGreater(html_files[0].stat().st_size, 400,
                               "HTML must carry rendered content")
            body = html_files[0].read_text(encoding="utf-8")
            self.assertIn("晚间实时新闻", body, "evening edition must be rendered")
            self.assertIn("发生了什么", body, "the three evening fields must render")

            # Markdown briefs must exist.
            for name in ("professional-automatic.md", "extended-automatic.md"):
                self.assertTrue((out / "news-preview" / name).exists(), name)

            # run-summary must be written with an audit trail.
            summary = json.loads((out / "run-summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["mode"], "preview")
            self.assertGreater(summary["model_calls"], 0, "real model calls must have run")
            self.assertTrue(summary["run_audit"].get("raw_calls"),
                            "generation raw responses must be persisted")
            self.assertTrue(summary["run_audit"].get("channel_contribution"),
                            "channel contribution must be recorded")
            self.assertIn("source_report", summary["run_audit"])
            self.assertEqual(summary.get("errors", summary["run_audit"].get("gate")) is not None, True)

            # No internal function may have been replaced: the public digest
            # must reflect real RSS parsing (source labels present).
            contribution = summary["run_audit"]["channel_contribution"]
            self.assertTrue(contribution, "channels must be counted")

    def test_morning_production_entry_completes_offline(self):
        """Morning stays frozen and must still run to completion."""
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "morning"
            env = {"DEEPSEEK_API_KEY": "offline-key", "TAVILY_KEY": ""}
            with patch.dict("os.environ", env, clear=False), \
                    patch("requests.sessions.Session.request",
                          side_effect=lambda method, url, **kw: requests_router(url, **kw)), \
                    patch("urllib.request.urlopen",
                          side_effect=lambda req, timeout=0: urllib_router(
                              req.full_url if hasattr(req, "full_url") else req,
                              data=getattr(req, "data", None), timeout=timeout)):
                execute("preview", out, "morning")
            self.assertTrue(list(out.glob("news-preview/part-*.html")),
                            "morning HTML must be produced")
            summary = json.loads((out / "run-summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["mode"], "preview")
            self.assertGreater(summary["model_calls"], 0)
