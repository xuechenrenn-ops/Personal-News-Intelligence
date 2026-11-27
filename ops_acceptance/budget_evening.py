"""One live evening run with its own delivery state and a shared budget hold."""
from base64 import b64encode
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess

import requests

from pni import production

RELEASE = "9edb5b14c881ac3fbf248bb4f443aa5b1ec0a8bd"
DAY = "2026-11-27"
BRANCH = "ops/pni-v17-budget-evening-20261127"
OFFICIAL_STATE = "pni-production-state"
PREVIOUS_STATE = "ops/pni-v17-live-acceptance-20261127-state"
STATE_BRANCH = BRANCH + "-state"
PREVIOUS_ID = "live-45974601709"
ACCEPTANCE_ID = "v17-budget-evening-20261127"
PREVIOUS_PREVIEW = "63e853903d2704b77ad9b46cd7b7b2a7b4161e38ef5082f424f8007e3332249b"


def read_state(branch):
    production.STATE_BRANCH = branch
    return production.GitHubState(os.environ["GITHUB_REPOSITORY"], os.environ["GH_TOKEN"])


def check_context():
    parent = subprocess.check_output(["git", "rev-parse", "HEAD^"], text=True).strip()
    if (parent != RELEASE or os.environ.get("GITHUB_REF") != f"refs/heads/{BRANCH}"
            or os.environ.get("GITHUB_RUN_ATTEMPT") != "1"
            or datetime.now(production.CST).date().isoformat() != DAY):
        raise SystemExit("Unexpected acceptance revision, time, branch, or attempt")


def initialize():
    official = read_state(OFFICIAL_STATE)
    previous = read_state(PREVIOUS_STATE)
    production.STATE_BRANCH = OFFICIAL_STATE
    ref = requests.get(official.base + "/git/ref/heads/main", headers=official.headers, timeout=15,
                       allow_redirects=False)
    ref.raise_for_status()
    if ref.json()["object"]["sha"] != RELEASE:
        raise SystemExit("The tested budget release is not deployed")
    previous_runs = previous.data["runs"]
    if (previous_runs[f"{DAY}:morning"]["state"] != "accepted_waiting_status"
            or previous_runs[f"{DAY}:evening"]["state"] != "no_publishable_articles"
            or previous_runs[f"{DAY}:evening"]["lanes"]
            or f"{DAY}:evening" in official.data.get("runs", {})):
        raise SystemExit("Delivery history does not permit a single evening retry")
    previous_meter = sum(row["estimate_micro_cny"] for row in previous_runs.values())
    if previous_meter != 92_358:
        raise SystemExit("Unexpected previously audited usage")

    month = DAY[:7]
    ledger = official.data["budgets"].setdefault(month, {"reserved": 0, "observed": 0})
    entries = ledger.setdefault("acceptance_runs", {})
    if ACCEPTANCE_ID in entries:
        raise SystemExit("This acceptance already has a budget entry")
    # The archived run counted one empty search, which is free; tokens retain the peak ceiling.
    prior_observed = 0 if PREVIOUS_ID in entries else previous_meter - 5_000
    reservation = production.EDITIONS["evening"]["reservation"]
    if production.monthly_exposure(official.data, month) + prior_observed + reservation > production.MONTHLY:
        raise SystemExit("The monthly cap cannot cover this acceptance")
    lock = requests.post(
        official.base + "/git/refs", headers=official.headers, timeout=20, allow_redirects=False,
        json={"ref": f"refs/heads/{STATE_BRANCH}", "sha": os.environ["GITHUB_SHA"]},
    )
    if lock.status_code != 201:
        raise SystemExit(f"Single-use claim unavailable (HTTP {lock.status_code}); no API calls or send")

    if PREVIOUS_ID not in entries:
        ledger["observed"] += prior_observed
        entries[PREVIOUS_ID] = {"estimate_micro_cny": prior_observed, "uncertain_micro_cny": 0}
    entries[ACCEPTANCE_ID] = {"reservation_micro_cny": reservation, "run_id": os.environ["GITHUB_RUN_ID"]}
    ledger["reserved"] += prior_observed + reservation
    official.save()  # Production writes are limited to the monthly budget.
    seed = {
        "version": 1, "runs": {},
        "history": [*production.exclusion_history(official.data), *production.exclusion_history(previous.data)],
        "budgets": deepcopy(official.data["budgets"]), "source_health": official.data.get("source_health", {}),
    }
    # The isolated production entry claims this same hold as its own batch.
    del seed["budgets"][month]["acceptance_runs"][ACCEPTANCE_ID]
    result = requests.put(
        official.url, headers=official.headers, timeout=20, allow_redirects=False,
        json={"branch": STATE_BRANCH, "message": "Initialize isolated evening acceptance ledger",
              "content": b64encode(json.dumps(seed, ensure_ascii=False).encode()).decode()},
    )
    if result.status_code != 201:
        raise SystemExit(f"Isolated ledger initialization failed (HTTP {result.status_code})")


class OnePreview(production.PushPlus):
    submitted = False

    def send(self, title, content, *, template="markdown"):
        if self.submitted or sha256(content.encode()).hexdigest() == PREVIOUS_PREVIEW:
            return {"status": "duplicate_suppressed"}, None
        self.submitted = True
        return super().send("【V1.7全流程验收】" + title, content, template=template)


def settle():
    local = read_state(STATE_BRANCH)
    row = local.data.get("runs", {}).get(f"{DAY}:evening", {})
    if "estimate_micro_cny" not in row:
        print("No measured settlement; full allowance remains held.", flush=True)
        return
    official = read_state(OFFICIAL_STATE)
    ledger = official.data["budgets"][DAY[:7]]
    batch = ledger["acceptance_runs"][ACCEPTANCE_ID]
    production.settle_usage(ledger, batch, row["estimate_micro_cny"], row.get("uncertain_micro_cny", 0))
    official.save()
    print(json.dumps({"acceptance_observed_micro_cny": batch["estimate_micro_cny"],
                      "acceptance_uncertain_micro_cny": batch["uncertain_micro_cny"],
                      "month_exposure_micro_cny": production.monthly_exposure(official.data, DAY[:7])}), flush=True)


if __name__ == "__main__":
    check_context()
    initialize()
    production.STATE_BRANCH = STATE_BRANCH
    production.PushPlus = OnePreview
    folder = Path(os.environ["RUNNER_TEMP"]) / "pni-budget-evening"
    try:
        production.execute("daily", folder, "evening")
    finally:
        settle()
    audit = json.loads((folder / "run-summary.json").read_text(encoding="utf-8"))
    if audit.get("delivery", {}).get("state") not in {"sent", "accepted_waiting_status"}:
        raise SystemExit("No accepted evening delivery; do not replay this operation")
