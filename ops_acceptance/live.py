"""One-off cloud acceptance through the frozen V1.7 production pipeline."""
import argparse
from base64 import b64encode
from calendar import monthrange
from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess

import requests

from pni import production

FROZEN_MAIN = "aea275fdc7dbe7fcad7a7e5b8ae19edd9be57e6c"
TODAY = "2026-11-27"
BRANCH = "ops/pni-v17-live-acceptance-20261127"
STATE_BRANCH = BRANCH + "-state"
PREVIOUS_PREVIEW = "63e853903d2704b77ad9b46cd7b7b2a7b4161e38ef5082f424f8007e3332249b"


def check_context():
    if (os.environ.get("GITHUB_REF") != f"refs/heads/{BRANCH}"
            or os.environ.get("GITHUB_RUN_ATTEMPT") != "1"
            or datetime.now(production.CST).date().isoformat() != TODAY):
        raise SystemExit("This acceptance is allowed once on the authorized Beijing date")
    parent = subprocess.check_output(["git", "rev-parse", "HEAD^"], text=True).strip()
    paths = subprocess.check_output(["git", "diff", "--name-only", "HEAD^", "HEAD"], text=True).splitlines()
    if parent != FROZEN_MAIN or set(paths) != {".github/workflows/pni-v17-live-acceptance.yml",
                                               "ops_acceptance/live.py"}:
        raise SystemExit("The reviewed production revision or operation files differ")


def initialize():
    official = production.GitHubState(os.environ["GITHUB_REPOSITORY"], os.environ["GH_TOKEN"])
    now = datetime.now(production.CST).date()
    month = TODAY[:7]
    reserved = official.data.get("budgets", {}).get(month, {}).get("reserved", 0)
    daily_reservation = sum(row["reservation"] for row in production.EDITIONS.values())
    days_left = monthrange(now.year, now.month)[1] - now.day
    if reserved + daily_reservation * (1 + days_left) > production.MONTHLY:
        raise SystemExit("Combined acceptance and remaining scheduled reservations would exceed the monthly cap")
    if any(key[:10] == TODAY for key in official.data.get("runs", {})):
        raise SystemExit("A production batch already exists for today; stopped before any paid request")

    data = {
        "version": 1,
        "runs": {},
        "history": production.exclusion_history(official.data),
        "budgets": official.data.get("budgets", {}),
        "source_health": official.data.get("source_health", {}),
    }
    response = requests.post(official.base + "/git/refs", headers=official.headers,
                             json={"ref": f"refs/heads/{STATE_BRANCH}", "sha": os.environ["GITHUB_SHA"]},
                             allow_redirects=False, timeout=20)
    if response.status_code != 201:
        raise SystemExit(f"Single-use state branch creation failed (HTTP {response.status_code}); no provider requests")
    response = requests.put(
        official.url, headers=official.headers, timeout=20, allow_redirects=False,
        json={"branch": STATE_BRANCH, "message": "Initialize isolated V1.7 acceptance ledger",
              "content": b64encode(json.dumps(data, ensure_ascii=False).encode()).decode()},
    )
    if response.status_code != 201:
        raise SystemExit(f"Acceptance ledger initialization failed (HTTP {response.status_code})")
    print("Independent ledger created. Production state was used read-only.", flush=True)


class AcceptancePushPlus(production.PushPlus):
    submitted = False

    def send(self, title, content, *, template="markdown"):
        if self.submitted or sha256(content.encode()).hexdigest() == PREVIOUS_PREVIEW:
            return {"status": "duplicate_suppressed"}, None
        self.submitted = True
        return super().send("【V1.7全流程验收】" + title, content, template=template)


def run(edition):
    if edition == "morning" and not os.environ.get("TAVILY_KEY", "").strip():
        raise SystemExit("The configured Tavily channel did not reach the morning job")
    production.STATE_BRANCH = STATE_BRANCH
    production.PushPlus = AcceptancePushPlus
    output = Path(os.environ["RUNNER_TEMP"]) / "pni-live-acceptance" / edition
    production.execute("daily", output, edition)
    summary = json.loads((output / "run-summary.json").read_text(encoding="utf-8"))
    if summary.get("delivery", {}).get("state") not in {"sent", "accepted_waiting_status"}:
        raise SystemExit("No accepted delivery was recorded; preserve the ledger and do not retry")


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    cli.add_argument("stage", choices=("initialize", "morning", "evening"))
    stage = cli.parse_args().stage
    check_context()
    if stage == "initialize":
        initialize()
    else:
        run(stage)
