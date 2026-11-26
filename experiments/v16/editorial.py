"""Small local presentation changes shared by the two comparison arms."""
import re

ROUTINE = re.compile(r"研讨会|交流会|学术论坛|学术交流|symposium|workshop|academic forum", re.I)
OUTCOME = re.compile(r"生效|签署(?:法案|协议|条约)|正式发布|获批|启动(?:全国|跨国)|treaty signed|law passed|in force", re.I)
REVIEWS = re.compile(r"systematic review|meta.analysis|scoping review|narrative review", re.I)
STUDY = re.compile(r"randomi[sz]ed|external validation|prospective|multicent(?:er|re)|cohort", re.I)
TERMS = {
    "RCT": "随机对照试验", "CAD": "计算机辅助诊断", "AUC": "曲线下面积",
    "CI": "置信区间", "ICC": "组内相关系数", "NNT": "需治疗人数",
    "ACLR": "前交叉韧带重建", "ACL": "前交叉韧带", "TKA": "全膝关节置换术",
    "MCID": "最小临床重要差异", "PROBAST": "预测模型偏倚风险评估工具",
    "DRG": "按疾病诊断相关分组", "DIP": "按病种分值付费",
    "VAS": "视觉模拟评分", "CMS": "Constant–Murley肩关节评分",
    "CBPT": "认知行为物理治疗", "ARCR": "关节镜下肩袖修复",
}
JOURNALS = {
    "Skeletal radiology": "《骨骼放射学》（Skeletal Radiology）",
    "Journal of shoulder and elbow surgery": "《肩肘外科杂志》（Journal of Shoulder and Elbow Surgery）",
    "Orthopaedics & traumatology, surgery & research : OTSR": "《骨科与创伤：外科与研究》（OTSR）",
    "Journal of medical Internet research": "《医学互联网研究杂志》（JMIR）",
}
TOKEN = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(TERMS) + r"|RR)(?![A-Za-z0-9])")


def routine_event(card):
    return card["lane"] == "world" and ROUTINE.search(card["title"]) and not OUTCOME.search(card["title"])


def prefer_variety(ranked, scores, limit=4):
    """Move a comparable empirical paper into an all-review shortlist."""
    chosen = ranked[:limit]
    if len(chosen) != limit or sum(bool(REVIEWS.search(row["title"])) for row in chosen) < 3:
        return chosen
    previous = scores.get(chosen[-1]["id"], {}).get("weighted")
    for row in ranked[limit:limit + 5]:
        grade = scores.get(row["id"], {}).get("weighted")
        if previous is not None and grade is not None and grade >= previous - 8 and (
            STUDY.search(row["title"]) and not REVIEWS.search(row["title"])
        ):
            return [*chosen[:-1], row]
    return chosen


def expand_abbreviations(item):
    seen = set()
    for field in ("localized_title", "question", "design", "result", "limits", "clinical", "insight", "supplement", "summary"):
        content = item.get(field)
        if not content:
            continue
        # Preserve evidence excerpts as the publisher wrote them.
        prefix, marker, original = content.partition("原文短引")
        if marker:
            stop = prefix.rfind("结果段")
            if stop >= 0:
                prefix, original = prefix[:stop], prefix[stop:] + marker + original
            else:
                original = marker + original
        def replace(match):
            label = match[0]
            before, after = prefix[:match.start()], prefix[match.end():]
            long = TERMS.get(label)
            if label == "RR":
                long = "风险比" if re.match(r"\s*[=:：]?\s*\d", after) else None
            if not long or label in seen:
                return label
            seen.add(label)
            if long in before[-40:] or long in after[:35]:
                return label
            return f"{long}（{label}）"
        item[field] = TOKEN.sub(replace, prefix) + original


TENS = dict(zip("twenty thirty forty fifty sixty seventy eighty ninety".split(), range(20, 100, 10)))
ONES = dict(zip("one two three four five six seven eight nine".split(), range(1, 10)))


def source_number(text):
    if text.isdigit():
        return int(text)
    tens, _, ones = text.lower().partition("-")
    if tens in TENS and (not ones or ones in ONES):
        return TENS[tens] + ONES.get(ones, 0)
    return None


def normalize_schedule(text, source):
    if not isinstance(text, str):
        return text
    if re.search(r"\bfour weekly postdischarge sessions\b", source, re.I):
        return re.sub(r"(?<=出院后)每周\s*4\s*次", "每周1次，共4次", text)
    return text


def consistency(item, paper):
    if item["lane"] != "medical":
        return
    from experiments.stage3.evidence import sections
    abstract = paper.get("evidence", "")
    part = sections(abstract)
    result = part.get("results") or part.get("result") or ""
    original = paper["title"]
    design = normalize_schedule(item.get("design", ""), abstract)
    if design:
        item["design"] = design
    randomized = re.search(r"\b([a-z]+(?:-[a-z]+)?|\d+) participants were randomized, and (\d+) completed follow-up", result, re.I)
    if randomized:
        total = source_number(randomized[1])
        complete = int(randomized[2])
        if total and 0 < complete <= total and not re.search(r"\b" + str(total) + r"\s*[人例名]", design):
            addition = f"随机分组共 {total} 人，{complete} 人完成随访。"
            item["design"] = (design + ("。" if design and not design.endswith("。") else "") + addition).strip()
            return
    if not design or "总样本量未" in design or "暂不报告" in design:
        included = re.search(r"(?:included|comprising|involving|included a total of)\s+([\d,]+)\s+(patients|participants|studies)", result, re.I)
        reviewed = re.search(r"([\d,]+)\s+(studies|trials)\s+(?:met (?:the )?(?:inclusion|eligibility)|were included)", result, re.I)
        numbers = [(m[1], {"patients":"名患者","participants":"名受试者","studies":"项研究","trials":"项试验"}[m[2].lower()])
                   for m in (reviewed, included) if m]
        usable = "系统评价" if re.search("systematic review", original, re.I) else ""
        if numbers:
            item["design"] = "，".join(filter(None, (usable, "摘要报告纳入" + "、".join(n + unit for n, unit in numbers)))) + "。"
        elif usable and not design:
            item["design"] = usable
