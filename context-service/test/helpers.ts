import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { Writable } from "node:stream";

import type { Config } from "../src/config.ts";
import type { CaseEvent } from "../src/schema.ts";

export const TOKEN = "t".repeat(40);

/** A valid event body; override fields per test. */
export function event(overrides: Partial<CaseEvent> = {}): CaseEvent {
  return {
    event_id: "e-0001",
    kind: "evidence_found",
    agent: "financial",
    at: "2026-10-27T10:00:00.000Z",
    summary: "Revenue fell 12 % year on year.",
    evidence_ids: ["ev-fin-1"],
    subjects: { supplier: "S0001" },
    data: { revenue_change_pct: -12 },
    ...overrides,
  };
}

/** Test config with small limits. */
export function config(overrides: Partial<Config> = {}): Config {
  return {
    token: TOKEN,
    host: "127.0.0.1",
    port: 0,
    dataDir: undefined,
    maxBodyBytes: 4096,
    maxEventsPerCase: 5,
    maxEventsTotal: 8,
    maxEntityRefs: 3,
    maxCaseBytes: 2 * 1024 * 1024,
    maxStoreBytes: 16 * 1024 * 1024,
    logLevel: "info",
    ...overrides,
  };
}

/** A fresh temporary data directory. */
export function tempDir(): string {
  return mkdtempSync(path.join(tmpdir(), "context-service-"));
}

/** Collects pino JSON lines written to it. */
export class LogSink extends Writable {
  lines: string[] = [];

  override _write(chunk: Buffer, _enc: string, done: () => void): void {
    this.lines.push(...chunk.toString("utf-8").split("\n").filter(Boolean));
    done();
  }

  records(): Record<string, unknown>[] {
    return this.lines.map((line) => JSON.parse(line) as Record<string, unknown>);
  }
}
