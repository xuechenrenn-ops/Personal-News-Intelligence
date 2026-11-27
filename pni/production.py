"""Publishing entry point. GitHub state stores no article text or credentials."""
from base64 import b64decode, b64encode
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import time

import requests

from experiments.discover import CST
from experiments.run import Flash
from experiments.v14.pipeline import identities, prepare_preview
from experiments.v15.sources import SearchFetch, TOOL_PRICE
from pni.editions import EDITIONS, edition_for_cron
from pni.presentation import measure, plan, plan_edition

DAILY = 300_000  # Production ceiling; unchanged for monthly protection.
MONTHLY = 10_000_000
TOOLS = 5
V17_MODEL_REQUESTS = 10  # Allow rank, targeted repair, verify and one bounded reserve pass.
PREVIEW_RESERVATION = {"morning": 1_000_000, "evening": 1_000_000}  # Research-only, local preview.
STATE_BRANCH = "pni-production-state"
STATE_FILE = "pni-state.json"
ACCEPTANCE_PATH = Path(".github/pni-acceptance.json")
PUSHPLUS = "https://www.pushplus.plus"
LANES = {"professional": "专业情报", "extended": "拓展阅读"}
FILES = {key: key + "-automatic.md" for key in LANES}
SHORTCODE = re.compile(r"[A-Za-z0-9_-]{8,100}")
SCHEDULE_WINDOW = timedelta(hours=2)


class GitHubState:
    def __init__(self, repo, token, transport=requests):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise ValueError("Invalid GitHub repository")
        self.base = f"https://api.github.com/repos/{repo}"
        self.url = f"{self.base}/contents/{STATE_FILE}"
        self.headers = {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json"}
        self.transport = transport
        response = transport.get(self.url, headers=self.headers, params={"ref": STATE_BRANCH},
                                 allow_redirects=False, timeout=15)
        response.raise_for_status()
        record = response.json()
        self.sha = record["sha"]
        self.data = json.loads(b64decode(record["content"]))
        if self.data.get("version") != 1:
            raise ValueError("Unrecognized production state")

    def save(self):
        content = b64encode(json.dumps(self.data, separators=(",", ":")).encode()).decode()
        response = self.transport.put(
            self.url, headers=self.headers, allow_redirects=False, timeout=20,
            json={"branch": STATE_BRANCH, "sha": self.sha, "content": content,
                  "message": "Update Personal News Intelligence delivery ledger"},
        )
        response.raise_for_status()
        self.sha = response.json()["content"]["sha"]


    def purge_logs(self, day):
        cutoff = (datetime.fromisoformat(day).date() - timedelta(days=7)).isoformat()
        for run_day, record in self.data.get("runs", {}).items():
            run_id = str(record.get("run_id", ""))
            if run_day[:10] > cutoff or record.get("logs_expired") is True or not re.fullmatch(r"\d+", run_id):
                continue
            try:
                response = self.transport.delete(f"{self.base}/actions/runs/{run_id}/logs",
                                                 headers=self.headers, timeout=12, allow_redirects=False)
                if response.status_code in {204, 404, 410}:
                    record["logs_expired"] = True
                else:
                    print(f"Production log expiry unavailable for {run_day}: HTTP {response.status_code}")
            except requests.RequestException:
                print(f"Production log expiry unavailable for {run_day}")
        self.save()


def monthly_exposure(data, month):
    ledger = data.get("budgets", {}).get(month, {})
    runs = [row for day, row in data.get("runs", {}).items() if day[:7] == month]
    runs.extend(ledger.get("acceptance_runs", {}).values())
    pending = sum(row.get("uncertain_micro_cny", 0) if "estimate_micro_cny" in row
                  else row.get("reservation_micro_cny", DAILY) for row in runs)
    return ledger.get("observed", 0) + pending


def settle_usage(ledger, batch, observed, uncertain):
    if observed + uncertain > batch.get("reservation_micro_cny", DAILY):
        raise RuntimeError("Recorded API exposure exceeded the run reservation")
    if "estimate_micro_cny" in batch:
        return
    ledger["observed"] += observed
    batch.update(estimate_micro_cny=observed, uncertain_micro_cny=uncertain)


def metered_usage(model_calls, tool_calls):
    # Use the existing peak rate for measured model usage. Unmeasured requests
    # keep their individual allowance in the exposure, outside observed spend.
    observed = sum(row.get("peak_estimate_micro_cny", 0) for row in model_calls)
    observed += sum(row["max_micro_cny"] for row in tool_calls if row["status"] == "billable")
    uncertain = sum(row["reserved_micro_cny"] for row in model_calls if "peak_estimate_micro_cny" not in row)
    uncertain += sum(row["max_micro_cny"] for row in tool_calls
                     if row["status"] in {"uncertain", "transport_error", "malformed_response"})
    return observed, uncertain


def claim(state, day, mode, run_id, edition=None):
    d = state.data
    runs = d.setdefault("runs", {})
    key = f"{day}:{edition}" if edition else day
    if (key in runs or day in runs or (edition is None and any(f"{day}:{slot}" in runs for slot in EDITIONS))
            or (mode == "acceptance" and d.get("acceptance"))):
        return None
    month = day[:7]
    budgets = d.setdefault("budgets", {})
    ledger = budgets.setdefault(month, {"reserved": 0, "observed": 0})
    reservation = EDITIONS[edition]["reservation"] if edition else DAILY
    if monthly_exposure(d, month) + reservation > MONTHLY:
        raise RuntimeError("Monthly reservation would exceed CNY 10")
    # Retain the historical cumulative column for rollback. It is not spend.
    ledger["reserved"] += reservation
    batch = {"run_id": run_id, "mode": mode, "state": "reserved", "lanes": {}}
    if edition:
        batch.update(edition=edition, reservation_micro_cny=reservation)
    runs[key] = batch
    if mode == "acceptance":
        d["acceptance"] = {"day": day, "run_id": run_id}
    cutoff = (datetime.fromisoformat(day).date() - timedelta(days=30)).isoformat()
    d["runs"] = {key: entry for key, entry in runs.items() if key[:10] >= cutoff}
    d["history"] = [row for row in d.get("history", []) if row["day"] >= cutoff]
    d["budgets"] = {key: row for key, row in sorted(budgets.items())[-12:]}
    state.save()
    return batch


class PushPlus:
    def __init__(self, token, access="", secret="", transport=requests):
        self.token, self.access, self.secret = token, access, secret
        self.transport = transport
        self.lookup_issue = ""

    def authenticate(self):
        if self.access or not self.secret:
            return
        try:
            response = self.transport.post(f"{PUSHPLUS}/api/common/openApi/getAccessKey",
                                           json={"token": self.token, "secretKey": self.secret},
                                           timeout=12, allow_redirects=False)
            record = response.json()
            if response.ok and record.get("code") == 200:
                self.access = record["data"]["accessKey"]
            else:
                self.lookup_issue = "access_key_unavailable"
        except (requests.RequestException, ValueError, TypeError, KeyError):
            self.lookup_issue = "access_key_unavailable"

    def send(self, title, content, *, template="markdown"):
        try:
            response = self.transport.post(
                f"{PUSHPLUS}/send",
                json={"token": self.token, "title": title, "content": content,
                      "template": template, "channel": "wechat"},
                timeout=(8, 20), allow_redirects=False,
            )
            if response.status_code != 200:
                return {"status": "submission_unknown", "http": response.status_code}, None
            doc = response.json()
            code = doc.get("code")
            if code != 200:
                return {"status": "rejected", "provider_code": code if type(code) is int else None}, None
            value = doc.get("data")
            receipt = value if isinstance(value, str) else value.get("shortCode") if isinstance(value, dict) else None
            if not isinstance(receipt, str) or not SHORTCODE.fullmatch(receipt):
                return {"status": "accepted", "verification": "receipt_unavailable"}, None
            return {"status": "accepted", "receipt": receipt[:4] + "…" + receipt[-4:]}, receipt
        except (requests.RequestException, ValueError, KeyError, TypeError):
            return {"status": "submission_unknown"}, None

    def lookup(self, receipt, pause=time.sleep):
        if not self.access:
            return {"verification": self.lookup_issue or "status_access_key_missing"}
        last = None
        for attempt in range(3):
            try:
                response = self.transport.get(
                    f"{PUSHPLUS}/api/open/message/sendMessageResult",
                    headers={"access-key": self.access}, params={"shortCode": receipt},
                    timeout=12, allow_redirects=False,
                )
                doc = response.json()
                if response.ok and doc.get("code") == 200 and isinstance(doc.get("data"), dict):
                    last = doc["data"].get("status")
                    if last == 2:
                        return {"status": "sent", "verification": "provider_confirmed"}
                    if last == 3:
                        return {"status": "delivery_failed", "verification": "provider_confirmed"}
            except (requests.RequestException, ValueError, TypeError, KeyError):
                pass
            if attempt < 2:
                pause(3)
        return {"verification": "status_unavailable", "provider_status": last if type(last) is int else None}


def add_history(state, records, day):
    remembered = {(row["day"], tuple(row["keys"])) for row in state.data.get("history", [])}
    history = state.data.setdefault("history", [])
    for item in records:
        keys = sorted(identities(item))
        if keys and (day, tuple(keys)) not in remembered:
            history.append({"day": day, "keys": keys})
            remembered.add((day, tuple(keys)))


def exclusion_history(data):
    """Confirmed deliveries plus a separate hold on submitted but unconfirmed articles."""
    holds = [{"day": key[:10], "keys": keys}
             for key, run in data.get("runs", {}).items()
             for part in run.get("lanes", {}).values()
             if part.get("status") in {"accepted", "submission_unknown"}
             for keys in part.get("pending_keys", [])]
    return [*data.get("history", []), *holds]


def deliver(state, batch, day, briefs, records, alerts, transport):
    transport.authenticate()
    for lane, title in LANES.items():
        batch["lanes"][lane] = {"status": "submission_unknown"}
        state.save()  # An interrupted POST is never automatically replayed.
        prefix = "【试运行验收】" if batch["mode"] == "acceptance" else ""
        response, code = transport.send(f"{prefix}{title}｜{day}", briefs[lane])
        batch["lanes"][lane] = response
        state.save()
        if code:
            response.update(transport.lookup(code))
            state.save()
        # Retry only when the provider explicitly confirms the first delivery failed.
        # The authorized one-time acceptance test always submits at most two messages.
        if response["status"] == "delivery_failed" and batch["mode"] == "daily":
            batch["lanes"][lane] = {"status": "submission_unknown", "attempt": 2}
            state.save()
            retry, code = transport.send(f"{prefix}{title}｜{day}", briefs[lane])
            retry["attempt"] = 2
            batch["lanes"][lane] = response = retry
            state.save()
            if code:
                response.update(transport.lookup(code))
        if response["status"] in {"sent", "accepted"}:
            add_history(state, [row for row in records if (row["lane"] == "medical") == (lane == "professional")]
                        + (alerts if lane == "extended" else []), day)
        state.save()
    statuses = {x["status"] for x in batch["lanes"].values()}
    batch["state"] = ("sent" if statuses == {"sent"} else "accepted_waiting_status"
                      if statuses <= {"sent", "accepted"} else "needs_review")
    state.save()


def deliver_html(state, batch, day, messages, records, alerts, transport):
    """One durable status per immutable part; only a confirmed failure may be retried."""
    transport.authenticate()
    provider_attempts = 0

    def submit(title, markup):
        nonlocal provider_attempts
        if provider_attempts and provider_attempts % 5 == 0:
            time.sleep(61)
        provider_attempts += 1
        return transport.send(title, markup, template="html")

    for index, message in enumerate(messages, 1):
        key = f"part-{index}"
        if key in batch["lanes"]:
            continue
        details = {"sha256": sha256(message.html.encode()).hexdigest(),
                   "title": message.title, **measure(message.html)}
        edition = batch.get("edition")
        urls = ({article.source_url for article in message.articles if article.source_url} if edition else
                {url for article in message.articles for url in article.urls})
        included = [item for item in [*records, *alerts] if item.get("url") in urls]
        if edition:
            details["pending_keys"] = [sorted(keys) for item in included if (keys := identities(item))]
        for attempt in range(1, 3):
            part = {**details, "status": "submission_unknown", "attempt": attempt}
            batch["lanes"][key] = part
            state.save()
            response, code = submit(message.title, message.html)
            part.update(response)
            state.save()
            if code:
                part.update(transport.lookup(code))
            if part["status"] != "delivery_failed" or batch["mode"] != "daily":
                break
        if part["status"] == "sent" or (not edition and part["status"] == "accepted"):
            add_history(state, included, day)
        if part["status"] not in {"accepted", "submission_unknown"}:
            part.pop("pending_keys", None)
        state.save()
    statuses = {part["status"] for part in batch["lanes"].values()}
    batch["state"] = ("sent" if statuses == {"sent"} else "accepted_waiting_status"
                      if statuses and statuses <= {"sent", "accepted"} else "needs_review")
    state.save()


def edition_messages(briefs, day, edition, result, **kwargs):
    scores = result.get("scores", {})
    news_value = result.get("candidate_audit", {}).get("news_value", {})
    priorities = {card["url"]: (scores.get(card["id"], {}).get("weighted", 0) if edition == "morning" else
                              news_value.get(card["id"], 4) * 2 + (card["date"] == day))
                  for card in result["selected"]}
    return plan_edition(briefs["professional"], briefs["extended"], date.fromisoformat(day), edition,
                        priorities=priorities, **kwargs)


def execute(mode, output, edition=None):
    now = datetime.now(CST)
    day = now.date().isoformat()
    settings = EDITIONS[edition] if edition else {}
    if mode == "preview" and edition in PREVIEW_RESERVATION:
        reservation = PREVIEW_RESERVATION[edition]
    else:
        reservation = settings.get("reservation", DAILY)
    tools = settings.get("tools", TOOLS)
    key = os.environ["DEEPSEEK_API_KEY"].strip()
    if not key:
        raise RuntimeError("DeepSeek key is missing")
    sender = None
    state = None
    if mode != "preview":
        sender = PushPlus(os.environ["PUSHPLUS_TOKEN"].strip(), os.environ.get("PUSHPLUS_ACCESS_KEY", "").strip(),
                          os.environ.get("PUSHPLUS_SECRET_KEY", "").strip())
        if not sender.token:
            raise RuntimeError("PushPlus token is missing")
        state = GitHubState(os.environ["GITHUB_REPOSITORY"], os.environ["GH_TOKEN"])
        batch = claim(state, day, mode, os.environ["GITHUB_RUN_ID"], edition)
        if batch is None:
            print("Duplicate batch skipped before any paid request or send.", flush=True)
            return
    provider = Flash(key, reservation - tools * TOOL_PRICE, max_reservation=reservation, max_requests=V17_MODEL_REQUESTS)
    if provider.check_balance() < reservation:
        raise RuntimeError("DeepSeek balance below the complete daily reservation")
    web = SearchFetch(provider.headers, tools)
    from tempfile import TemporaryDirectory

    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="pni-acquisition-") as temp:
        runtime = Path(temp)
        history = runtime / "history.json"
        source_health = runtime / "source-health.json"
        data = state.data if state else {}
        history.write_text(json.dumps(exclusion_history(data)), encoding="utf-8")
        source_health.write_text(json.dumps(data.get("source_health", {})), encoding="utf-8")
        result = prepare_preview(output / "result.json", provider, history_path=history,
                                 source_state=source_health, web=web, final_edit=True, v17=True, edition=edition)
        if result["as_of"] != day:
            raise RuntimeError("Date crossed during the acquisition; the reservation is still recorded")
        observed, uncertain = metered_usage(provider.calls, web.calls)
        summary = {"mode": mode, "day": day, "counts": result["counts"],
                   "estimate_micro_cny": observed, "uncertain_micro_cny": uncertain,
                   "model_calls": len(provider.calls),
                   "web_calls": len(web.calls), "provider_errors": len(result["errors"]),
                   "provider_failures": result["errors"]}
        if edition:
            summary["reserved_micro_cny"] = reservation
            summary["model_tokens"] = {field: sum(row.get(field, 0) for row in provider.calls)
                                       for field in ("prompt_tokens", "completion_tokens")}
        summary.update(selection=result["selection"], candidate_audit=result["candidate_audit"],
                       field_gate=result["gate"])
        audit = result.get("run_audit")
        if audit:
            summary["run_audit"] = audit
        summary["source_report"] = result.get("discovery") or audit.get("source_report", {})
        summary["tool_calls"] = audit.get("tool_calls", []) if audit else []
        summary["channel_contribution"] = audit.get("channel_contribution", {}) if audit else {}
        if mode == "daily":
            age = trial_age(day, os.environ.get("PNI_TRIAL_START"))
            if age is not None and 0 <= age < 7:
                summary["trial_day"] = age + 1
        render_error = None
        if state or edition:
            if state:
                state.data["source_health"] = json.loads(source_health.read_text(encoding="utf-8"))
                settle_usage(state.data["budgets"][day[:7]], batch, observed, uncertain)
                batch["state"] = "prepared"
                state.save()
            briefs = {lane: (output / "news-preview" / filename).read_text(encoding="utf-8")
                      for lane, filename in FILES.items()}
            try:
                if edition:
                    messages, removed = edition_messages(briefs, day, edition, result)
                    summary.update(edition=edition, removed_for_capacity=removed,
                                   planned_articles=sum(len(message.articles) for message in messages))
                else:
                    messages = plan(briefs["professional"], briefs["extended"], date.fromisoformat(day))
            except ValueError as exc:
                render_error = exc
                if state:
                    batch.update(state="render_failed", render_error=str(exc))
                    state.save()
            else:
                for index, message in enumerate(messages, 1):
                    (output / "news-preview" / f"part-{index}.html").write_text(message.html, encoding="utf-8")
                if state:
                    if messages:
                        deliver_html(state, batch, day, messages, result["selected"], result["alerts"], sender)
                    else:
                        batch["state"] = "no_publishable_articles"
                        state.save()
            if state:
                summary["delivery"] = batch
                state.purge_logs(day)
        # Acquisition files are confined to this run's temporary directory.
        (output / "run-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                                                encoding="utf-8")
        print(json.dumps({key: value for key, value in summary.items()
                          if key not in {"candidate_audit", "field_gate", "run_audit"}},
                         ensure_ascii=False), flush=True)
        if render_error:
            raise render_error
        if state and batch["state"] == "needs_review":
            raise RuntimeError("Review partial or unknown delivery before any manual retry")


def trial_age(day, start):
    try:
        return (datetime.fromisoformat(day).date() - datetime.fromisoformat(start).date()).days
    except (ValueError, TypeError):
        return None


def daily_enabled(day, start):
    age = trial_age(day, start)
    return age is not None and age >= 0


def archived_preview(output):
    root = Path("experiments/v16/preview")
    archive = output / "news-preview"
    archive.mkdir(parents=True, exist_ok=True)
    for source, lane in (("professional-B.md", "professional"), ("extended-automatic.md", "extended")):
        (archive / FILES[lane]).write_bytes((root / source).read_bytes())
    print("Archived V1.6-B preview. No paid requests or deliveries.")


def scheduled_time(now, cron):
    minute, hour = map(int, cron.split()[:2])
    nominal = now.astimezone(timezone.utc).replace(hour=hour, minute=minute, second=0, microsecond=0)
    local = nominal.astimezone(CST)
    return local + (now.date() - local.date())


def main():
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise RuntimeError("Production runs only on main")
    edition = None
    output = Path(os.environ["RUNNER_TEMP"]) / "pni"
    if event == "push":
        if json.loads(ACCEPTANCE_PATH.read_text(encoding="utf-8")) != {"id": "v16-initial-wechat-acceptance"}:
            raise RuntimeError("Unrecognized acceptance trigger")
        mode = "acceptance"
    elif event == "schedule":
        now = datetime.now(CST)
        cron = os.environ.get("PNI_SCHEDULE_CRON")
        print(f"Schedule event: cron={cron!r}, beijing_now={now.isoformat()}", flush=True)
        edition = edition_for_cron(cron)
        nominal = scheduled_time(now, cron)
        if nominal > now:
            nominal -= timedelta(days=1)
        print(f"Schedule slot: edition={edition}, planned_beijing={nominal.isoformat()}", flush=True)
        if nominal.date() != now.date() or now - nominal >= SCHEDULE_WINDOW:
            print("Scheduled slot is on another Beijing date or outside its two-hour window.", flush=True)
            return
        mode = "daily"
    elif event == "workflow_dispatch":
        requested_day = os.environ.get("PNI_MORNING_CATCH_UP_DATE", "").strip()
        if not requested_day:
            return archived_preview(output)
        now = datetime.now(CST)
        morning = scheduled_time(now, EDITIONS["morning"]["cron"])
        evening = scheduled_time(now, EDITIONS["evening"]["cron"])
        print(f"Morning catch-up: requested={requested_day!r}, beijing_now={now.isoformat()}", flush=True)
        if requested_day != now.date().isoformat() or not morning <= now < evening:
            raise RuntimeError("Morning catch-up requires today's Beijing date, between 06:30 and 19:30")
        edition = "morning"
        mode = "daily"
    else:
        raise RuntimeError("Unrecognized event")
    if mode == "daily" and not daily_enabled(now.date().isoformat(), os.environ.get("PNI_TRIAL_START")):
        print("The approved production start date has not been reached.", flush=True)
        return
    execute(mode, output, edition)


if __name__ == "__main__":
    main()
