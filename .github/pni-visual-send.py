"""Authorized layout replay of two immutable briefs. This module has no pipeline entry point."""
import base64
import hashlib
import json
import os
from pathlib import Path
import sys

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pni.production import PushPlus

SOURCE_RUN = "45813332802"
BRANCH = "ops/pni-visual-45813332802"
DAY = "2026-11-26"
FILES = (
    ("professional", "专业情报", "31c7c33df48a567944274bc4e8f6d9d27894825aed3face839b0cb59498b6a91"),
    ("extended", "拓展阅读", "d414ffb992ceb85dac8bb55d10ee1ba779232ffa5a75bb06f4337406492bbbef"),
)


def main():
    if (os.environ.get("GITHUB_REF") != "refs/heads/" + BRANCH
            or os.environ.get("GITHUB_RUN_ATTEMPT") != "1"
            or os.environ.get("GITHUB_EVENT_NAME") != "push"):
        raise SystemExit("Replay event not authorized")
    marker = json.loads(Path(".github/pni-visual-45813332802.json").read_text())
    if marker != {"id": "visual-45813332802", "source_run": SOURCE_RUN}:
        raise SystemExit("Unexpected replay marker")
    tmp = Path(os.environ["RUNNER_TEMP"])
    report = json.loads((tmp / "pni-visual-source/run-summary.json").read_text())
    if report.get("day") != DAY or str(report.get("delivery", {}).get("run_id")) != SOURCE_RUN:
        raise SystemExit("Unexpected source artifact")
    briefs = []
    for lane, title, digest in FILES:
        data = (tmp / f"pni-visual-source/news-preview/{lane}-automatic.md").read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise SystemExit("Original brief digest mismatch")
        briefs.append((lane, f"{title}｜{DAY}", data.decode("utf-8")))
    token = os.environ.get("PUSHPLUS_TOKEN")
    if not token:
        raise SystemExit("PushPlus send token is missing")
    # Create-only, isolated from production. A second runner cannot claim the request.
    claim = json.dumps({"run_id": os.environ["GITHUB_RUN_ID"], "source_run": SOURCE_RUN}).encode()
    repo = os.environ["GITHUB_REPOSITORY"]
    response = requests.put(
        f"https://api.github.com/repos/{repo}/contents/.github/pni-visual-claim-45813332802.json",
        headers={"Authorization": "Bearer " + os.environ["GH_TOKEN"], "Accept": "application/vnd.github+json"},
        json={"branch": BRANCH, "content": base64.b64encode(claim).decode(), "message": "Claim one-time visual replay"},
        timeout=15, allow_redirects=False,
    )
    if response.status_code != 201:
        raise SystemExit(f"Visual replay not claimed (HTTP {response.status_code}). No messages sent.")
    sender = PushPlus(token, os.environ.get("PUSHPLUS_ACCESS_KEY", ""), os.environ.get("PUSHPLUS_SECRET_KEY", ""))
    sender.authenticate()
    delivery = {}
    out = tmp / "pni-visual-receipts.json"
    for lane, title, brief in briefs:
        delivery[lane] = {"title": title, "status": "submission_unknown"}
        out.write_text(json.dumps(delivery, ensure_ascii=False, indent=2), encoding="utf-8")
        status, code = sender.send(title, brief)
        delivery[lane].update(status)
        if code:
            delivery[lane].update(sender.lookup(code))
        out.write_text(json.dumps(delivery, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({lane: delivery[lane]}, ensure_ascii=False), flush=True)
    if any(row["status"] not in {"accepted", "sent"} for row in delivery.values()):
        raise SystemExit("Inspect provider status. No resend was attempted.")


if __name__ == "__main__":
    main()
