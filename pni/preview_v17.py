"""Offline V1.7 preview using the two archived Markdown briefs."""
import argparse
from datetime import date
import json
from pathlib import Path
import re

from pni.editorial import clean_repeated_terms, polish_verified
from pni.presentation import document, measure, plan


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True, help="Path with archived professional/extended Markdown")
    p.add_argument("--date", type=date.fromisoformat, required=True)
    p.add_argument("--abstract-43085541", type=Path, help="Optional locally verified PubMed abstract")
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()

    professional = clean_repeated_terms((args.source / "professional-automatic.md").read_text(encoding="utf-8"))
    extended = (args.source / "extended-automatic.md").read_text(encoding="utf-8")
    if args.abstract_43085541:
        abstract = args.abstract_43085541.read_text(encoding="utf-8")
        sections = re.split(r"(?=^### )", professional, flags=re.M)
        for index, entry in enumerate(sections):
            if "PMID 43085541" in entry:
                match = re.search(r"(?m)^\*\*原文主要结果\*\*　(.*)$", entry)
                if not match:
                    continue
                item = {"lane": "medical", "result": match[1]}
                polish_verified(item, {"pmid": "43085541", "evidence": abstract})
                if item["result"] != match[1]:
                    parts = ["**原文主要结果**　" + item["result"]]
                    if item.get("clinical") and "**临床意义（分析）**" not in entry:
                        parts.append("**临床意义（分析）**　" + item["clinical"])
                    sections[index] = entry[:match.start()] + "\n\n".join(parts) + entry[match.end():]
        professional = "".join(sections)

    args.out.mkdir(parents=True, exist_ok=True)
    messages = plan(professional, extended, args.date)
    output = []
    for i, row in enumerate(messages, 1):
        (args.out / f"PNI-V1.7-预览-{i:02d}.html").write_text(document(row.html), encoding="utf-8")
        output.append({"title": row.title, "articles": len(row.articles), **measure(row.html)})
    (args.out / "lengths.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
