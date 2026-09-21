/**
 * The Worker->origin search auth contract (ADR-0075, Wave 2B).
 *
 * `cs-api.barnardhq.com` is directly internet-reachable (the cloudflared
 * ingress allowlist has to admit `/api/archive/search` for THIS Worker to
 * reach it), so the origin cannot assume Turnstile/the per-IP limiter ran —
 * both are edge-only, applied solely to traffic that came through
 * `cs.barnardhq.com`. `handleSearch` now forwards a constant-time Bearer
 * (`ORIGIN_SEARCH_SECRET`) the origin's `_require_worker_bearer`
 * (backend/app/api/archive.py) checks before doing anything else.
 *
 * Same discipline as `push.test.ts`: every "X is absent/forwarded" claim
 * gets a positive control so the guard can actually fail.
 */

import test from "node:test";
import assert from "node:assert/strict";

import worker from "../src/index.ts";

const ORIGIN = "https://origin.test";
const HOST = "https://callsignlane.com";

type Call = { url: string; method: string; headers: Record<string, string> };

function stubOrigin(payload: unknown = { results: [] }, status = 200) {
  const calls: Call[] = [];
  const real = globalThis.fetch;
  globalThis.fetch = (async (input: unknown, init: Record<string, any> = {}) => {
    const headers: Record<string, string> = {};
    for (const [k, v] of Object.entries(init.headers ?? {})) headers[k.toLowerCase()] = String(v);
    calls.push({ url: String(input), method: init.method ?? "GET", headers });
    return new Response(JSON.stringify(payload), {
      status,
      headers: { "content-type": "application/json" },
    });
  }) as unknown as typeof globalThis.fetch;
  return { calls, restore: () => void (globalThis.fetch = real) };
}

const get = (path: string) => new Request(`${HOST}${path}`, { method: "GET" });

// No SEARCH_RATE_LIMITER/TURNSTILE_SECRET bound in these tests — both fail
// open by design (see turnstileGate), so they never block reaching the
// origin fetch this suite is actually pinning.
const env = (overrides: Record<string, unknown> = {}) => ({ ORIGIN, ...overrides }) as Parameters<typeof worker.fetch>[1];

test("search forwards a Bearer authorization header when ORIGIN_SEARCH_SECRET is set", async () => {
  const s = stubOrigin();
  try {
    const res = await worker.fetch(
      get("/api/archive/search?date_from=2026-01-01&date_to=2026-01-01"),
      env({ ORIGIN_SEARCH_SECRET: "test-only-shared-secret" }),
    );
    assert.equal(res.status, 200);
    assert.equal(s.calls.length, 1);
    assert.equal(s.calls[0].headers["authorization"], "Bearer test-only-shared-secret");
  } finally {
    s.restore();
  }
});

test("search sends NO authorization header when ORIGIN_SEARCH_SECRET is unbound", async () => {
  const s = stubOrigin();
  try {
    const res = await worker.fetch(
      get("/api/archive/search?date_from=2026-01-01&date_to=2026-01-01"),
      env(), // no ORIGIN_SEARCH_SECRET
    );
    assert.equal(res.status, 200);
    assert.equal(s.calls.length, 1);
    // Positive control on the SAME call, so an implementation that drops
    // ALL headers (rather than conditionally omitting just this one)
    // cannot pass by accident.
    assert.equal(s.calls[0].headers["accept"], "application/json");
    assert.equal("authorization" in s.calls[0].headers, false);
  } finally {
    s.restore();
  }
});

test("the forwarded secret is a fixed per-deployment value, not derived from the caller's request", async () => {
  const s = stubOrigin();
  try {
    await worker.fetch(
      new Request(`${HOST}/api/archive/search?date_from=2026-01-01&date_to=2026-01-01`, {
        headers: { authorization: "Bearer attacker-supplied-value" },
      }),
      env({ ORIGIN_SEARCH_SECRET: "the-real-secret" }),
    );
    assert.equal(s.calls[0].headers["authorization"], "Bearer the-real-secret");
  } finally {
    s.restore();
  }
});
