"""One controlled cloud acceptance, with separate generation and delivery claims."""
from calendar import monthrange
from datetime import datetime
from hashlib import sha256
import argparse
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import requests

from experiments.discover import CST
from experiments.run import Flash
from experiments.v14.pipeline import prepare_preview
from experiments.v15.sources import SearchFetch, TOOL_PRICE
from pni.presentation import document, measure, plan
from pni.production import DAILY, MONTHLY, TOOLS, V17_MODEL_REQUESTS, FILES, GitHubState, PushPlus, deliver_html

IDENTIFIER = "pni-v17-selection-acceptance-20261126"


def claim_once(step):
    if os.environ.get("GITHUB_RUN_ATTEMPT") != "1" or os.environ.get("GITHUB_REF") != "refs/heads/upgrade/news-pni-v1.7":
        raise RuntimeError("Acceptance must be a first attempt on the isolated branch")
    response = requests.post(
        f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}/git/refs",
        headers={"Authorization": "Bearer " + os.environ["GH_TOKEN"], "Accept": "application/vnd.github+json"},
        json={"ref": f"refs/tags/{IDENTIFIER}-{step}", "sha": os.environ["GITHUB_SHA"]},
        timeout=15, allow_redirects=False,
    )
    if response.status_code != 201:
        raise RuntimeError(f"Acceptance claim denied (HTTP {response.status_code})")


def check_month(state, day):
    month = day.isoformat()[:7]
    previous = state.get("budgets", {}).get(month, {}).get("reserved", 0)
    # Scheduled production is retained. Reserve every remaining calendar slot,
    # including today if it has no production reservation yet.
    unclaimed = sum(
        day.replace(day=number).isoformat() not in state.get("runs", {})
        for number in range(day.day, monthrange(day.year, day.month)[1] + 1)
    )
    maximum = previous + (unclaimed + 1) * DAILY
    if maximum > MONTHLY:
        raise RuntimeError("Acceptance reservation would breach the shared monthly ceiling")
    return {"production_reserved": previous, "acceptance_reserved": DAILY, "projected_month_max": maximum}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def generate(output):
    day = datetime.now(CST).date()
    state = GitHubState(os.environ["GITHUB_REPOSITORY"], os.environ["GH_TOKEN"]).data
    budget = check_month(state, day)
    claim_once("generate")
    provider = Flash(os.environ["DEEPSEEK_API_KEY"], DAILY - TOOLS * TOOL_PRICE,
                     max_reservation=DAILY, max_requests=V17_MODEL_REQUESTS)
    web = SearchFetch(provider.headers, TOOLS)
    report = {"day": day.isoformat(), "budget": budget}
    output.mkdir(parents=True, exist_ok=True)
    try:
        if provider.check_balance() < DAILY:
            raise RuntimeError("Insufficient balance for acceptance reservation")
        with TemporaryDirectory(prefix="pni-v17-acceptance-") as work:
            work = Path(work)
            index, health = work / "history.json", work / "source-health.json"
            write_json(index, state.get("history", []))
            write_json(health, state.get("source_health", {}))
            result = prepare_preview(output / "result.json", provider, history_path=index,
                                     source_state=health, web=web, final_edit=True, v17=True)
        if result["as_of"] != day.isoformat():
            raise RuntimeError("Acceptance crossed the reserved calendar day")
        briefs = {kind: (output / "news-preview" / name).read_text(encoding="utf-8") for kind, name in FILES.items()}
        messages = plan(briefs["professional"], briefs["extended"], day)
        manifest = []
        for index, msg in enumerate(messages, 1):
            markup = msg.html.encode("utf-8")
            (output / f"payload-{index}.txt").write_bytes(markup)
            (output / f"preview-{index}.html").write_text(document(msg.html), encoding="utf-8")
            manifest.append({"sha256": sha256(markup).hexdigest(), "title": msg.title, **measure(msg.html)})
        report.update({key: result[key] for key in ("selection", "counts", "errors", "selected", "gate", "discovery",
                                                   "news_candidates", "news_ranked", "candidate_audit")})
        report["parts"] = manifest
    finally:
        report.update(model_calls=provider.calls, search_fetch_calls=web.calls)
        report["estimated_micro_cny"] = sum(
            row.get("peak_estimate_micro_cny", row["reserved_micro_cny"]) for row in provider.calls
        ) + sum(row.get("observed_peak_micro_cny", row["max_micro_cny"]) for row in web.calls)
        write_json(output / "audit.json", report)
        print(json.dumps({key: report.get(key) for key in ("day", "budget", "selection", "counts", "parts", "estimated_micro_cny")},
                         ensure_ascii=False), flush=True)


def send(output, artifact, approval):
    audit = json.loads((artifact / "audit.json").read_text(encoding="utf-8"))
    from pni.presentation import Message
    from pni.review_v17 import manifest, reviewed_messages

    if audit["parts"] != approval["source_parts"]:
        raise RuntimeError("The source manifest differs from the generated artifact")
    reviewed = reviewed_messages(artifact, audit, approval["keep_pmids"])
    if manifest(reviewed) != approval["parts"]:
        raise RuntimeError("The reviewed HTML differs from the approved manifest")
    messages = [Message("【V1.7验收】" + msg.title, msg.html, msg.articles) for msg in reviewed]
    sender = PushPlus(os.environ["PUSHPLUS_TOKEN"], os.environ.get("PUSHPLUS_ACCESS_KEY", ""),
                      os.environ.get("PUSHPLUS_SECRET_KEY", ""))
    if not messages or not sender.token:
        raise RuntimeError("Sending requires a reviewed brief and configured PushPlus")
    claim_once("send")
    output.mkdir(parents=True, exist_ok=True)
    batch = {"mode": "acceptance", "lanes": {}}

    class Receipt:
        data = {}

        def save(self):
            write_json(output / "delivery.json", batch)

    deliver_html(Receipt(), batch, audit["day"], messages, [], [], sender)
    print(json.dumps(batch, ensure_ascii=False), flush=True)
    if batch["state"] == "needs_review":
        raise RuntimeError("Some acceptance messages require receipt review")


if __name__ == "__main__":
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("step", choices=("generate", "send"))
    args.add_argument("output", type=Path)
    args.add_argument("--artifact", type=Path)
    args.add_argument("--approval", type=Path)
    options = args.parse_args()
    if options.step == "generate":
        generate(options.output)
    else:
        send(options.output, options.artifact, json.loads(options.approval.read_text(encoding="utf-8")))
