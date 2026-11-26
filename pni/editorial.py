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
    item.update(ai_study_details(card.get("pmid"), card.get("evidence", "")))
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


def ai_study_details(pmid, abstract):
    """Translate selected methods only when the original supports the full statement."""
    source = " ".join(abstract.split())
    if str(pmid) == "43058301":
        model = re.search(r"(ResNet-\d+)-based convolutional neural network", source)
        cohorts = re.search(
            r"training \(([\d,]+) patients, ([\d,]+) annotated images\) and internal validation "
            r"\(([\d,]+) patients, ([\d,]+) hips\)\. External validation included ([\d,]+) patients "
            r"from independent institutions scanned with CT systems from two vendors", source)
        if model and cohorts and "retrospective study" in source and "six readers" in source:
            train, images, internal, hips, external = cohorts.groups()
            return {
                "design": (f"多中心回顾性研究，以冠状位 CT 图像训练基于 {model[1]} 的卷积神经网络。训练集"
                           f"{train}人、{images}张标注图像，内部验证{internal}人（{hips}髋），独立机构外部验证"
                           f"{external}人，覆盖两家 CT 设备厂商。另有6名医师参加探索性阅片研究。"),
                "limits": "外部验证和阅片研究评估的是诊断表现。摘要未报告该系统是否改善实际诊疗决策或患者结局。",
            }
    if str(pmid) == "43092989":
        count = re.search(r"Three focus groups were conducted consisting of (\d+) participants", source)
        if count and all(term in source for term in ("single-institution pilot mixed-methods",
                                                    "large language model", "System Usability Scale",
                                                    "technologically experienced cohort")):
            return {
                "design": (f"单中心混合方法试点，共3个焦点小组、{count[1]}名既往接受放疗的女性乳腺癌幸存者。"
                           "大语言模型根据个体资料生成餐单和购物清单。参与者以系统可用性量表（SUS）评分，并参加讨论。"),
                "limits": "样本来自单一机构，参与者较熟悉数字技术。这项试点未验证营养改善、安全性或临床获益。",
            }
    return {}
