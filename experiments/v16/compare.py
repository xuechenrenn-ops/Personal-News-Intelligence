"""One authorized M3-Plus request, then a paired public-literature replay."""
from copy import deepcopy
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import time

import requests

from experiments.discover import collect_live_cards, medical_card
from experiments.run import Flash, claim_dispatch
from experiments.stage2.channels import metadata
from experiments.v16.editorial import routine_event
from news_push import _pubmed_records
from experiments.v14.pipeline import (abstract_packet, active_cards, identities,
                                      prepare_preview, ranked_candidates, verify_citation)

DISPATCH_ID = "v16-baichuan-once"
TARGETS = ("43058301", "43085613", "43085541")
MODEL = "Baichuan-M3-Plus"
ENDPOINT = "https://api.baichuan-ai.com/v1/chat/completions"
TOPIC = re.compile(r"hip fracture|femoral neck|proximal fem|intertrochanter|股骨近端|髋部骨折|股骨颈骨折", re.I)
METHOD = re.compile(r"artificial intelligence|deep learn|network|machine learn|automat|computer.aid|CT\b|诊断|深度学习|指南", re.I)
PMID = re.compile(r"(?:PMID\s*[:：]?\s*|pubmed\.ncbi\.nlm\.nih\.gov/)(\d{7,9})\b", re.I)
DOI = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)


def response_documents(response):
    if "event-stream" not in response.headers.get("Content-Type", ""):
        yield response.json()
        return
    for line in response.iter_lines():
        if not line.startswith(b"data:"):
            continue
        data = line[5:].strip()
        if data == b"[DONE]":
            break
        if data:
            yield json.loads(data)


def parse_evidence(text):
    start = text.find("{")
    if start >= 0:
        try:
            data, _ = json.JSONDecoder().raw_decode(text[start:])
            items = data.get("items", [])
            if isinstance(items, list) and items:
                return [row for row in items if isinstance(row, dict)][:8]
        except (ValueError, AttributeError):
            pass
    return ([{"pmid": n} for n in dict.fromkeys(PMID.findall(text))] +
            [{"doi": n.rstrip(".,;)")} for n in dict.fromkeys(DOI.findall(text))])[:8]


def ask_once(key, target):
    prompt = (
        "为骨科医生补充以下研究的外部循证证据。重点查找 CT 深度学习对股骨近端骨折的诊断、"
        "分型或骨折线量化的独立验证、反向证据、指南及方法比较。只报告 4 条最相关、可独立查证的发表研究。"
        "列出准确的 DOI 或 PMID 及完整原标题，至少指出研究人群、验证方式、结局和适用边界。若没有可靠证据，返回空。"
        '输出 JSON {"items":[{"pmid":"编号","doi":"编号","title":"原始题名","finding":"核心发现"}]}。'
        "禁止引用目标论文自身或补造标识。目标文献：\n"
        + target["title"] + "\nPMID " + target["pmid"] + "\n" + target["evidence"][:1700]
    )
    usage = {}
    result = {"attempts": 1, "model": MODEL, "status": "unknown", "medical_search_calls": None,
              "token_cost_micro_cny": None, "total_cost_micro_cny": None}
    content = []
    citations = []
    started = time.monotonic()
    try:
        with requests.post(
            ENDPOINT,
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
            json={"model": MODEL, "messages": [{"role": "user", "content": prompt}],
                  "stream": True, "temperature": 0.2, "with_search_enhance": False},
            timeout=(12, 100), stream=True, allow_redirects=False,
        ) as response:
            result["http_status"] = response.status_code
            if response.status_code != 200:
                result["status"] = "http_error"
                return result, []
            for doc in response_documents(response):
                if not isinstance(doc, dict):
                    continue
                if isinstance(doc.get("usage"), dict):
                    usage.update(doc["usage"])
                if doc.get("error") or (doc.get("code") not in (None, 0, 200, "0", "200")):
                    result["status"] = "provider_error"
                    if isinstance(doc.get("code"), int):
                        result["provider_code"] = doc["code"]
                    return result, []
                for choice in doc.get("choices", []):
                    message = choice.get("delta") or choice.get("message") or {}
                    if isinstance(message.get("content"), str):
                        content.append(message["content"])
                    for value in (message.get("citations"), choice.get("citations"), doc.get("citations")):
                        if isinstance(value, list):
                            citations.extend(str(item) for item in value)
                    if isinstance(choice.get("finish_reason"), str):
                        result["finish_reason"] = choice["finish_reason"][:30]
            result["status"] = "completed" if content else "empty"
    except (requests.RequestException, ValueError, TypeError) as exc:
        result["status"] = type(exc).__name__
    finally:
        result["seconds"] = round(time.monotonic() - started, 2)
    for name in ("prompt_tokens", "completion_tokens", "total_tokens", "medical_search_calls"):
        if type(usage.get(name)) is int and usage[name] >= 0:
            result[name] = usage[name]
    if "prompt_tokens" in result and "completion_tokens" in result:
        result["token_cost_micro_cny"] = 5 * result["prompt_tokens"] + 9 * result["completion_tokens"]
        if result["medical_search_calls"] is not None:
            result["total_cost_micro_cny"] = result["token_cost_micro_cny"] + 30_000 * result["medical_search_calls"]
    return result, parse_evidence("".join(content)) + parse_evidence("\n".join(citations))


def audit_leads(leads, target, as_of, verify=verify_citation):
    seen = set(identities(target))
    audited = []
    admitted = []
    for raw in leads[:12]:
        lead = {}
        for key in ("pmid", "doi"):
            value = raw.get(key)
            if isinstance(value, str) and (DOI.fullmatch(value) if key == "doi" else re.fullmatch(r"\d{7,9}", value)):
                lead[key] = value
        note = {"claimed": lead}
        if not lead:
            continue
        try:
            record = verify(lead)
        except (OSError, ValueError, RuntimeError, KeyError):
            record = None
        if not record:
            note["status"] = "unverified"
        elif identities(record) & seen:
            note["status"] = "duplicate_or_target"
        else:
            seen.update(identities(record))
            note.update({"source": metadata(record), "status": "relatedness_unverified"})
            combined = record["title"] + " " + record.get("evidence", "")[:1600]
            if date.fromisoformat(record["date"]) <= date.fromisoformat(as_of) and TOPIC.search(combined) and METHOD.search(combined):
                note["status"] = "verified_and_relevant"
                if len(admitted) < 3:
                    admitted.append({**metadata(record), "evidence": abstract_packet(record)})
        audited.append(note)
    return admitted, audited


def run(identifier, plan, output):
    if identifier != DISPATCH_ID or plan.get("baichuan_one_request") is not True:
        raise RuntimeError("V1.6 requires the single authorized dispatch")
    reserve = plan["reserve_micro_cny"]
    key = os.environ["DEEPSEEK_API_KEY"]
    baichuan = os.environ["BAICHUAN_API_KEY"]
    if not baichuan:
        raise RuntimeError("Baichuan credential unavailable")
    selection, a, b = (Flash(key, reserve // 3, max_reservation=300_000) for _ in range(3))
    if selection.check_balance() < reserve:
        raise RuntimeError("DeepSeek balance insufficient")
    records = {row["pmid"]: row for row in _pubmed_records(list(TARGETS))}
    target = []
    for pmid in TARGETS:
        record = records.get(pmid)
        paper = medical_card({"id": pmid, "title": record["title"], "pubmed_record": record},
                             allow_possible=True) if record else None
        if paper:
            paper["evidence"] = record["abstract"]
        target.append(paper)
    headlines, as_of = collect_live_cards(include_academic=False, wider=True)
    headlines = [item for item in headlines if not routine_event(item)]
    if any(not row for row in target) or len(active_cards(target, as_of)) != len(TARGETS):
        raise RuntimeError("Could not freeze every primary evidence record")
    ranked, scores, world = ranked_candidates(selection, target, as_of, headlines)
    fixed = tuple(row["id"] for row in ranked)
    fingerprint = hashlib.sha256(json.dumps(target, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    # A single create-only ledger entry gates the vendor POST even after workflow restarts.
    claim_dispatch(DISPATCH_ID, os.environ["GH_TOKEN"], os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_RUN_ID"])
    result = {"phase": plan["phase"], "as_of": as_of, "fixed_ids": fixed, "fixed_world": world, "evidence_sha256": fingerprint,
              "scores": scores, "wechat_requests": 0, "baichuan": {"attempts": 0}, "evidence": [], "arms": {}}
    output.parent.mkdir(parents=True, exist_ok=True)

    def checkpoint():
        result["calls"] = {"common": selection.calls, "A": a.calls, "B": b.calls}
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    result["baichuan"], leads = ask_once(baichuan, target[0])
    checkpoint()
    admitted, result["evidence"] = audit_leads(leads, target[0], as_of)
    result["admitted"] = [metadata(row) for row in admitted]
    arms = (("B", b, {}), ("A", a, {TARGETS[0]: admitted}))
    for name, provider, extra in arms:
        destination = output.parent / name
        arm = prepare_preview(destination / "unused.json", provider,
                              evaluation=(as_of, deepcopy(target), deepcopy(headlines), {}),
                              final_edit=True, fixed_ids=fixed, verified_extra=extra, frozen_scores=scores, fixed_world=world)
        result["arms"][name] = {field: arm[field] for field in ("counts", "selected", "gate", "errors")}
        (destination / "news-preview/professional-automatic.md").replace(output.parent / f"professional-{name}.md")
        if name == "B":
            (destination / "news-preview/extended-automatic.md").replace(output.parent / "extended-automatic.md")
        checkpoint()
