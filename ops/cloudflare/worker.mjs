const REPOSITORY = "xuechenrenn-ops/Personal-News-Intelligence";
const TARGETS = {
  probe: { workflow: "validate.yml", ref: "ops/pni-cloudflare-probe" },
  production: { workflow: "news-push.yml", ref: "main" },
};
const EDITIONS = { "30 22 * * *": "morning", "30 11 * * *": "evening" };
const WINDOW_MS = 2 * 60 * 60 * 1000;

export default {
  async scheduled(controller, env) {
    const mode = env.PNI_DISPATCH_TARGET ?? "probe";
    const target = Object.hasOwn(TARGETS, mode) ? TARGETS[mode] : null;
    const edition = EDITIONS[controller.cron];
    const scheduledMs = controller.scheduledTime;
    if (!target || !edition || !Number.isFinite(scheduledMs)) {
      console.error(JSON.stringify({ event: "invalid_schedule_configuration" }));
      return;
    }

    const local = new Date(scheduledMs + 8 * 60 * 60 * 1000).toISOString();
    const inputs = {
      beijing_date: local.slice(0, 10),
      edition,
      scheduled_beijing: local.slice(0, 19) + "+08:00",
    };
    const expectedTime = edition === "morning" ? "06:30:00.000" : "19:30:00.000";
    const startedMs = Date.now();
    const context = { mode, cron: controller.cron, ...inputs, started_utc: new Date(startedMs).toISOString() };
    if (local.slice(11, 23) !== expectedTime || startedMs < scheduledMs || startedMs - scheduledMs >= WINDOW_MS) {
      console.error(JSON.stringify({ ...context, event: "invalid_or_expired_slot" }));
      return;
    }
    if (!env.GITHUB_DISPATCH_TOKEN) {
      console.error(JSON.stringify({ ...context, event: "missing_dispatch_token" }));
      return;
    }
    console.log(JSON.stringify({ ...context, event: "dispatch_started" }));

    const url = "https://api.github.com/repos/" + REPOSITORY + "/actions/workflows/" + target.workflow + "/dispatches";
    const body = JSON.stringify({ ref: target.ref, inputs });
    for (let attempt = 1; attempt <= 3; attempt += 1) {
      if (Date.now() - scheduledMs >= WINDOW_MS) break;
      let status = null;
      let retryDelayMs = attempt * 5000;
      try {
        const response = await fetch(url, {
          method: "POST",
          headers: {
            "Authorization": "Bearer " + env.GITHUB_DISPATCH_TOKEN,
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "pni-cloudflare-scheduler",
            "X-GitHub-Api-Version": "2026-03-10",
          },
          body,
          redirect: "error",
          signal: AbortSignal.timeout(8000),
        });
        status = response.status;
        if (status === 200 || status === 204) {
          const result = status === 200 ? await response.json().catch(() => null) : null;
          const runId = Number.isSafeInteger(result?.workflow_run_id) ? result.workflow_run_id : null;
          console.log(JSON.stringify({ ...context, event: "dispatch_accepted", attempt, status, run_id: runId }));
          return;
        }
        const retryAfter = response.headers.get("Retry-After");
        if (retryAfter) {
          const seconds = Number(retryAfter);
          const delay = Number.isFinite(seconds) ? seconds * 1000 : Date.parse(retryAfter) - Date.now();
          if (Number.isFinite(delay)) retryDelayMs = Math.max(retryDelayMs, delay);
        } else if (status === 429 && response.headers.get("X-RateLimit-Remaining") === "0") {
          retryDelayMs = Infinity;
        }
        await response.body?.cancel().catch(() => {});
      } catch {
        // A transport error can hide a successful dispatch. Production claims
        // the same dated edition in the ledger before any paid use or send.
      }
      const transient = status === null || status === 429 || [500, 502, 503, 504].includes(status);
      const retry = transient && attempt < 3 && retryDelayMs <= 15000 &&
        Date.now() + retryDelayMs - scheduledMs < WINDOW_MS;
      console.error(JSON.stringify({ ...context, event: retry ? "dispatch_retry" : "dispatch_failed", attempt, status }));
      if (!retry) return;
      await new Promise((resolve) => setTimeout(resolve, retryDelayMs));
    }
    console.error(JSON.stringify({ ...context, event: "dispatch_window_expired" }));
  },
};
