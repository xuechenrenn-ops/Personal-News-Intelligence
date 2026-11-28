import assert from "node:assert/strict";
import { test } from "node:test";
import worker from "./worker.mjs";

const env = { GITHUB_DISPATCH_TOKEN: "mock-token" };

function setup(t, scheduled, replies) {
  const calls = [];
  const messages = [];
  t.mock.method(Date, "now", () => scheduled + 2000);
  t.mock.method(globalThis, "setTimeout", (resolve) => resolve());
  t.mock.method(console, "log", (msg) => messages.push(JSON.parse(msg)));
  t.mock.method(console, "error", (msg) => messages.push(JSON.parse(msg)));
  t.mock.method(globalThis, "fetch", async (url, options) => {
    calls.push({ url, body: JSON.parse(options.body) });
    const reply = replies.shift();
    if (reply instanceof Error) throw reply;
    return reply;
  });
  return { calls, messages };
}

test("defaults to isolated probe and uses Beijing date across UTC month end", async (t) => {
  const scheduledTime = Date.parse("2026-11-30T22:30:00Z");
  const { calls, messages } = setup(t, scheduledTime, [Response.json({ workflow_run_id: 42 })]);
  await worker.scheduled({ cron: "30 22 * * *", scheduledTime }, env);
  assert.ok(calls[0].url.endsWith("/validate.yml/dispatches"));
  assert.deepEqual(calls[0].body, {
    ref: "ops/pni-cloudflare-probe",
    inputs: { beijing_date: "2026-12-01", edition: "morning", scheduled_beijing: "2026-12-01T06:30:00+08:00" },
  });
  assert.equal(messages.at(-1).run_id, 42);
});

test("retries transient dispatch failures with the same slot, then stops", async (t) => {
  const scheduledTime = Date.parse("2026-12-01T11:30:00Z");
  const { calls, messages } = setup(t, scheduledTime, [
    new Error("never logged"),
    new Response(null, { status: 503 }),
    new Response(null, { status: 204 }),
  ]);
  await worker.scheduled({ cron: "30 11 * * *", scheduledTime }, { ...env, PNI_DISPATCH_TARGET: "production" });
  assert.equal(calls.length, 3);
  assert.ok(calls[0].url.endsWith("/news-push.yml/dispatches"));
  assert.equal(calls[0].body.ref, "main");
  assert.equal(calls[0].body.inputs.scheduled_beijing, "2026-12-01T19:30:00+08:00");
  assert.deepEqual(calls[0], calls[2]);
  assert.equal(messages.at(-1).event, "dispatch_accepted");
});

test("does not retry invalid credentials or a long rate-limit delay", async (t) => {
  const scheduledTime = Date.parse("2026-12-01T11:30:00Z");
  const { calls, messages } = setup(t, scheduledTime, [
    new Response(null, { status: 401 }),
    new Response(null, { status: 429, headers: { "Retry-After": "120" } }),
  ]);
  const controller = { cron: "30 11 * * *", scheduledTime };
  await worker.scheduled(controller, env);
  assert.equal(calls.length, 1);
  assert.equal(messages.at(-1).event, "dispatch_failed");
  await worker.scheduled(controller, env);
  assert.equal(calls.length, 2);
  assert.equal(messages.at(-1).event, "dispatch_failed");
});

test("rejects an expired slot with no outbound requests", async (t) => {
  const scheduledTime = Date.parse("2026-12-01T11:30:00Z");
  const { calls, messages } = setup(t, scheduledTime, []);
  Date.now = () => scheduledTime + 7200000;
  await worker.scheduled({ cron: "30 11 * * *", scheduledTime }, env);
  assert.equal(calls.length, 0);
  assert.equal(messages.at(-1).event, "invalid_or_expired_slot");
});

test("empty and whitespace targets use the probe; unknown targets fail closed", async (t) => {
  const scheduledTime = Date.parse("2026-12-01T11:30:00Z");
  const { calls, messages } = setup(t, scheduledTime, [
    new Response(null, { status: 204 }),
    new Response(null, { status: 204 }),
  ]);
  const controller = { cron: "30 11 * * *", scheduledTime };
  for (const target of ["", "   ", "unknown"]) {
    await worker.scheduled(controller, { ...env, PNI_DISPATCH_TARGET: target });
  }
  assert.equal(calls.length, 2);
  assert.ok(calls.every((call) => call.body.ref === "ops/pni-cloudflare-probe"));
  assert.equal(messages.at(-1).event, "invalid_schedule_configuration");
});
