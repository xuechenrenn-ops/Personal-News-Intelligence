"""Render a narrow, traceable claim from the source rather than re-generating it."""
import re
from datetime import date

from experiments.preview import clean_text
from experiments.stage2.quality import NO_RESULTS_DESIGN, number_set
from research_scope import research_lane

HEADING = re.compile(
    r"(?<!\w)(objective(?:s)?|purpose|aims?|background|methods?|materials and methods|"
    r"study design|design|participants|level of evidence|results?|findings|limitations?|conclusions?|interpretation)\s*:\s*",
    re.I,
)
SENTENCES = re.compile(r"(?<=[!?。])\s*|(?<!\bvs)(?<!\be\.g)(?<!\bi\.e)(?<=\.)\s+(?=[A-Z])")
DESIGNS = [
    (r"target trial emulation|emulated a target trial", "目标试验模拟（观察性）"),
    (r"network meta.analysis", "网状荟萃分析"),
    (r"meta.analysis", "荟萃分析"),
    (r"systematic review", "系统评价"),
    (r"randomi[sz]ed (?:controlled |clinical )?trial|randomly assign|randomly allocate|RCT\b", "随机试验"),
    (r"retrospective", "回顾性研究"),
    (r"prospective", "前瞻性研究"),
    (r"cross.sectional", "横断面研究"),
]
NOUNS = r"patients?|participants?|adults?|subjects?|studies|trials|knees|shoulders|hips"
VALIDATION_SAMPLE = re.compile(
    r"\bexternal validation included ([\d,]+) patients\b|"
    r"\b(?:independent|external) (?:[\w-]+ ){0,3}cohort \(n\s*=\s*([\d,]+)\)",
    re.I,
)
BASELINE_ONLY = re.compile(r"^\s*(?:(?:the )?(?:average|mean|median) age|baseline characteristics|"
                           r"(?:at baseline[, ]+)?[\d,.%]+ (?:patients|participants) were (?:male|female)|"
                           r"(?:absolute|relative) risk reduction for )", re.I)
PROGNOSTIC = re.compile(r"predict(?:ion|ive)|classification models?|external validation|"
                        r"risk (?:calculator|model)|deep learning|machine learning", re.I)
TEST_ONLY = re.compile(r"exam(?:ination)?s?\b|question.bank", re.I)
SAMPLES = re.compile(
    rf"\b(?:a total of |we (?:included|enrolled|identified) |trial included |included |enrolled |review of )"
    rf"([\d,]+)\s+({NOUNS})\b|"
    rf"\b([\d,]+)\s+({NOUNS})\s+(?:were (?:enrolled|randomi[sz]ed|included|randomly assigned))\b",
    re.I,
)
UNIT_LABEL = {"patients": "例患者", "patient": "例患者", "participants": "名受试者",
              "participant": "名受试者", "adults": "名成人", "subjects": "名受试者",
              "studies": "项研究", "trials": "项试验", "knees": "膝", "shoulders": "肩", "hips": "髋"}
METRIC = re.compile(r"\b(?:RR|HR|OR|AUC|AUROC|ICC|NNT|95% CI|sensitivity|specificity|accuracy|"
                    r"mean difference|risk ratio|hazard ratio|odds ratio)\b", re.I)
OUTCOME = re.compile(
    r"improv|decreas|reduc|increas|differ|similar|associat|detect|discriminat|"
    r"fracture|retear|mortality|pain|injur|mobilit|function|accuracy|sensitivity|"
    r"\bKOOS\b|\bAUC\b|precision|performance|complication|failure|agreement|"
    r"achiev|correlation|estimated|demonstrat|found|outperform|risk of bias|outcome|calibration",
    re.I,
)
BAD_EXCERPT = re.compile(r"https?://|\[.*?\]\(|(?:ignore|disregard).{0,20}instructions|"
                         r"shall prescribe|recommend(?:ed)? that patients", re.I)
INSIGHTS = {
    "clinical": "若用于科研选题，可事先定义最小临床重要差异，并复核随访期和可纳入人群。",
    "ortho-ai": "若做本院复现，可保留独立外部验证集，分别报告区分度、校准和亚组误差。",
    "medical-ai": "若评估临床落地，可前瞻记录漏报、人工复核工作量和科室间性能变化。",
}


def sections(abstract):
    matches = list(HEADING.finditer(abstract))
    parts = {}
    for idx, match in enumerate(matches):
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(abstract)
        name = match.group(1).lower()
        parts[name] = (parts.get(name, "") + " " + abstract[match.end():end]).strip()
    return parts


def fragments(text):
    for sentence in SENTENCES.split(text):
        sentence = clean_text(sentence).strip()
        if not sentence:
            continue
        yield sentence
        # Clip a complete leading claim at a switch to a different outcome.
        if "," in sentence and re.search(r", (?:interrater )?reliability (?:was|ranged) ", sentence, re.I):
            lead = re.split(r", (?:interrater )?reliability (?:was|ranged) ", sentence, maxsplit=1, flags=re.I)[0]
            if len(lead.split()) >= 7 and re.search(r"\b(?:was|ranged from)\b", lead, re.I):
                yield lead
        stack = []
        cut = 0
        for index, char in enumerate(sentence):
            if char in "([":
                stack.append(char)
            elif char in ")]" and stack:
                stack.pop()
            if char == ";" and not stack:
                yield sentence[cut:index].strip()
                cut = index + 1
        if cut:
            yield sentence[cut:].strip()
        leading = re.match(r"^(.+?\)) and (?:partial|full)[- ]thickness retear", sentence, re.I)
        if leading:
            yield leading[1]


def choose_result(text, primary=None):
    confirmed = []
    for value in fragments(text):
        word_count = len(value.split())
        if not 5 <= word_count <= 23 or len(value) > 240 or BAD_EXCERPT.search(value):
            continue
        if (not OUTCOME.search(value) or BASELINE_ONLY.search(value)
                or any(value.count(left) != value.count(right) for left, right in ("()", "[]"))
                or re.match(r"^(?:and\b|but\b|or\b|where\b|number needed|at a post.hoc)", value, re.I)):
            continue
        counts = number_set(value)
        score = (bool(primary and primary in value.casefold()),
                 bool(METRIC.search(value)), bool(counts),
                 not value.lower().startswith(("it ", "this ", "these ")))
        confirmed.append((score, value))
    # These compact measurements carry their complete endpoint and validation context.
    for match in re.finditer(
        r"\bexternal (?:AUC(?:-ROC)?|AUROC)\s+0\.\d{2,4},?\s*95% CI\s+0\.\d{2,4}[-–]0\.\d{2,4}",
        text, re.I,
    ):
        confirmed.append(((False, True, True, True), match[0]))
    return max(confirmed, key=lambda pair: pair[0])[1] if confirmed else ""


def paper_card(card):
    """Use outcomes from labeled Results with a narrow unstructured fallback."""
    identifier = card.get("id")
    def reject(reason):
        return None, {"id": identifier, "status": reason}
    if not card.get("primary") or not (card.get("pmid") or card.get("doi")):
        return reject("missing_primary_identifier")
    title = card["title"]
    lane = research_lane(title, card.get("mesh", []))
    if not lane:
        return reject("outside_scope")
    if NO_RESULTS_DESIGN.search(title):
        return reject("protocol_or_non_outcome")
    abstract = card.get("evidence") or ""
    parts = sections(abstract)
    result_text = parts.get("results") or parts.get("result") or parts.get("findings") or ""
    # Journals with no structured labels can still report a numeric outcome explicitly.
    methods = " ".join(parts.get(k, "") for k in ("methods", "method", "materials and methods", "study design", "design", "participants"))
    primary = re.search(r"primary (?:outcome|endpoint) (?:was|were) (?:a composite of )?([^.;(]{6,48})", methods, re.I)
    outcome = choose_result(result_text, primary[1].strip().casefold() if primary else None) if result_text else ""
    if not outcome and not result_text and not parts:
        outcome = choose_result(abstract)
        if not (outcome and METRIC.search(outcome) and number_set(outcome)):
            outcome = ""
    if not outcome:
        return reject("no_complete_outcome_excerpt")
    types = " ".join(card.get("publication_types", ()))
    design = next((label for expr, label in DESIGNS if re.search(expr, title + " " + types + " " + methods, re.I)),
                  "摘要未明确标示研究设计")
    sample = None
    for match in SAMPLES.finditer(methods):
        count, unit = (match[1], match[2]) if match[1] else (match[3], match[4])
        if unit.lower() in UNIT_LABEL and int(count.replace(",", "")) > 0:
            sample = (int(count.replace(",", "")), UNIT_LABEL[unit.lower()])
            break
    validation_sample = VALIDATION_SAMPLE.search(methods)
    if validation_sample:
        sample = (int((validation_sample[1] or validation_sample[2]).replace(",", "")), "例（外部验证队列）")
    if not sample and "荟萃分析" in design:
        pooled = re.search(r"\b([\d,]+) (patients|participants|knees|shoulders|hips)[;):]", result_text, re.I)
        if pooled and re.search(r"^[^.]{0,140}studies.* were included\b", result_text, re.I):
            sample = (int(pooled[1].replace(",", "")), UNIT_LABEL[pooled[2].lower()] + "（合并分析）")
    if not sample:
        responder = re.search(r"^Of ([\d,]+) pain responders, ", result_text)
        if responder:
            sample = (int(responder[1].replace(",", "")), "名疼痛缓解者（条件分析人群）")
    objectives = parts.get("purpose") or parts.get("objective") or parts.get("objectives") or parts.get("aim")
    question = next((value for value in reversed(list(fragments(objectives or "")))
                     if 6 <= len(value.split()) <= 23 and not BAD_EXCERPT.search(value)
                     and re.search(r"\b(?:aim|evaluat|assess|compar|investigat|validat|determin|examin|develop)", value, re.I)),
                    "")
    provenance = "结果段原文短引" if result_text else "非结构化摘要原文短引"
    quality = {
        "id": identifier, "status": "eligible", "lane": lane, "sample_confirmed": bool(sample),
        "structured_results": bool(result_text), "metric": bool(METRIC.search(outcome)),
        "test_only": bool(TEST_ONLY.search(title)),
        "validation": bool(PROGNOSTIC.search(title) or lane != "clinical"),
        "external_validation": bool(re.search(r"external(?:ly)? validat", title, re.I))
    }
    limitation = "摘要未能核实具体局限。仍需结合全文的偏倚、适用人群和终点判读。"
    if "观察性" in design or "回顾性" in design or "横断面" in design:
        limitation = ("回顾性模型验证，推广至其他医院前需额外验证。"
                      if lane != "clinical" else "观察性证据，比较结果可能受残余混杂影响。")
    elif lane != "clinical" and re.search(r"exam|benchmark|validation|performance", title, re.I):
        limitation = "该研究评估模型或任务表现，临床获益仍需另行验证。"
    explicit_limits = " ".join(parts.get(k, "") for k in ("limitations", "limitation"))
    short_limit = next((value for value in fragments(explicit_limits)
                        if 5 <= len(value.split()) <= 23 and not BAD_EXCERPT.search(value)), "")
    if short_limit:
        limitation = f"原文局限短引　“{short_limit}”"
    insight = INSIGHTS["ortho-ai" if quality["validation"] and lane == "clinical" else lane]
    if re.search(r"rotator[- ]cuff", title, re.I):
        insight = "可按撕裂大小预先分层，同时比较影像再撕裂与患者报告功能是否一致。"
    elif re.search(r"vertebroplasty", title, re.I):
        insight = "可将疼痛下降与功能改善设为不同结局，检验两者不一致的患者亚群。"
    elif re.search(r"prophylaxis|antibiotic", title, re.I):
        insight = "可在前瞻设计中并列记录感染、翻修及药物不良事件。"
    elif re.search(r"acetabul", title, re.I):
        insight = "可在独立队列验证骨折移位与加做内固定的关联，并预设不同骨折类型的分层。"
    row = {
        "id": identifier, "lane": "medical",
        "question": f"原文目的短引　“{question}”" if question else clean_text(title),
        "design": design + (f"；摘要报告 {sample[0]:,} {sample[1]}。" if sample else "；总样本量未从摘要明确提取。"),
        "result": f'{provenance}　“{outcome}”',
        "limits": limitation, "insight": insight,
    }
    return row, quality


def medical_digest(cards, as_of, priority=()):
    eligible, audit = [], []
    today = date.fromisoformat(as_of)
    for card in cards:
        if card["lane"] != "medical":
            continue
        age = (today - date.fromisoformat(card["date"])).days
        if not 0 <= age <= 30:
            audit.append({"id": card["id"], "status": "outside_date_window"})
            continue
        row, quality = paper_card(card)
        audit.append(quality)
        if row:
            eligible.append((age, card, row, quality))
    by_priority = {key: order for order, key in enumerate(priority)}
    eligible.sort(key=lambda x: (
        x[0] > 14, x[3]["test_only"], x[3]["lane"] == "medical-ai",
        x[1]["id"] not in by_priority, by_priority.get(x[1]["id"], 999),
        x[1]["title"].startswith("["), not x[3]["structured_results"],
        x[0] > 7, x[0] > 2, not x[3]["metric"], not x[3]["sample_confirmed"], x[0],
    ))
    used = set()
    unique = []
    for entry in eligible:
        _, card, _, _ = entry
        aliases = {str(card[key]).casefold() for key in ("pmid", "doi") if card.get(key)}
        if aliases & used:
            continue
        used |= aliases
        unique.append(entry)
    finalists = unique[:3]
    def strong_ai(entry):
        q = entry[3]
        return entry[0] <= 14 and q["lane"] == "ortho-ai" and not q["test_only"] and q["structured_results"]

    if len(finalists) > 1 and not any(strong_ai(x) for x in finalists):
        alternative = next((x for x in unique if strong_ai(x)), None)
        if alternative:
            finalists[-1] = alternative

    def domestic(entry):
        return "中国" in (entry[1].get("region") or "")

    if len(finalists) == 3 and not any(domestic(x) for x in finalists):
        local = next((x for x in unique if domestic(x) and x[0] <= 7 and not x[3]["test_only"]
                      and x[3]["structured_results"] and x[3]["metric"]), None)
        for index in range(len(finalists) - 1, -1, -1):
            if local and not strong_ai(finalists[index]):
                finalists[index] = local
                break
    return [x[2] for x in finalists], audit
