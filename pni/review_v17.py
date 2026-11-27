"""Re-render vetted articles from the single acceptance run without a model call."""
from datetime import date
from hashlib import sha256
import re

from news_push import _pubmed_records
from pni import curation
from pni.editorial import ai_study_details
from pni.presentation import document, measure, plan
from pni.production import FILES


def reviewed_messages(root, audit, keep):
    records = audit["selected"]
    permitted = {row["pmid"]: row for row in records if row.get("pmid")}
    if len(set(keep)) != len(keep) or not 2 <= len(keep) <= 4 or set(keep) - permitted.keys():
        raise RuntimeError("Review must match two to four original selected studies")
    sources = _pubmed_records(keep)
    if {row["pmid"] for row in sources} != set(keep):
        raise RuntimeError("Original evidence is unavailable during review")
    details = {}
    for row in sources:
        pmid = row["pmid"]
        card = {**permitted[pmid], "evidence": row["abstract"]}
        topic = curation.field(card)
        if topic not in curation.SLOT:
            raise RuntimeError("Reviewed study does not match a professional category")
        details[pmid] = ai_study_details(pmid, row["abstract"]) if topic in {"ortho-ai", "medical-ai"} else {}

    archived = root / "news-preview"
    pro = (archived / FILES["professional"]).read_text(encoding="utf-8")
    ext = (archived / FILES["extended"]).read_text(encoding="utf-8")
    prefix, *entries = re.split(r"(?=^### )", pro, flags=re.M)
    indexed = {}
    for entry in entries:
        key = re.search(r"｜ PMID (\d+)", entry)
        if key and key[1] in details:
            for field, label in (("design", "设计与样本量"), ("limits", "局限")):
                if field in details[key[1]]:
                    entry = re.sub(rf"^\*\*{label}\*\*　.*$", f"**{label}**　{details[key[1]][field]}",
                                   entry, flags=re.M)
            indexed[key[1]] = entry
    if indexed.keys() != set(keep):
        raise RuntimeError("Reviewed study missing from original Markdown")
    return plan(prefix + "".join(indexed[key] for key in keep), ext, date.fromisoformat(audit["day"]))


def manifest(messages):
    return [{"sha256": sha256(msg.html.encode()).hexdigest(), "title": msg.title, **measure(msg.html)}
            for msg in messages]


def write_preview(path, messages):
    for index, msg in enumerate(messages, 1):
        (path / f"V1.7-微信验收-{index}.html").write_text(document(msg.html), encoding="utf-8")
