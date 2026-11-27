import unittest

from experiments.v14.pipeline import formatted_copy


class RoundFiveFactTests(unittest.TestCase):
    def test_rejects_upgraded_claims_accepted_in_real_run(self):
        for evidence, incorrect, correct in (
            ("去年以来，各央企共开展整合项目1100余个，涉及资产规模约6.8万亿元。",
             "1100余个整合项目落地、约6.8万亿元资产完成整合。",
             "央企开展整合项目1100余个，涉及资产规模约6.8万亿元。"),
            ("专项检查查处5类突出违规问题，涉及金额4426.27万元。",
             "专项检查追回违规金额。", "专项检查查处5类违规问题。"),
            ("专项检查涉及金额4426.27万元，已追回100万元。",
             "已追回4426.27万元。", "专项检查已追回100万元。"),
            ("项目开展整合，拟在年底完成整合。",
             "该项目已完成整合。", "该项目拟在年底完成整合。"),
        ):
            with self.subTest(incorrect=incorrect):
                card = {"lane": "policy", "title": evidence}
                for claim, allowed in ((incorrect, False), (correct, True)):
                    proposal = {"impact": claim, "refs": {"impact": ["s"]}}
                    kept, _ = formatted_copy(card, proposal, {"impact"}, {"s": evidence}, evening=True)
                    self.assertEqual("impact" in kept, allowed)
