import type { FastifyInstance } from "fastify";
import { afterEach, describe, expect, it } from "vitest";

import { buildApp } from "../src/app.ts";
import { CaseBoard, StoreWriteError } from "../src/board.ts";
import type { Config } from "../src/config.ts";
import { LogSink, TOKEN, config, event } from "./helpers.ts";

let app: FastifyInstance | undefined;

afterEach(async () => {
  await app?.close();
  app = undefined;
});

function makeApp(overrides: Partial<Config> = {}): { app: FastifyInstance; logs: LogSink } {
  const cfg = config(overrides);
  const logs = new LogSink();
  const board = CaseBoard.open({
    maxEventsPerCase: cfg.maxEventsPerCase,
    maxEventsTotal: cfg.maxEventsTotal,
    maxEntityRefs: cfg.maxEntityRefs,
  });
  app = buildApp({ config: cfg, board, logStream: logs });
  return { app, logs };
}

const auth = { "x-internal-token": TOKEN };

function post(
  target: FastifyInstance,
  caseId: string,
  body: unknown,
  headers: Record<string, string> = auth,
) {
  return target.inject({
    method: "POST",
    url: `/cases/${caseId}/events`,
    headers: { ...headers, "content-type": "application/json" },
    payload: typeof body === "string" ? body : JSON.stringify(body),
  });
}

function expectError(res: { json: () => unknown; headers: Record<string, unknown> }, code: string) {
  const body = res.json() as Record<string, unknown>;
  expect(Object.keys(body).sort()).toEqual(["code", "message", "request_id"]);
  expect(body.code).toBe(code);
  expect(body.request_id).toBe(res.headers["x-request-id"]);
  expect(JSON.stringify(body)).not.toMatch(/at .*\.ts:\d+|stack/i);
}

describe("GET /health", () => {
  it("answers without a token and reveals nothing else", async () => {
    const res = await makeApp().app.inject({ method: "GET", url: "/health" });
    expect(res.statusCode).toBe(200);
    expect(res.json()).toEqual({ status: "ok" });
  });
});

describe("auth", () => {
  it.each([
    ["missing", {}],
    ["wrong", { "x-internal-token": "x".repeat(40) }],
    ["shorter", { "x-internal-token": "t" }],
  ])("returns 401 for a %s token on every route", async (_label, headers) => {
    const { app: a } = makeApp();
    for (const res of [
      await post(a, "inv-1", event(), headers),
      await a.inject({ method: "GET", url: "/cases/inv-1/timeline", headers }),
      await a.inject({ method: "GET", url: "/entities/supplier:erp:S0001", headers }),
    ]) {
      expect(res.statusCode).toBe(401);
      expectError(res, "unauthorized");
    }
  });

  it("checks the token before parsing the body", async () => {
    const res = await post(makeApp().app, "inv-1", "{not json", {});
    expect(res.statusCode).toBe(401);
  });
});

describe("POST /cases/:id/events", () => {
  it("stores an event (201) and replays it idempotently (200)", async () => {
    const { app: a } = makeApp();
    const first = await post(a, "inv-1", event());
    expect(first.statusCode).toBe(201);
    expect(first.json()).toEqual({
      case_id: "inv-1",
      event_id: "e-0001",
      seq: 1,
      duplicate: false,
    });
    const again = await post(a, "inv-1", event());
    expect(again.statusCode).toBe(200);
    expect(again.json()).toMatchObject({ seq: 1, duplicate: true });
  });

  it("returns 409 for the same event id with a different body", async () => {
    const { app: a } = makeApp();
    await post(a, "inv-1", event());
    const res = await post(a, "inv-1", event({ summary: "other" }));
    expect(res.statusCode).toBe(409);
    expectError(res, "event_conflict");
  });

  it.each([
    ["unknown kind", { kind: "drop_table" }],
    ["unknown agent", { agent: "attacker" }],
    ["bad event id", { event_id: "../../etc/passwd" }],
    ["non-UTC time", { at: "2026-10-27T10:00:00+02:00" }],
    ["long summary", { summary: "x".repeat(501) }],
    ["bad supplier id", { subjects: { supplier: "S1" } }],
    ["unknown subject", { subjects: { person: "x" } }],
    [
      "too many evidence ids",
      { evidence_ids: Array.from({ length: 51 }, (_, i) => `ev-${String(i)}`) },
    ],
    ["nested data", { data: { nested: { a: 1 } } }],
    [
      "too many data keys",
      { data: Object.fromEntries(Array.from({ length: 21 }, (_, i) => [`k${String(i)}`, i])) },
    ],
    ["extra field", { role: "director" }],
  ])("returns 400 for %s", async (_label, overrides) => {
    const res = await post(makeApp().app, "inv-1", { ...event(), ...overrides });
    expect(res.statusCode).toBe(400);
    expectError(res, "invalid_request");
    expect(res.json<{ message: string }>().message).not.toContain("passwd");
  });

  it("returns 400 for a bad case id", async () => {
    const res = await post(makeApp().app, "inv%3A1", event());
    expect(res.statusCode).toBe(400);
    expectError(res, "invalid_request");
  });

  it("returns 400 for malformed JSON without echoing it", async () => {
    const res = await post(makeApp().app, "inv-1", '{"event_id": "secret-ish"');
    expect(res.statusCode).toBe(400);
    expectError(res, "invalid_request");
    expect(res.body).not.toContain("secret-ish");
  });

  it("returns 413 above the body limit", async () => {
    const res = await post(makeApp({ maxBodyBytes: 1024 }).app, "inv-1", {
      ...event(),
      summary: "x".repeat(2000),
    });
    expect(res.statusCode).toBe(413);
    expectError(res, "payload_too_large");
  });

  it("returns 415 for a non-JSON body", async () => {
    const res = await makeApp().app.inject({
      method: "POST",
      url: "/cases/inv-1/events",
      headers: { ...auth, "content-type": "text/plain" },
      payload: "hello",
    });
    expect(res.statusCode).toBe(415);
    expectError(res, "unsupported_media_type");
  });

  it("returns 409 case_full / store_full at the event caps", async () => {
    const { app: a } = makeApp({ maxEventsPerCase: 1, maxEventsTotal: 2 });
    await post(a, "inv-1", event());
    const caseFull = await post(a, "inv-1", event({ event_id: "e-2" }));
    expect(caseFull.statusCode).toBe(409);
    expectError(caseFull, "case_full");
    await post(a, "inv-2", event());
    const storeFull = await post(a, "inv-3", event());
    expect(storeFull.statusCode).toBe(409);
    expectError(storeFull, "store_full");
  });
});

describe("GET /cases/:id/timeline", () => {
  it("returns the events in order", async () => {
    const { app: a } = makeApp();
    await post(a, "inv-1", event());
    await post(
      a,
      "inv-1",
      event({ event_id: "e-0002", agent: "country", subjects: { country: "DE" } }),
    );
    const res = await a.inject({ method: "GET", url: "/cases/inv-1/timeline", headers: auth });
    expect(res.statusCode).toBe(200);
    const body = res.json<{ case_id: string; count: number; events: { event_id: string }[] }>();
    expect(body.case_id).toBe("inv-1");
    expect(body.count).toBe(2);
    expect(body.events.map((e) => e.event_id)).toEqual(["e-0001", "e-0002"]);
  });

  it("returns 404 for an unknown case", async () => {
    const res = await makeApp().app.inject({
      method: "GET",
      url: "/cases/nope/timeline",
      headers: auth,
    });
    expect(res.statusCode).toBe(404);
    expectError(res, "case_not_found");
  });
});

describe("GET /entities/:id", () => {
  it("returns the entity with its case references", async () => {
    const { app: a } = makeApp();
    await post(a, "inv-1", event());
    const res = await a.inject({
      method: "GET",
      url: "/entities/supplier:erp:S0001",
      headers: auth,
    });
    expect(res.statusCode).toBe(200);
    expect(res.json()).toMatchObject({
      id: "supplier:erp:S0001",
      type: "supplier",
      references: [{ case_id: "inv-1", event_id: "e-0001", kind: "evidence_found" }],
      truncated: false,
    });
  });

  it.each(["case_event:inv-1:e-0001", "supplier:erp:../x", "x".repeat(200)])(
    "returns 400 for entity id %s",
    async (id) => {
      const res = await makeApp().app.inject({
        method: "GET",
        url: `/entities/${encodeURIComponent(id)}`,
        headers: auth,
      });
      expect(res.statusCode).toBe(400);
      expectError(res, "invalid_request");
    },
  );

  it("returns 404 for an unknown entity", async () => {
    const res = await makeApp().app.inject({
      method: "GET",
      url: "/entities/supplier:erp:S9999",
      headers: auth,
    });
    expect(res.statusCode).toBe(404);
    expectError(res, "entity_not_found");
  });
});

describe("errors, request ids and logs", () => {
  it("returns 404 not_found for an unknown route", async () => {
    const res = await makeApp().app.inject({ method: "GET", url: "/admin", headers: auth });
    expect(res.statusCode).toBe(404);
    expectError(res, "not_found");
  });

  it("echoes a valid X-Request-ID and replaces an invalid one", async () => {
    const { app: a } = makeApp();
    const good = await a.inject({
      method: "GET",
      url: "/health",
      headers: { "x-request-id": "req-123" },
    });
    expect(good.headers["x-request-id"]).toBe("req-123");
    const bad = await a.inject({
      method: "GET",
      url: "/health",
      headers: { "x-request-id": "a\nb c" },
    });
    expect(bad.headers["x-request-id"]).toMatch(/^[0-9a-f-]{36}$/);
  });

  it("writes JSON logs with event, request_id and investigation_id and never the token", async () => {
    const { app: a, logs } = makeApp();
    await post(a, "inv-1", event(), { ...auth, "x-request-id": "req-42" });
    await post(a, "inv-1", event(), { "x-internal-token": "wrong-token-value-123456789012345" });
    const records = logs.records();
    const appended = records.find((r) => r.event === "event_appended");
    expect(appended).toMatchObject({
      level: "INFO",
      logger: "context-service",
      request_id: "req-42",
      investigation_id: "inv-1",
      event_id: "e-0001",
      seq: 1,
    });
    expect(typeof appended?.timestamp).toBe("string");
    expect(typeof appended?.message).toBe("string");
    const done = records.find((r) => r.event === "http_request_done" && r.status_code === 201);
    expect(done).toMatchObject({ method: "POST", request_id: "req-42" });
    expect(typeof done?.duration_ms).toBe("number");
    expect(records.some((r) => r.event === "auth_failed" && r.level === "WARNING")).toBe(true);
    const all = logs.lines.join("\n");
    expect(all).not.toContain(TOKEN);
    expect(all).not.toContain("wrong-token-value");
    expect(all).not.toContain("Revenue fell"); // no event contents in logs
  });
});

describe("hardening (security review)", () => {
  it.each([
    ["a lone surrogate in summary", { summary: "bad \ud800 text" }],
    ["a bidi override in summary", { summary: "invoice \u202Efdp.exe" }],
    ["a control character in a data value", { data: { note: "a\u0007b" } }],
    ["an unsafe integer", { data: { open_po_value: Number("12345678901234567890") } }],
  ])("returns 400 for %s (SEC-079, SEC-080)", async (_label, overrides) => {
    const res = await post(makeApp().app, "inv-1", { ...event(), ...overrides });
    expect(res.statusCode).toBe(400);
    expectError(res, "invalid_request");
  });

  it("treats the same instant with and without milliseconds as the same event (SEC-086)", async () => {
    const { app: a } = makeApp();
    await post(a, "inv-1", event({ at: "2026-10-27T10:00:00Z" }));
    const again = await post(a, "inv-1", event({ at: "2026-10-27T10:00:00.000Z" }));
    expect(again.statusCode).toBe(200);
  });

  it("answers HEAD /health without a token (SEC-086)", async () => {
    const res = await makeApp().app.inject({ method: "HEAD", url: "/health" });
    expect(res.statusCode).toBe(200);
  });

  it("sets connection, request and keep-alive timeouts (SEC-083)", async () => {
    const { app: a } = makeApp();
    await a.ready();
    expect(a.server.requestTimeout).toBeGreaterThan(0);
    expect(a.server.requestTimeout).toBeLessThanOrEqual(15_000);
    expect(a.server.timeout).toBeGreaterThan(0);
    expect(a.server.keepAliveTimeout).toBeLessThanOrEqual(5_000);
  });

  it("logs a bounded number of lines for a flood of unauthenticated requests (SEC-083)", async () => {
    const { app: a, logs } = makeApp();
    for (let i = 0; i < 50; i++) await a.inject({ method: "GET", url: "/cases/x/timeline" });
    expect(logs.lines.length).toBeLessThanOrEqual(12);
  });

  it("returns 503 store_unavailable when a write fails (SEC-075)", async () => {
    const cfg = config();
    const board = {
      append: () => Promise.reject(new StoreWriteError()),
    } as unknown as CaseBoard;
    app = buildApp({ config: cfg, board, logStream: new LogSink() });
    const res = await post(app, "inv-1", event());
    expect(res.statusCode).toBe(503);
    expectError(res, "store_unavailable");
  });
});
