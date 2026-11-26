"""Evidence-preserving local corrections shared by preview and live formatting."""
import re


def clean_repeated_terms(text):
    return re.sub(r"Constant[-–]Murley评分（Constant[-–]Murley肩关节评分（CMS））",
                  "Constant–Murley肩关节评分（CMS）", text)


def shoulder_trial_result(pmid, abstract):
    """Fallback for the reviewed trial, activated only by exact original comparisons."""
    if str(pmid) != "43085541":
        return None
    source = " ".join(abstract.split())
    match = re.search(
        r"At 3 months, the CBPT group had lower activity-related VAS scores than the control group "
        r"\(([\d.]+)\s*±\s*([\d.]+) vs\. ([\d.]+)\s*±\s*([\d.]+); p\s*=\s*([\d.]+)\), "
        r"with between-group differences also observed for CMS \(p\s*=\s*([\d.]+)\)", source)
    if not match or "At 6 months, neither co-primary outcome differed significantly between groups" not in source:
        return None
    treatment, treatment_sd, control, control_sd, pain_p, score_p = match.groups()
    return (f"3个月时，加用认知行为物理治疗组的活动相关疼痛评分低于标准康复组"
            f"（{treatment}±{treatment_sd} 对 {control}±{control_sd}，P={pain_p}），"
            f"Constant–Murley肩关节评分也有组间差异（P={score_p}）。"
            "6个月时，两项共同主要结局均无显著组间差异。"
            "所核实的摘要未列出3个月肩关节评分的具体组均值。")


def polish_verified(item, card):
    result = item.get("result", "")
    if item.get("lane") == "medical" and ("原文短引" in result or not result):
        verified = shoulder_trial_result(card.get("pmid"), card.get("evidence", ""))
        if verified:
            item["result"] = verified
            if not item.get("clinical"):
                item["clinical"] = ("结果提示短期活动痛可能改善；6个月的组间差异未达到统计学显著。"
                                    "对照组未获得等量额外接触，尚不能分离认知行为训练本身的效应。")
    for field in ("design", "result", "clinical", "insight"):
        if item.get(field):
            item[field] = clean_repeated_terms(item[field])
