import { describe, expect, it } from "vitest";

import { ConfigError, loadConfig } from "../src/config.ts";
import { TOKEN } from "./helpers.ts";

describe("loadConfig", () => {
  it("uses safe defaults", () => {
    const cfg = loadConfig({ CONTEXT_SERVICE_TOKEN: TOKEN });
    expect(cfg.host).toBe("127.0.0.1");
    expect(cfg.port).toBe(7070);
    expect(cfg.maxBodyBytes).toBe(16384);
    expect(cfg.maxEventsPerCase).toBe(500);
    expect(cfg.maxEventsTotal).toBe(5000);
    expect(cfg.dataDir).toBe("data");
    expect(cfg.maxCaseBytes).toBe(1024 * 1024);
    expect(cfg.maxStoreBytes).toBe(16 * 1024 * 1024);
  });

  it("reads overrides", () => {
    const cfg = loadConfig({
      CONTEXT_SERVICE_TOKEN: TOKEN,
      CONTEXT_SERVICE_HOST: "0.0.0.0",
      CONTEXT_SERVICE_PORT: "8080",
      CONTEXT_SERVICE_DATA_DIR: "/data",
      CONTEXT_SERVICE_MAX_EVENTS_PER_CASE: "10",
    });
    expect(cfg).toMatchObject({
      host: "0.0.0.0",
      port: 8080,
      dataDir: "/data",
      maxEventsPerCase: 10,
    });
  });

  it.each([
    ["missing", undefined],
    ["too short", "short-token"],
    ["placeholder", "replace-me"],
    ["with spaces", `${"a".repeat(20)} ${"b".repeat(20)}`],
  ])("refuses a %s token and never echoes the value", (_label, token) => {
    const env = token === undefined ? {} : { CONTEXT_SERVICE_TOKEN: token };
    let error: unknown;
    try {
      loadConfig(env);
    } catch (err) {
      error = err;
    }
    expect(error).toBeInstanceOf(ConfigError);
    expect((error as ConfigError).variables).toEqual(["CONTEXT_SERVICE_TOKEN"]);
    if (token) expect((error as Error).message).not.toContain(token);
  });

  it("refuses out-of-range limits", () => {
    expect(() =>
      loadConfig({ CONTEXT_SERVICE_TOKEN: TOKEN, CONTEXT_SERVICE_MAX_BODY_BYTES: "999999999" }),
    ).toThrow(ConfigError);
    expect(() =>
      loadConfig({ CONTEXT_SERVICE_TOKEN: TOKEN, CONTEXT_SERVICE_PORT: "not-a-port" }),
    ).toThrow(ConfigError);
  });
});
