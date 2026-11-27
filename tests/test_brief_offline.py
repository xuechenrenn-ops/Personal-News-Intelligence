"""Frozen evidence, editorial copy and scripted rank/verification decisions for offline replay."""
from datetime import date, datetime, timezone
from email.utils import format_datetime
from html.parser import HTMLParser
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from experiments.run import Flash
from experiments.discover import collect_live_cards
from experiments.v14.pipeline import prepare_preview
from pni.presentation import document, measure, plan, render
from pni.production import DAILY, TOOLS, TOOL_PRICE, V17_MODEL_REQUESTS
from pni.review_v17 import reviewed_messages


DAY = "2026-11-26"


def paper(pmid, day, title, journal, methods, result, region):
    return dict(id=pmid, pmid=pmid, date=day, lane="medical", title=title, source=journal,
                region=region, evidence=f"METHODS: {methods} RESULTS: {result}",
                primary=True, url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/")


def news(key, title, evidence, url, source, lane="world"):
    return dict(id=key, date=DAY, lane=lane, title=title, source=source, primary=True,
                url=url, evidence=evidence, official=True, source_scope="官方原文节选")


ORTHO = paper(
    "43069377", "2026-11-23", "Performance of a self-supervised pretrained neural network for orthopedic radiograph classification.",
    "Skeletal Radiology", "Retrospective comparison of 3 ways to initialize ResNet-50: supervised ImageNet, self-supervised ImageNet (DINO) and DINO adapted using 44,029 musculoskeletal radiographs. External calcaneal dataset: 5,365 images in 1,775 patients.",
    "No significant AUROC difference between initializations. Domain adaptation: in-distribution AUROC 0.82, 95% CI 0.79 to 0.84. External calcaneal AUROCs for control, DINO and DINO-Ortho respectively: 0.65, 0.64 and 0.64. Saliency ROAD score: domain-adapted weights exceeded supervised control, but did not outperform DINO on the external dataset.",
    "美国、加拿大")
MED = paper(
    "43046223", "2026-11-17", "AI-Assisted Ni Classification in Nasopharyngeal NBI: Multicenter Validation and Reader Study.",
    "The Laryngoscope", "Retrospective multicenter dataset of 5,404 narrow-band endoscopy images. Separate segmentation and 5-class classification networks. Internal testing, 2 independent external cohorts and recorded-video validation. Randomized prospective crossover reader study: 12 physicians and 200 images.",
    "External accuracy 0.889 and 0.873, AUC 0.976 and 0.967. Video accuracy 0.888, AUC 0.974. Potentially unnecessary biopsy decisions fell in junior readers from 21.6% to 10.3% and in senior readers from 13.7% to 8.0%.",
    "中国")
RCT = paper(
    "43085541", "2026-11-25", "Short-term effects of cognitive-behavioral physical therapy after arthroscopic rotator cuff repair: A randomized controlled trial.",
    "Orthopaedics & Traumatology: Surgery & Research", "68 participants randomized after arthroscopic rotator cuff repair; 55 completed follow-up and were included in the complete-case analysis. Cognitive-behavioral physical therapy once weekly for 4 sessions versus standard rehabilitation. Follow-up at 3 and 6 months.",
    "At 3 months the CBPT group had lower activity-related VAS scores: 2.8 ± 2.0 vs 4.4 ± 1.5; P = 0.002. Constant-Murley Score differed (P = 0.007). At 6 months neither co-primary outcome differed significantly.",
    "中国")
POLICY = news(
    "policy-NHSA", "国家医保局上线连续用药管理功能",
    "国家医保局上线连续用药管理功能。参保人可查询用药及购药记录，覆盖全国所有定点医疗机构和定点零售药店，就医时可授权接诊医生查阅既往用药。数据由各地医保结算系统归集，频率为T+1。",
    "https://www.nhsa.gov.cn/art/2026/11/26/art_14_23530.html", "国家医保局", "policy")
SPACE = news(
    "space", "神舟二十四号乘组进驻空间站",
    "北京时间11月25日14时35分，神舟二十四号乘组进驻空间站。神舟二十三号乘组迎接3名航天员，中外航天员首次在中国空间站会面。本次是首次有外籍航天员参与的中国载人飞行任务。",
    "https://statistics.cmse.gov.cn/xwzx/202611/t20261125_57903.html", "中国载人航天工程办公室") | {"date": "2026-11-25"}
LOW = news("labels", "食品标签争议获例行回应", "主管部门回应个别瓶身标签争议。",
           "https://example.org/consumer", "回归反例")


def scholarly(title, lead, question, methods, result, limitations, clinical, insight):
    return dict(title=title, lead=lead, question=question, methods=methods, result=result,
                limitations=limitations, clinical=clinical, insight=insight,
                refs={"lead": ["m", "r"], "question": ["m"], "methods": ["m"], "result": ["r"],
                      "limitations": ["m", "r"], "clinical": ["m", "r"], "insight": ["m", "r"]})


def event(lead, summary, why, impact):
    return dict(lead=lead, summary=summary, why=why, impact=impact,
                refs={key: ["s"] for key in ("lead", "summary", "why", "impact")})


COPY = {
    ORTHO["id"]: scholarly(
        "给骨折识别模型补上骨科预训练，能提高判别力吗？",
        "骨科影像预训练让热力图更贴合模型的判读依据，但未提高骨折判别力，遇到陌生解剖部位仍表现欠佳。",
        "使用肌骨X线片做自监督预训练，能否提升骨折判别力和模型归因可信度？",
        "回顾性研究比较3种ResNet-50初始化方式：ImageNet有监督、ImageNet自监督和用44,029张肌骨X线片补训。外部跟骨数据来自1,775名患者，共5,365张影像。",
        "骨科预训练未显著提高判别力。熟悉分布的曲线下面积（AUROC）为0.82（95%置信区间0.79至0.84）；外部跟骨影像上，三种模型仅为0.65、0.64、0.64。归因指标总体优于有监督基线，外部集未优于通用自监督模型。",
        "回顾性数据和特定外部解剖部位限制泛化，归因分数不等于临床可用性。",
        "可将归因图用于研究阶段排查模型学到的错误线索。陌生部位上的性能仍需单独验证。",
        "按解剖部位与机构留出外部集，比较本地微调前后敏感性与误诊类型，并分开评估归因和准确性。"),
    MED["id"]: scholarly(
        "鼻咽内镜分型加上AI，阅片活检判断如何变化？",
        "鼻咽内镜AI在两个外部队列表现接近，在阅片实验中减少了可能不必要的活检决定，真实诊疗效果仍待验证。",
        "AI能否辅助鼻咽窄带内镜分型，并改善医生阅片时的活检判断？",
        "多中心回顾性收集5,404张鼻咽窄带内镜图像，分别训练病灶分割与分型网络，进行内部、独立外部和录像验证。随后由12名医生对200张图像开展前瞻性随机交叉阅片。",
        "两个外部队列的准确率为0.889和0.873，AUC为0.976和0.967；录像验证准确率为0.888。阅片实验中，可能不必要的活检决定在低年资组由21.6%降至10.3%，高年资组由13.7%降至8.0%。",
        "阅片用的是图像，观察终点包括医生的活检决定，实际活检、漏诊及患者结局未在此设计中评估。",
        "可作为鼻咽内镜第二阅片者的候选工具，临床应用仍需核对错误分型和漏诊。",
        "在多中心真实门诊做前瞻验证，以病理或随访为参照，同时记录活检率、漏诊率及读片时间。"),
    RCT["id"]: scholarly(
        "肩袖修复后加入认知行为康复，早期疼痛有何变化？",
        "肩袖修复后加入认知行为康复，活动痛在三个月时较低，六个月未见主要结局显著组间差异。",
        "关节镜肩袖修复术后，在标准康复基础上加入认知行为物理治疗是否改善疼痛与功能？",
        "随机试验纳入68名术后患者，55名完成随访并进入完整病例分析。认知行为物理治疗每周一次，共4次，与标准康复对照，评估3个月和6个月结局。",
        "3个月时，干预组活动痛评分为2.8±2.0，对照组为4.4±1.5（P=0.002）；Constant–Murley肩关节评分也存在组间差异（P=0.007）。6个月时，两项共同主要结局均未见显著组间差异。",
        "完整病例分析对失访敏感，短期组间差异不足以判断长期收益。",
        "可能影响术后早期的疼痛体验，六个月结果提示需避免把早期改善外推到长期功能。",
        "后续试验可预设意向性分析，并分层评估术前疼痛灾难化程度是否修饰获益。"),
    POLICY["id"]: event(
        "医保上线连续用药管理，参保人可查询购药记录，并授权接诊医生查看既往用药。",
        "国家医保局上线连续用药管理功能，参保人可查询在定点医疗机构和定点零售药店的用药、购药记录，就医时可授权接诊医生查看既往用药。",
        "记录来自医保结算系统，可补充跨机构就诊时的用药信息。",
        "患者可在就诊前核对记录，医生可据授权辅助问诊。记录基于医保结算，仍需核对患者实际用药。"),
    SPACE["id"]: event(
        "神舟二十四号乘组进驻中国空间站，中外航天员首次在站内会面。",
        "北京时间11月25日14时35分，神舟二十四号乘组进驻中国空间站，神舟二十三号乘组迎接新乘组，中外航天员首次在站内会面。",
        "这是首次有外籍航天员参加中国空间站载人任务，国际合作进入在轨执行阶段。",
        "可跟踪后续公布的在轨任务和科学合作进展，通报目前确认的是乘组进驻。"),
}


class PayloadStyles(HTMLParser):
    """Check declared color pairs on the sent fragment against either client background."""
    def __init__(self, host):
        super().__init__()
        self.stack = [("#222", host)]
        self.contrasts = []

    def handle_starttag(self, tag, attrs):
        props = dict(pair.split(":", 1) for pair in dict(attrs).get("style", "").split(";") if ":" in pair)
        foreground, background = self.stack[-1]
        self.stack.append((props.get("color", foreground), props.get("background-color", props.get("background", background))))

    def handle_endtag(self, tag):
        self.stack.pop()

    def handle_data(self, data):
        if not data.strip():
            return
        def luminance(hex_color):
            digits = hex_color.lstrip("#")
            if len(digits) == 3:
                digits = "".join(n * 2 for n in digits)
            rgb = [int(digits[i:i + 2], 16) / 255 for i in range(0, 6, 2)]
            linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in rgb]
            return sum(c * weight for c, weight in zip(linear, (.2126, .7152, .0722)))
        bright, dark = sorted(map(luminance, self.stack[-1]), reverse=True)
        self.contrasts.append((bright + .05) / (dark + .05))


def declared_contrast(payload):
    values = {}
    for host in ("#fff", "#222"):
        parser = PayloadStyles(host)
        parser.feed(payload)
        values[host] = round(min(parser.contrasts), 2)
    return values


class ScriptedEditor:
    def __init__(self, copy=None, events=("space",), reject=(), weak=(), raise_first_rank=False):
        self.copy = COPY if copy is None else copy
        self.events = set(events)
        self.reject = set(reject)
        self.weak = set(weak)
        self.raise_first_rank = raise_first_rank

    def __call__(self, phase, prompt, items, **kwargs):
        if "score" in phase:
            if phase == "v14-score" and self.raise_first_rank:
                raise RuntimeError("offline rank failure")
            return [{"id": row["id"], "scores": [1] * 6 if row["id"] in self.weak else [4] * 6}
                    for row in items["papers"]] + [
                        {"id": row["id"], "news_value": 5, "event": row["title"]} for row in items["world"]
                        if row["id"] in self.events]
        if phase == "v14-generate":
            return [json.loads(json.dumps({"id": row["id"], **self.copy[row["id"]]}))
                    for row in items if row["id"] in self.copy]
        if phase == "v14-verify":
            return [{"id": block["record"]["id"], "valid": list(block["proposed"])}
                    for block in items if block["record"]["id"] not in self.reject]
        return []


def replay(folder, editor=None, cards=None, headlines=None):
    return prepare_preview(folder / "result.json", editor or ScriptedEditor(), final_edit=True, v17=True,
                           evaluation=(DAY, list(cards if cards is not None else (ORTHO, MED, RCT)),
                                       list(headlines if headlines is not None else (POLICY, SPACE, LOW)), {}))


class OfflineBriefTests(TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        requests = patch("requests.sessions.Session.request", side_effect=AssertionError("unexpected network call"))
        requests.start()
        self.addCleanup(requests.stop)

    def test_full_replay_preserves_evidence_and_source_links(self):
        result = replay(self.folder)
        self.assertEqual(result["counts"], {"medical": 3, "policy": 1, "world": 1})
        self.assertEqual(result["selection"]["missing_professional"], [])
        self.assertEqual(result["candidate_audit"]["news"]["labels"]["outcome"], "low_public_impact")
        self.assertEqual(result["gate"][RCT["id"]]["result"], "accepted")
        self.assertEqual(result["gate"][MED["id"]]["lead"], "accepted")
        briefs = self.folder / "news-preview"
        parts = plan(*(p.read_text(encoding="utf-8") for p in
                       (briefs / "professional-automatic.md", briefs / "extended-automatic.md")), date.fromisoformat(DAY))
        self.assertEqual(sum(len(p.articles) for p in parts), 5)
        html = "".join(part.html for part in parts)
        self.assertIn("background-color:#fff", html)
        self.assertIn("4.4±1.5", html)
        self.assertIn("六个月未见主要结局显著组间差异", html)
        self.assertEqual(html.count(COPY[MED["id"]]["lead"]), 1)
        self.assertIn(SPACE["url"], html)
        self.assertEqual(len(set(declared_contrast(html).values())), 1)
        self.assertGreaterEqual(min(declared_contrast(html).values()), 4.5)

    def test_rejected_guideline_gets_clinical_replacement_and_later_world_batch(self):
        guideline = RCT | {"id": "guide", "pmid": "guide", "url": "https://example.org/guide",
                           "title": "Clinical practice guideline for rotator cuff rehabilitation"}
        distractors = [news(f"n-{i}", f"地区议会发布例行财政记录第{i}号", "财政简报，没有新政策。",
                            f"https://example.org/{i}", f"source-{i}") for i in range(20)]
        editor = ScriptedEditor(COPY | {"guide": COPY[RCT["id"]]}, reject={"guide"})
        result = replay(self.folder, editor, [ORTHO, MED, guideline, RCT], [POLICY, *distractors, SPACE])
        self.assertEqual(result["counts"], {"medical": 3, "policy": 1, "world": 1})
        audit = result["candidate_audit"]
        self.assertEqual(audit["medical"]["guide"]["outcome"], "semantic_verification_failed")
        self.assertEqual(audit["medical"][RCT["id"]]["outcome"], "published")
        self.assertNotIn("space", audit["rank_batches"][0]["world"])
        self.assertIn("space", audit["rank_batches"][1]["world"])

    def test_weak_study_and_unverified_story_leave_auditable_gaps(self):
        editor = ScriptedEditor(weak={MED["id"]}, reject={"space"})
        result = replay(self.folder, editor)
        self.assertEqual(result["counts"], {"medical": 2, "policy": 1, "world": 0})
        self.assertEqual(result["selection"]["missing_professional"], ["medical-ai"])
        self.assertEqual(result["selection"]["world_omission"], "ranked_event_summary_not_verified")
        self.assertEqual(result["candidate_audit"]["news"]["space"]["outcome"], "semantic_verification_failed")

    def test_failed_rank_and_invalid_numeric_lead(self):
        editor = ScriptedEditor(COPY | {POLICY["id"]: COPY[POLICY["id"]] |
                                {"lead": "医保上线连续用药管理，声称覆盖全球99个国家和地区。"}}, raise_first_rank=True)
        result = replay(self.folder, editor)
        self.assertEqual(result["candidate_audit"]["rank_batches"][0]["error"], "RuntimeError")
        self.assertNotEqual(result["gate"][POLICY["id"]]["lead"], "accepted")

    def test_rank_write_verify_twice_under_real_flash_reservation_guard(self):
        guide = RCT | {"id": "guide", "pmid": "guide", "url": "https://example.org/guide",
                       "title": "Clinical practice guideline for rotator cuff rehabilitation"}
        distractors = [news(f"n-{i}", f"地区议会公布财政档案第{i}号", "地区档案整理，没有公布新政策。",
                            f"https://example.org/{i}", f"source-{i}") for i in range(20)]
        editor = ScriptedEditor(COPY | {"guide": COPY[RCT["id"]]}, reject={"guide"})
        provider = Flash("offline-placeholder", DAILY - TOOLS * TOOL_PRICE,
                         max_reservation=DAILY, max_requests=V17_MODEL_REQUESTS)

        def api(url, headers, payload):
            packet = json.loads(payload["messages"][1]["content"])
            phase = ("v14-score" if isinstance(packet, dict) else
                     "v14-verify" if "record" in packet[0] else "v14-generate")
            answer = editor(phase, "", packet)
            return {"usage": {"prompt_tokens": 1, "completion_tokens": 1},
                    "choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"items": answer})}}]}

        with patch("experiments.run.api_json", side_effect=api):
            result = replay(self.folder, provider, [ORTHO, MED, RCT, guide], [POLICY, *distractors, SPACE])
        self.assertEqual(len(provider.calls), 6)
        self.assertGreaterEqual(provider.remaining, 0)
        self.assertEqual(result["counts"], {"medical": 3, "policy": 1, "world": 1})

    def test_live_collectors_parse_rss_and_official_date_path_with_mocked_http(self):
        now = datetime.now(timezone.utc)
        datestamp = now.strftime("%Y%m%d")
        path = f"/xwzx/{datestamp[:6]}/t{datestamp}_12345.html"
        title = "载人任务发布新的在轨科学合作进展"
        listing = f'<a href="{path}">{title}</a>'
        detail = '<p>' + "工程办公室公布本次在轨科学合作的任务进展，空间站乘组已完成交接，后续安排以正式通报为准。" * 3 + '</p>'
        rss = (f'<rss version="2.0"><channel><title>示例新闻</title><item><title>国际研发合作协议生效</title>'
               f'<link>https://example.org/news</link><pubDate>{format_datetime(now)}</pubDate>'
               '<description>两家机构发布联合研究合作公告。</description></item></channel></rss>')

        def page(url, **kwargs):
            body = detail if url.endswith(path) else listing if "cmse.gov.cn" in url else rss
            return Mock(status_code=200, text=body, content=body.encode())

        trace = []
        with patch("news_push.requests.get", side_effect=page):
            cards, _ = collect_live_cards(skip_official=True, include_academic=False, wider=True,
                                          major_official=True, discovery=trace)
        self.assertTrue(any(row.get("official") and row["title"] == title for row in cards))
        self.assertTrue(any(row["url"] == "https://example.org/news" for row in cards))
        self.assertTrue(any(row["outcome"] == "duplicate_title" for row in trace))

    def test_expands_missing_slot_even_when_low_value_topic_is_present(self):
        weak = MED | {"id": "trial-use", "pmid": "trial-use",
                      "title": "AI-assisted assessment of patient satisfaction in a preliminary clinical pilot",
                      "url": "https://example.org/low-value-pilot"}
        with (patch("experiments.v14.pipeline.collect_live_cards", return_value=([POLICY, SPACE], DAY)),
              patch("experiments.v14.pipeline.collect", return_value=({DAY: [ORTHO, RCT, weak]}, {})),
              patch("pni.curation.enrich_open_results", return_value=[]),
              patch("pni.curation.widen", return_value=([MED], {"medical-ai:30d": {"verified": 1}})) as widen):
            result = prepare_preview(self.folder / "result.json", ScriptedEditor(weak={"trial-use"}),
                                     final_edit=True, source_state=self.folder / "health.json", v17=True)
        self.assertEqual(widen.call_args.args[-1], {"medical-ai"})
        self.assertEqual(result["candidate_audit"]["medical"]["trial-use"]["outcome"], "below_quality_floor")
        self.assertEqual(result["candidate_audit"]["medical"][MED["id"]]["outcome"], "published")
        self.assertEqual(result["selection"]["missing_professional"], [])

    def test_manual_review_preserves_a_verified_clinical_paper(self):
        result = replay(self.folder)
        selected = [ORTHO, MED, RCT]
        with patch("pni.review_v17._pubmed_records",
                   return_value=[{"pmid": card["id"], "abstract": card["evidence"]} for card in selected]):
            messages = reviewed_messages(self.folder, {**result, "day": DAY}, [card["id"] for card in selected])
        self.assertIn("骨科临床", messages[0].html)
        self.assertEqual(sum(len(message.articles) for message in messages), 5)


def write_product(folder):
    result = replay(folder)
    briefs = folder / "news-preview"
    parts = plan(*(p.read_text(encoding="utf-8") for p in
                   (briefs / "professional-automatic.md", briefs / "extended-automatic.md")), date.fromisoformat(DAY))
    articles = tuple(article for part in parts for article in part.articles)
    (folder / "brief.html").write_text(document(render(articles, date.fromisoformat(DAY),
                                                      context=" · 离线验收样稿")), encoding="utf-8")
    for i, part in enumerate(parts, 1):
        (folder / f"message-{i}.html").write_text(document(part.html), encoding="utf-8")
    result["messages"] = [measure(part.html) for part in parts]
    result["declared_contrast"] = [declared_contrast(part.html) for part in parts]
    (folder / "audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    write_product(Path(sys.argv[1]))
