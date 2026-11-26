"""Render two preview messages from reviewed evidence cards and normalized selections."""
import argparse
import json
from pathlib import Path

from experiments.run import FIELDS, normalize


def clean_text(value):
    return " ".join(value.split()).replace("<", "＜").replace(">", "＞")


def render_messages(items, cards, as_of):
    sources = {row["id"]: row for row in cards}
    lanes = {"medical": [], "policy": [], "world": []}
    for item in normalize(items, cards):
        card = sources[item["id"]]
        if not card["primary"]:
            continue
        editorial = {field: clean_text(item[field]) for field in FIELDS if field in item}
        block = [f"### {editorial.get('headline') or clean_text(card['title'])}",
                 f"{card['source']} ｜ {card['date']}"]
        if item["lane"] == "medical":
            if card.get("region"):
                block[-1] += f" ｜ {card['region']}"
            if editorial.get("summary"):
                block.append(f"**研究问题**　{editorial['summary']}")
            for field, label, fallback in (
                ("evidence", "设计与结果", "样本量与结果未核实。"),
                ("limitations", "局限", "局限未核实。"),
                ("sports_medicine_insight", "科研启发", "暂无可独立核实的启发。"),
            ):
                block.append(f"**{label}**　{editorial.get(field) or fallback}")
        elif editorial.get("summary"):
            block.append(editorial["summary"])
        note = card.get("verification_note")
        if note:
            block.append(clean_text(note))
        link_label = "报道来源" if note else "原始来源"
        source = f"[{link_label}]({card['url']})"
        if card.get("pmid"):
            source += f" ｜ PMID {card['pmid']}"
        if card.get("doi"):
            source += f" ｜ DOI {card['doi']}"
        block.append(source)
        lanes[item["lane"]].append("\n\n".join(block))

    empty = "本次候选中暂无达到证据与时效要求的资讯。"
    professional = "\n\n".join(
        [f"# 专业情报｜{as_of}", *(lanes["medical"] or [empty])]
    )
    extended = "\n\n".join([
        f"# 拓展阅读｜{as_of}", "## 中国医疗政策与民生", *(lanes["policy"] or [empty]),
        "## 国内外重大事件", *(lanes["world"] or [empty]),
    ])
    return professional + "\n", extended + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("reviewed", type=Path)
    parser.add_argument("fixture", type=Path)
    parser.add_argument("output_directory", type=Path)
    args = parser.parse_args()
    result = json.loads(args.reviewed.read_text(encoding="utf-8"))
    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    if result["phase"] == "live":
        cards, as_of = result["sources"], result["as_of"]
    else:
        split = "holdout" if result["phase"] == "holdout" else "development"
        cards, as_of = fixture[split], fixture["as_of"]
    messages = render_messages(result["items"], cards, as_of)
    args.output_directory.mkdir(parents=True, exist_ok=True)
    for name, message in zip(("professional-preview.md", "extended-preview.md"), messages):
        (args.output_directory / name).write_text(message, encoding="utf-8")
