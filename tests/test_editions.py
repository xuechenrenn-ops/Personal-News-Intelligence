"""Offline contract for two shared-pipeline editions and their final payloads."""
from datetime import date, datetime
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from experiments.discover import CST, TECH_SOURCE, medical_card
from experiments.run import Flash
from experiments.v15.sources import TOOL_PRICE
from experiments.v14.pipeline import copy_packet, prepare_preview, write_verified
from news_push import _pubmed_records, fetch_official_page_candidates
from pni.editions import EDITIONS
from pni.presentation import document, measure, plan_edition
from pni.production import DAILY, MONTHLY, FILES, claim, deliver_html, edition_messages, exclusion_history, main
from tests.test_brief_offline import COPY, DAY, LOW, MED, ORTHO, POLICY, RCT, SPACE, ScriptedEditor, event, news
from tests.test_pni_production import Ledger


DEEP_MED = MED | {"original_abstract": (
    "OBJECTIVE: Develop AI for standardized Ni classification of nasopharyngeal narrow-band imaging and evaluate diagnostic consistency and biopsy decisions. "
    + MED["evidence"] + " The recorded-video AUC was 0.974. Fleiss kappa increased from 0.610 to 0.788 for junior physicians and from 0.744 to 0.870 for senior physicians (both P < 0.05). "
    "CONCLUSION: The validations showed consistency in model performance. Physicians agreed more often with assistance, and chose fewer potentially unnecessary biopsies during the reader experiment. This warrants further assessment of computer assistance as a second reader."
)}
ABSTRACT_ORTHO = ORTHO | {"original_abstract": (
    "OBJECTIVE: To evaluate whether domain-adaptive self-supervised pretraining of a convolutional neural network on musculoskeletal radiographs improves binary fracture classification and attribution faithfulness relative to ImageNet-pretrained baselines. "
    "MATERIALS AND METHODS: This retrospective study (conducted June 2025 to May 2026) compared three initializations of a ResNet-50 convolutional neural network: supervised ImageNet (control), self-supervised ImageNet (DINO), and DINO further pretrained on 44,029 musculoskeletal radiographs (DINO-Ortho). Each was fine-tuned in three experiments: in-distribution (MURA and FracAtlas), out-of-distribution (5365 external calcaneal radiographs from 1775 patients acquired during 2015-2022), and initial weights (calcaneal fine-tuning). Attribution faithfulness was quantified using Remove and Debias (ROAD) scores from Grad-CAM saliency maps, with higher scores indicating greater faithfulness. AUROCs were compared using DeLong tests, and ROAD scores using Friedman tests with Bonferroni-adjusted post hoc pairwise comparisons. "
    "RESULTS: DINO-Ortho AUROC did not differ significantly from either baseline in any experiment (0.82 [95% confidence interval, 0.79-0.84] in-distribution and 0.95 [0.93-0.96] with initial weights). DINO-Ortho ROAD scores were significantly higher than both baselines in-distribution (20.01 vs 3.70 and 9.35, respectively) and with initial weights (20.41 vs 12.15 and 1.67, respectively, P < 0.001 for all) and higher than control but not DINO out-of-distribution (15.47 vs 3.06 and 16.09, respectively). All models discriminated poorly out-of-distribution (AUROC 0.65 [0.63-0.67], 0.64 [0.63-0.66], and 0.64 [0.63-0.66] for control, DINO, and DINO-Ortho, respectively). "
    "CONCLUSION: With no significant difference in AUROC from either baseline, domain-adapted self-supervised pretraining yielded more faithful attributions than supervised ImageNet pretraining in every experiment and than self-supervised ImageNet pretraining in two of three. No model generalized adequately to unfamiliar anatomy without fine-tuning. Groups fine-tuning on their own musculoskeletal data should therefore consider domain-adapted weights, whose saliency maps more reliably show what drove each prediction, making reliance on irrelevant features easier to detect."
)}
ABSTRACT_RCT = RCT | {"original_abstract": (
    "BACKGROUND: Pain-related beliefs and behavior may matter after arthroscopic repair. HYPOTHESIS: Adding CBPT to standard rehabilitation may improve early movement-related pain, shoulder function and pain psychology. "
    "METHODS: Single-center parallel randomized trial in adults aged 18 to 75 undergoing repair of primary full-thickness supraspinatus tears. Allocation 1:1. The added program had 6 sessions: one before surgery, one before discharge and four weekly after discharge. The control received standard rehabilitation with no matched extra contact. Prespecified co-primary outcomes were activity-related VAS and Constant-Murley score at baseline, 3 and 6 months; psychological scales and SF-12 were secondary outcomes. Longitudinal analysis used mixed-design ANOVA. "
    "RESULTS: 68 randomized, 55 in complete-case analysis (26 intervention, 29 control). Group-time interactions: VAS P=0.023, Constant-Murley P=0.013 and SF-12 physical P=0.018. At 3 months, activity VAS was 2.8 ± 2.0 vs 4.4 ± 1.5 (P=0.002), with a higher shoulder score (P=0.007) and an SF-12 physical score difference (P=0.025). Worst pain was descriptively higher in the intervention group. Some 3-month psychological findings were exploratory without significant group-time interactions. At 6 months, co-primary outcomes did not differ; negative pain thoughts score still differed (P=0.007). "
    "DISCUSSION: The program was associated with better 3-month activity pain and total Constant-Murley score, with no evidence of sustained difference in either co-primary outcome at 6 months. Unmatched contact leaves the cognitive-behavioral component inseparable. The study does not establish superior overall pain control or final surgical outcomes."
)}
TECH = news(
    "network", "三部门印发新一代通信网建设实施方案",
    "工业和信息化部、国家网信办、国家发展改革委印发新一代通信网建设实施方案。方案提出以移动通信演进、万兆光网、卫星互联网等为支撑，统筹基础、空间、国际和融合网络建设，适时开展第六代移动通信网络建设。",
    "https://www.miit.gov.cn/jgsj/txs/gzdt/art/2026/art_24332ac106be4f28ac594c05439dcbe1.html", "工业和信息化部")
SPACE_NEWS = SPACE | {"evidence": "飞船与空间站组合体完成交会对接后，乘组从飞船进入空间站。 " + SPACE["evidence"]}
SAMPLE = COPY | {
    ORTHO["id"]: {
        "title": "骨科影像自监督预训练能否提高骨折分类性能？",
        "lead": "肌骨影像预训练可以改善模型的归因表现，却未显著提高骨折识别性能；针对目标解剖部位进行微调仍然重要。",
        "question": "探讨利用肌骨X线片开展领域自监督预训练，能否在提高骨折识别准确性的同时，改善模型归因的可信度。",
        "result": (
            "研究者采用ResNet-50构建骨折分类模型，比较ImageNet有监督预训练、通用自监督预训练，以及利用44,029张肌骨X线片进一步开展领域自监督预训练三种策略。\n"
            "在同分布数据上，领域预训练模型的AUROC为0.82，与其他两种策略无显著差异。为进一步评价跨解剖部位的泛化能力，研究者使用来自1,775名患者的5,365张跟骨影像进行验证，三种模型的AUROC均为0.64–0.65。\n"
            "利用跟骨数据进行微调后，领域预训练模型的AUROC达到0.95（95% CI：0.93–0.96），但与其他预训练策略仍无显著差异。此外，领域预训练在同分布及跟骨微调实验中的归因忠实度显著更高。"),
        "conclusion": "肌骨影像领域预训练能够改善模型归因的可信度，但尚未显示出额外的骨折分类性能优势。",
        "evaluation": "模型在陌生解剖部位的泛化能力仍然有限，需要结合目标部位的数据进行适配。",
        "refs": {key: ["u"] for key in ("question", "result", "conclusion", "evaluation", "lead")},
    },
    MED["id"]: {
        "title": "鼻咽窄带内镜的Ni分型：AI外部验证与医生交叉阅片",
        "lead": "将深度学习与鼻咽窄带内镜相结合，可提高医生的分型一致性，并减少阅片实验中潜在不必要的活检决策。",
        "question": "开发基于病灶分割和五分类网络的鼻咽窄带内镜AI系统，验证其Ni分型表现，并评估辅助阅片能否改变医生的分型一致性和活检判断。",
        "result": (
            "研究回顾性收集多中心5,404张鼻咽窄带内镜图像，分别训练病灶分割和Ni五分类网络。为评估模型自身的分型表现，研究设置内部测试集及两个独立外部队列。两个外部队列的准确率分别为0.889和0.873，AUC分别为0.976和0.967。研究另用录制录像考察不同输入形式下的性能，准确率为0.888，AUC为0.974。\n"
            "研究另以前瞻性随机交叉阅片评估AI对医生判读的影响，纳入12名医生和200张图像。AI辅助时，低、高年资组的Fleiss κ分别由0.610升至0.788、由0.744升至0.870（均P<0.05）。被判为可能不必要的活检决定比例，低年资组由21.6%降至10.3%，高年资组由13.7%降至8.0%。"),
        "conclusion": "模型在验证队列中的分型表现较为一致，AI辅助时不同年资医生的阅片一致性更高，值得进一步评估其作为第二阅片者的价值。",
        "evaluation": "图像和录像验证衡量模型性能，交叉阅片衡量医生的辅助判读和活检决定。摘要未报告实时部署、实际活检或漏诊结局，尚不能判断临床获益。",
        "refs": {key: ["u"] for key in ("question", "result", "conclusion", "evaluation", "lead")},
    },
    RCT["id"]: COPY[RCT["id"]] | {
        "lead": "在常规康复中加入认知行为物理治疗，可能改善术后早期疼痛及肩关节功能，但长期获益尚未得到证实。",
        "result": "这项单中心随机试验纳入68名拟接受关节镜修复的原发全层冈上肌腱撕裂成人，按1:1分为标准康复组与加用认知行为物理治疗组。干预共6次，术前及出院前各一次，出院后每周一次、共四次。55名完成随访者进入完整病例分析（干预组26名，对照组29名）。3个月时，干预组活动痛评分为2.8±2.0，对照组为4.4±1.5（P=0.002），Constant–Murley评分也较高（P=0.007）。6个月时，两项共同主要结局的组间差异均无统计学意义。",
        "conclusion": "加用认知行为物理治疗与3个月时较低的活动痛和较高的肩关节评分相关。6个月随访中，未见两项共同主要结局具有持续组间获益的证据。",
        "evaluation": "对照组没有获得等量额外接触，因而无法分离认知行为成分与额外接触各自的作用。完整病例分析也需要结合失访解读。",
        "refs": {key: ["u"] for key in ("question", "result", "conclusion", "evaluation", "lead")},
    },
    POLICY["id"]: COPY[POLICY["id"]] | {
        "summary": "国家医保局上线连续用药管理功能。参保人可查询全国定点医疗机构和定点零售药店的用药及购药记录，就医时可授权接诊医生查看既往用药。",
        "why": "记录由各地医保结算系统归集，按T+1更新。跨机构就诊时，患者和医生由此可以先核对已有的结算记录。",
        "impact": "对需要回顾长期用药的患者，可以在复诊前核对购药记录。医生仍需询问药物是否实际服用、是否停药，以及结算记录外的用药。",
    },
    SPACE["id"]: COPY[SPACE["id"]] | {
        "summary": "神舟二十四号飞船与空间站组合体完成交会对接后，乘组于北京时间11月25日14时35分进驻。神舟二十三号乘组在轨迎接，中外航天员首次在中国空间站会面。",
        "why": "这是中国载人飞行任务首次有外籍航天员参与。两组乘组在空间站会面，标志着这次载人飞行的国际合作进入在轨阶段。",
        "impact": "后续可关注联合在轨任务和科学合作的安排。本次通报确认的是对接后的进驻与会面，具体科研成果仍待后续公布。",
    },
    TECH["id"]: event(
        "三部门印发新一代通信网方案，移动通信、光网与卫星互联网将协同推进。",
        "工业和信息化部、国家网信办与国家发展改革委印发新一代通信网建设方案，统筹移动通信演进、万兆光网和卫星互联网建设，并提出适时推进第六代移动通信网络建设。",
        "方案把基础、空间、国际和融合网络放在同一框架中，将移动、固定和卫星接入纳入共同的建设安排。",
        "通信和设备企业可据此关注各类接入网络的后续部署。第六代移动通信网络仍是适时开展的建设方向，文件发布不足以确定商用时间。"),
}


def replay(folder, edition, *, headlines=None, editor=None, history_path=None):
    result = prepare_preview(folder / "result.json", editor or ScriptedEditor(SAMPLE, events=("space", "network")),
                             edition=edition, final_edit=True, history_path=history_path,
                             evaluation=(DAY, [ABSTRACT_ORTHO, DEEP_MED, ABSTRACT_RCT],
                                         list((POLICY, SPACE_NEWS, TECH, LOW) if headlines is None else headlines), {}))
    briefs = {key: (folder / "news-preview" / filename).read_text(encoding="utf-8") for key, filename in FILES.items()}
    return result, briefs


class EditionTests(TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        no_network = patch("requests.sessions.Session.request", side_effect=AssertionError("unexpected network call"))
        no_network.start()
        self.addCleanup(no_network.stop)

    def test_schedule_keys_budget_and_legacy_migration(self):
        ledger = Ledger()
        self.assertEqual(EDITIONS["morning"]["cron"], "30 22 * * *")
        morning = claim(ledger, DAY, "daily", "1", "morning")
        self.assertIsNotNone(claim(ledger, DAY, "daily", "2", "evening"))
        self.assertIsNone(claim(ledger, DAY, "daily", "3", "morning"))
        self.assertIsNone(claim(ledger, DAY, "daily", "3"))
        self.assertEqual(morning["reservation_micro_cny"], 1_000_000)
        self.assertEqual(ledger.data["budgets"][DAY[:7]]["reserved"], 2_000_000)
        ledger.data["budgets"][DAY[:7]]["observed"] = MONTHLY - 100
        with self.assertRaises(RuntimeError):
            claim(ledger, "2026-11-27", "daily", "4", "evening")
        self.assertNotIn("2026-11-27:evening", ledger.data["runs"])
        other = Ledger()
        claim(other, DAY, "daily", "1")
        self.assertEqual(other.data["budgets"][DAY[:7]]["reserved"], DAILY)
        self.assertIsNone(claim(other, DAY, "daily", "2", "evening"))

    def test_schedule_accepts_only_current_beijing_slot_within_window(self):
        environment = {"GITHUB_EVENT_NAME": "schedule", "GITHUB_REF": "refs/heads/main",
                       "RUNNER_TEMP": str(self.root), "PNI_TRIAL_START": "2026-11-28"}
        cases = (
            ("2026-11-28T06:30:00+08:00", "morning", True),
            ("2026-12-01T07:45:00+08:00", "morning", True),
            ("2026-12-01T19:30:00+08:00", "evening", True),
            ("2026-11-28T04:10:00+08:00", "evening", False),
            ("2026-12-01T04:10:00+08:00", "evening", False),
            ("2026-11-28T06:29:59+08:00", "morning", False),
            ("2026-11-28T08:30:00+08:00", "morning", False),
            ("2026-11-28T21:30:00+08:00", "evening", False),
            ("2026-11-27T19:30:00+08:00", "evening", False),
        )
        with patch.dict(os.environ, environment, clear=True), patch("pni.production.execute") as run, \
                patch("pni.production.datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime.fromisoformat(cases[0][0])
            with self.assertRaisesRegex(ValueError, "Unrecognized PNI schedule"):
                main()
            run.assert_not_called()
            for stamp, edition, allowed in cases:
                with self.subTest(stamp=stamp, edition=edition), \
                        patch.dict(os.environ, {"PNI_SCHEDULE_CRON": EDITIONS[edition]["cron"]}):
                    run.reset_mock()
                    clock.now.return_value = datetime.fromisoformat(stamp)
                    main()
                    if allowed:
                        run.assert_called_once_with("daily", self.root / "pni", edition)
                    else:
                        run.assert_not_called()

    def test_manual_morning_catch_up_is_dated_and_keeps_preview_default(self):
        environment = {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/heads/main",
                       "RUNNER_TEMP": str(self.root), "PNI_TRIAL_START": "2026-11-28"}
        with patch.dict(os.environ, environment, clear=True), patch("pni.production.execute") as run, \
                patch("pni.production.archived_preview") as preview, \
                patch("pni.production.datetime", wraps=datetime) as clock:
            main()
            preview.assert_called_once_with(self.root / "pni")
            run.assert_not_called()
            clock.now.return_value = datetime.fromisoformat("2026-11-28T07:50:00+08:00")
            with patch.dict(os.environ, {"PNI_MORNING_CATCH_UP_DATE": "2026-11-27"}):
                with self.assertRaisesRegex(RuntimeError, "requires today's Beijing date"):
                    main()
            run.assert_not_called()
            with patch.dict(os.environ, {"PNI_MORNING_CATCH_UP_DATE": "2026-11-28"}):
                main()
                run.assert_called_once_with("daily", self.root / "pni", "morning")
                clock.now.return_value = datetime.fromisoformat("2026-11-28T19:30:00+08:00")
                with self.assertRaisesRegex(RuntimeError, "between 06:30 and 19:30"):
                    main()
                self.assertEqual(run.call_count, 1)

    def test_morning_deep_copy_and_final_html(self):
        result, briefs = replay(self.root, "morning")
        self.assertEqual(result["counts"], {"medical": 3, "policy": 0, "world": 0})
        self.assertEqual(result["gate"][MED["id"]]["result"], "accepted")
        messages, removed = edition_messages(briefs, DAY, "morning", result)
        self.assertFalse(removed)
        self.assertEqual(len(messages), 1)
        markup = messages[0].html
        self.assertIn("5,404张", markup)
        self.assertIn("准确率为0.888，AUC为0.974", markup)
        self.assertIn("纳入12名医生和200张图像", markup)
        self.assertIn("实际活检或漏诊", markup)
        self.assertIn("干预共6次", markup)
        self.assertIn("等量额外接触", markup)
        for field in ("研究目的", "研究方法与结果", "结论"):
            self.assertEqual(markup.count(f">{field}</b>"), 3)
        self.assertNotIn("结论与评价", markup)
        self.assertNotIn("作者结论：", markup)
        self.assertNotIn("编辑评价：", markup)
        for lead in (SAMPLE[ORTHO["id"]]["lead"], SAMPLE[MED["id"]]["lead"], SAMPLE[RCT["id"]]["lead"]):
            self.assertIn(lead, markup)
        body = markup[markup.index(SAMPLE[MED["id"]]["title"]):]
        self.assertLess(body.index("外部队列的准确率"), body.index("录制录像"))
        self.assertLess(body.index("录制录像"), body.index("前瞻性随机交叉阅片"))
        self.assertNotIn(">分阶段结果及解读</b>", markup)
        self.assertNotIn("未能从摘要核实", markup)
        self.assertNotIn("国内医疗民生", markup)

    def test_morning_fewshot_prompt_and_full_abstract_keep_fine_tuning_result(self):
        editor = Mock(side_effect=ScriptedEditor(SAMPLE))
        result, briefs = replay(self.root, "morning", editor=editor)
        calls = {call.args[0]: call for call in editor.call_args_list}
        generate_prompt = calls["v14-generate"].args[1]
        verify_prompt = calls["v14-verify"].args[1]
        self.assertIn("表达范例", generate_prompt)
        self.assertIn("禁止复用", generate_prompt)
        self.assertIn("0.95", generate_prompt)
        self.assertIn("核心研究问题＋主要发现", generate_prompt)
        for lead in (SAMPLE[ORTHO["id"]]["lead"], SAMPLE[MED["id"]]["lead"], SAMPLE[RCT["id"]]["lead"]):
            self.assertIn(lead, generate_prompt)
        self.assertNotIn("表达范例", verify_prompt)
        self.assertIn("唯一可用证据是 evidence.u 的完整摘要", verify_prompt)
        packet = calls["v14-generate"].args[2]
        ortho = next(row for row in packet if row["id"] == ORTHO["id"])
        self.assertEqual(ortho["evidence"], {"u": ABSTRACT_ORTHO["original_abstract"]})
        self.assertIn("0.95 [0.93-0.96]", ortho["evidence"]["u"])
        messages, removed = edition_messages(briefs, DAY, "morning", result)
        self.assertFalse(removed)
        markup = messages[0].html
        self.assertIn("0.95", markup)
        self.assertIn("0.93–0.96", markup)
        self.assertIn("微调", markup)
        self.assertEqual(result["gate"][ORTHO["id"]]["result"], "accepted")

    def test_wrong_stage_number_is_rejected_even_if_mock_verifier_accepts(self):
        copy = SAMPLE[MED["id"]]
        changed = SAMPLE | {MED["id"]: copy | {"result": copy["result"].replace("0.974", "0.999")}}
        result, briefs = replay(self.root, "morning", editor=ScriptedEditor(changed))
        markup = edition_messages(briefs, DAY, "morning", result)[0][0].html
        self.assertEqual(result["gate"][MED["id"]]["result"], "source_or_numeric_mismatch")
        self.assertNotIn("0.999", markup)
        self.assertEqual(result["counts"]["medical"], 2)

    def test_plausible_numbers_from_wrong_validation_stage_need_semantic_approval(self):
        copy = SAMPLE[MED["id"]]
        swapped = copy["result"].replace("0.976", "@").replace("0.974", "0.976").replace("@", "0.974")
        editor = ScriptedEditor(SAMPLE | {MED["id"]: copy | {"result": swapped}}, reject={MED["id"]})
        result, _ = replay(self.root, "morning", editor=editor)
        self.assertEqual(result["gate"][MED["id"]]["result"], "semantic_verification_failed")

    def test_evening_sections_and_no_academic_work(self):
        result, briefs = replay(self.root, "evening")
        self.assertEqual(result["counts"], {"medical": 0, "policy": 1, "world": 2})
        self.assertEqual(result["selection"]["missing_news_sections"], [])
        self.assertTrue(all(not round_["papers"] for round_ in result["candidate_audit"]["rank_batches"]))
        messages, _ = edition_messages(briefs, DAY, "evening", result)
        self.assertEqual([a.section for a in messages[0].articles], ["国内医疗民生", "国内外重大事件", "科技与产业"])
        self.assertIn("T+1更新", messages[0].html)
        self.assertIn("不足以确定商用时间", messages[0].html)

    def test_fresh_collection_calls_only_sources_for_the_edition(self):
        for edition in EDITIONS:
            with (
                self.subTest(edition=edition),
                patch("experiments.v14.pipeline.datetime", wraps=datetime) as clock,
                patch("experiments.v14.pipeline.collect", return_value=({DAY: [ABSTRACT_ORTHO, DEEP_MED, ABSTRACT_RCT]}, {})) as academic,
                patch("experiments.v14.pipeline.collect_live_cards", return_value=([POLICY, TECH, SPACE], DAY)) as current,
                patch("pni.curation.enrich_open_results", return_value=[]) as fulltext,
            ):
                clock.now.return_value = datetime.fromisoformat(DAY + "T06:30:00+08:00")
                prepare_preview(self.root / "result.json", ScriptedEditor(SAMPLE, events=("space", "network")),
                                edition=edition, final_edit=True, source_state=self.root / "source-health.json")
                self.assertEqual(academic.called, edition == "morning")
                self.assertEqual(current.called, edition == "evening")
                fulltext.assert_not_called()

    def test_complete_pubmed_abstract_survives_legacy_rank_excerpt_and_is_used_twice(self):
        long_text = "Complete abstract text. " * 360
        xml = (f"<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>43046223</PMID><Article>"
               f"<ArticleTitle>{MED['title']}</ArticleTitle><Journal><Title>Journal</Title></Journal>"
               f"<ArticleDate DateType='Electronic'><Year>2026</Year><Month>11</Month><Day>17</Day></ArticleDate>"
               f"<Abstract><AbstractText Label='METHODS'>{long_text}</AbstractText>"
               f"<AbstractText Label='CONCLUSION'>P &lt; 0.05 in participants with ages &gt; 60. Endpoint at abstract tail.</AbstractText></Abstract>"
               f"</Article></MedlineCitation></PubmedArticle></PubmedArticleSet>").encode()
        with patch("news_push.requests.get", return_value=Mock(content=xml)), patch("news_push.time.sleep"):
            record, = _pubmed_records([MED["id"]])
        card = medical_card({"id": MED["id"], "title": MED["title"], "pubmed_record": record},
                            allow_possible=True)
        self.assertLess(len(card["evidence"]), len(card["original_abstract"]))
        self.assertNotIn("abstract tail", card["evidence"])
        self.assertEqual(copy_packet(card, morning=True)["evidence"]["u"], card["original_abstract"])
        self.assertTrue(card["original_abstract"].endswith("Endpoint at abstract tail."))
        self.assertIn("P < 0.05 in participants with ages > 60", card["original_abstract"])
        editor = Mock(side_effect=ScriptedEditor(SAMPLE))
        replay(self.root, "morning", editor=editor)
        generate = next(call.args[2] for call in editor.call_args_list if call.args[0] == "v14-generate")
        verify = next(call.args[2] for call in editor.call_args_list if call.args[0] == "v14-verify")
        expected = {"u": DEEP_MED["original_abstract"]}
        self.assertEqual(next(row for row in generate if row["id"] == MED["id"])["evidence"], expected)
        self.assertEqual(next(row["record"] for row in verify if row["record"]["id"] == MED["id"])["evidence"], expected)

    def test_morning_does_not_edit_from_rank_excerpt_alone(self):
        editor = Mock(side_effect=AssertionError("missing registered abstract must not be sent to editor"))
        self.assertEqual(write_verified(editor, [MED], edition="morning"), ({}, {}, []))

    def test_morning_and_evening_capacity_and_rebuilt_lead(self):
        def article(name, section, size):
            return f"### {name}\n**栏目**　{section}\n{'中' * size}\n[来源](https://example.org/{name})\n"
        clinical = article("clinical", "骨科临床", 210)
        ortho = article("ortho", "骨科 × AI", 180)
        weak = article("weak", "骨科 × AI", 800)
        other = article("other", "医疗 AI", 790)
        notices = "## 国自然动态\n[申报通知](https://example.org/funding)\n"
        priorities = {"https://example.org/ortho": 99, "https://example.org/weak": 10}
        day = date.fromisoformat(DAY)
        ideal, _ = plan_edition(ortho + clinical, notices, day, "morning")
        limit = measure(ideal[0].html)["utf8_bytes"]
        messages, cuts = plan_edition(ortho + weak + other + clinical, notices, day, "morning",
                                      priorities=priorities, limit=limit)
        self.assertEqual([r["title"] for r in cuts], ["other", "weak"])
        self.assertEqual([r.number for r in messages[0].articles], [1, 2, 3])
        self.assertNotIn("weak", messages[0].html)
        self.assertIn("中" * 210, messages[0].html)
        self.assertIn("https://example.org/funding", messages[0].html)
        strong_section = "## 国内外重大事件\n" + article("strong", "国内外重大事件", 200)
        weaker = "\n## 科技与产业\n" + article("minor", "科技与产业", 790)
        limit = measure(plan_edition("", strong_section, day, "evening")[0][0].html)["utf8_bytes"]
        night, removed = plan_edition("", strong_section + weaker, day, "evening", limit=limit,
                                     priorities={"https://example.org/strong": 10})
        self.assertEqual([r["title"] for r in removed], ["minor"])
        self.assertEqual(len(night[0].articles), 1)
        self.assertEqual(plan_edition("", strong_section, day, "evening", limit=20)[0], [])

    def test_only_confirmed_included_primary_articles_enter_history(self):
        full, briefs = replay(self.root, "evening")
        tail = "## 国内外重大事件" + briefs["extended"].split("## 国内外重大事件", 1)[1]
        related = f"\n[补充背景]({POLICY['url']})\n"
        ideal, _ = edition_messages(briefs | {"extended": tail + related}, DAY, "evening", full)
        briefs["extended"] += related
        limited, removed = edition_messages(briefs, DAY, "evening", full,
                                            limit=measure(ideal[0].html)["utf8_bytes"])
        self.assertEqual(len(removed), 1)
        removed_url = removed[0]["url"]
        for status in ("accepted", "sent", "rejected", "submission_unknown"):
            with self.subTest(status=status):
                ledger = Ledger()
                batch = claim(ledger, DAY, "daily", "1", "evening")
                sender = Mock()
                sender.send.return_value = {"status": status}, None
                deliver_html(ledger, batch, DAY, limited, full["selected"], [], sender)
                seen = {key for row in exclusion_history(ledger.data) for key in row["keys"]}
                self.assertNotIn("url:" + removed_url.casefold(), seen)
                self.assertEqual(bool(ledger.data.get("history")), status == "sent")
                self.assertEqual(bool(seen), status in {"sent", "accepted", "submission_unknown"})
                deliver_html(ledger, batch, DAY, limited, full["selected"], [], sender)
                self.assertEqual(sender.send.call_count, 1)

    def test_frozen_candidate_history_is_read_only_until_delivery(self):
        history = self.root / "before.json"
        before = json.dumps([{"day": DAY, "keys": ["url:" + TECH["url"]]}])
        history.write_text(before, encoding="utf-8")
        result, _ = replay(self.root, "evening", history_path=history)
        self.assertEqual(result["candidate_audit"]["news"]["network"]["outcome"], "history_duplicate")
        self.assertEqual(history.read_text(encoding="utf-8"), before)

    def test_miit_list_uses_detail_timestamp_and_does_not_accept_future_item(self):
        path = "/zwgk/zcwj/wjfb/tz/art/2026/art_abcdef123.html"
        listing = f'<a href="{path}">工业互联网相关试点办法正式发布</a>'
        now = datetime.now(CST).strftime("%Y-%m-%d %H:%M")
        body = "<p>" + "试点方案明确应用场景和项目实施要求，具体推进以当地正式发布的安排为准。" * 5 + "</p>"
        for stamp, count in ((now, 1), ("2099-11-26 20:00", 0)):
            replies = [Mock(text=listing, status_code=200),
                       Mock(text=f"<div>发布时间：{stamp}</div>{body}", status_code=200)]
            with patch("news_push.requests.get", side_effect=replies):
                rows = fetch_official_page_candidates([TECH_SOURCE])
            self.assertEqual(len(rows), count)
            if rows:
                self.assertEqual(rows[0]["evidence_status"], "detail_fetched")
                self.assertTrue(rows[0]["published_at"].endswith("+08:00"))

    def test_reserved_model_budgets_run_through_the_actual_provider_guard(self):
        scripted = ScriptedEditor(SAMPLE, events=("space", "network"))

        def fake_api(url, headers, payload):
            packet = json.loads(payload["messages"][1]["content"])
            phase = ("v14-score" if isinstance(packet, dict) else
                     "v14-verify" if "record" in packet[0] else "v14-generate")
            answer = scripted(phase, "", packet)
            return {"usage": {"prompt_tokens": 1, "completion_tokens": 1},
                    "choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"items": answer})}}]}

        for edition, settings in EDITIONS.items():
            budget = settings["reservation"]
            provider = Flash("local-tests-only", budget - settings["tools"] * TOOL_PRICE,
                             max_reservation=budget, max_requests=6)
            with patch("experiments.run.api_json", side_effect=fake_api):
                result, briefs = replay(self.root / edition, edition, editor=provider)
            self.assertEqual(len(edition_messages(briefs, DAY, edition, result)[0][0].articles), 3)
            self.assertEqual(len(provider.calls), 3)
            self.assertGreater(provider.remaining, 0)

    def test_preview_budget_is_separate_from_production_ceiling(self):
        from pni.production import DAILY, PREVIEW_RESERVATION
        self.assertEqual(DAILY, 300_000)
        self.assertEqual(PREVIEW_RESERVATION, {"morning": 1_000_000, "evening": 1_000_000})
        self.assertLessEqual(PREVIEW_RESERVATION["morning"] + PREVIEW_RESERVATION["evening"], 2_000_000)

    def test_flash_tracks_observed_spent_separately_from_reservation(self):
        provider = Flash("local-tests-only", 100_000, max_reservation=200_000)
        self.assertEqual(provider.observed_spent, 0)
        response = {"usage": {"prompt_tokens": 100, "completion_tokens": 20},
                    "choices": [{"finish_reason": "stop",
                                 "message": {"content": json.dumps({"items": []})}}]}
        with patch("experiments.run.api_json", return_value=response):
            provider("v14-score", "prompt", [{"id": "x"}])
        self.assertGreater(provider.observed_spent, 0)
        self.assertLess(provider.observed_spent, 100_000 - provider.remaining)
        self.assertLess(provider.remaining, 100_000)

    def test_fill_brief_uses_observed_spent_not_reservation_ledger(self):
        from experiments.v14.pipeline import fill_brief
        provider = Flash("local-tests-only", 100_000, max_reservation=200_000)
        provider.observed_spent = 1_000  # Real spend is tiny; reservation ledger is nearly drained.
        provider.remaining = 40_000  # Old conservative ledger would block the reserve pass.
        audit = {"rank_batches": [], "missing_after_first_draft": [], "reserve": None}
        extra, extra_news, extra_results, extra_drafts, extra_errors = fill_brief(
            provider, DAY, [], [], [], {}, [], {}, audit, [], set(), set(), edition="morning")
        self.assertNotEqual(audit["reserve"], "insufficient_budget")

    def test_run_audit_contains_full_abstract_drafts_scores_and_rejections(self):
        result, _ = replay(self.root, "morning")
        audit = result["run_audit"]
        self.assertIn("scores", audit)
        self.assertIn("rank_batches", audit)
        self.assertIn("drafts", audit)
        self.assertIn(ORTHO["id"], audit["medical"])
        self.assertIn("original_abstract", audit["medical"][ORTHO["id"]])
        self.assertIn("0.95 [0.93-0.96]", audit["medical"][ORTHO["id"]]["original_abstract"])
        self.assertIn(ORTHO["id"], audit["drafts"])
        self.assertIn(MED["id"], audit["drafts"])

    def test_round2_policy_draft_passes_with_real_evidence(self):
        # Frozen from the second real run: policy-O1 draft fields were rejected even
        # though every number came from the official evidence.
        from experiments.v14.pipeline import abstract_packet, usable_field
        fixture = json.loads(Path(__file__).with_name("fixtures").joinpath(
            "cloud_regressions.json").read_text(encoding="utf-8"))["round2"]
        draft = fixture["policy_o1_draft"]
        card = {"id": "policy-O1", "lane": "policy", "title": fixture["policy_o1_title"],
                "date": fixture["policy_o1_date"], "evidence": fixture["policy_o1_evidence"],
                "source_scope": "官方原文节选"}
        packet = abstract_packet(card)
        for field in ("title", "methods", "result", "summary", "lead"):
            refs = draft["refs"].get(field) or (["s"] if field == "lead" else None)
            self.assertTrue(usable_field(field, draft[field], refs, packet, lane="policy",
                                         title=card["title"], date=card["date"]), field)

    def test_round2_adjacent_version_number_is_recognised(self):
        from experiments.stage2.quality import number_set
        self.assertIn("2", number_set("DRG/DIP2.0、3.0版"))
        self.assertIn("99%", number_set("接入率达99%"))

    def test_unknown_event_topic_falls_back_to_model_subject(self):
        from experiments.v14.pipeline import ranked_candidates
        world = [{"id": "space", "title": "神舟二十四号乘组进驻空间站", "date": DAY,
                  "source": "中国载人航天工程办公室", "evidence": "乘组进驻。",
                  "official": True, "topic": "major", "lane": "world"}]
        editor = Mock(return_value=[{"id": "space", "scores": [4] * 6,
                                     "event": "中国空间站载人任务", "news_value": 5}])
        _, scores, chosen = ranked_candidates(editor, [], DAY, world, v17=True,
                                              audit={"rank_batches": []}, retry=True, edition="evening")
        self.assertEqual(chosen, ["space"])
        self.assertEqual(editor.call_args.args[2]["world"][0]["id"], "space")


def write_previews(folder):
    for edition in EDITIONS:
        result, briefs = replay(folder / edition, edition)
        messages, cuts = edition_messages(briefs, DAY, edition, result)
        if len(messages) != 1:
            raise RuntimeError(f"No complete {edition} sample")
        message = messages[0]
        (folder / f"{edition}.html").write_text(document(message.html), encoding="utf-8")
        report = {"planned_articles": len(message.articles), "payload": measure(message.html),
                  "removed": cuts, "selection": result["selection"], "gate": result["gate"],
                  "candidate_audit": result["candidate_audit"]}
        (folder / f"{edition}-audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    write_previews(Path(sys.argv[1]))
