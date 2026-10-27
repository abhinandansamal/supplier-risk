import {
  chmodSync,
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  statSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import path from "node:path";

import { describe, expect, it, vi } from "vitest";

import {
  BoardLimitError,
  CaseBoard,
  EventConflictError,
  StoreWriteError,
  caseFilePath,
} from "../src/board.ts";
import { event, tempDir } from "./helpers.ts";

const limits = { maxEventsPerCase: 5, maxEventsTotal: 8, maxEntityRefs: 3 };
const big = { maxEventsPerCase: 1000, maxEventsTotal: 1000, maxEntityRefs: 3 };

function memoryBoard(): CaseBoard {
  return CaseBoard.open({ ...limits });
}

describe("CaseBoard.append", () => {
  it("stores a new event with seq 1", async () => {
    const board = memoryBoard();
    const result = await board.append("inv-1", event());
    expect(result).toEqual({ caseId: "inv-1", eventId: "e-0001", seq: 1, duplicate: false });
    expect(board.eventCount).toBe(1);
  });

  it("is idempotent by event id: a replay is a duplicate and writes nothing", async () => {
    const board = memoryBoard();
    await board.append("inv-1", event());
    const replay = await board.append("inv-1", event());
    expect(replay).toEqual({ caseId: "inv-1", eventId: "e-0001", seq: 1, duplicate: true });
    expect(board.timeline("inv-1")).toHaveLength(1);
    expect(board.eventCount).toBe(1);
  });

  it("rejects the same event id with a different body", async () => {
    const board = memoryBoard();
    await board.append("inv-1", event());
    await expect(board.append("inv-1", event({ summary: "changed" }))).rejects.toBeInstanceOf(
      EventConflictError,
    );
  });

  it("keeps the same event id in two cases apart", async () => {
    const board = memoryBoard();
    await board.append("inv-1", event());
    const other = await board.append("inv-2", event());
    expect(other.duplicate).toBe(false);
    expect(board.timeline("inv-2")).toHaveLength(1);
  });

  it("loses no events when two agents write concurrently", async () => {
    const board = CaseBoard.open({ maxEventsPerCase: 100, maxEventsTotal: 100, maxEntityRefs: 3 });
    const writes = Array.from({ length: 20 }, (_, i) =>
      board.append(
        "inv-1",
        event({
          event_id: `e-${String(i)}`,
          agent: i % 2 === 0 ? "financial" : "country",
          subjects: i % 2 === 0 ? { supplier: "S0001" } : { country: "DE" },
        }),
      ),
    );
    const results = await Promise.all(writes);
    expect(new Set(results.map((r) => r.seq)).size).toBe(20);
    const timeline = board.timeline("inv-1") ?? [];
    expect(timeline.map((e) => e.seq)).toEqual(Array.from({ length: 20 }, (_, i) => i + 1));
    expect(new Set(timeline.map((e) => e.agent))).toEqual(new Set(["financial", "country"]));
  });

  it("serialises a concurrent replay of the same event into one write", async () => {
    const board = memoryBoard();
    const [a, b] = await Promise.all([
      board.append("inv-1", event()),
      board.append("inv-1", event()),
    ]);
    expect([a.duplicate, b.duplicate].sort()).toEqual([false, true]);
    expect(board.eventCount).toBe(1);
  });

  it("caps events per case", async () => {
    const board = memoryBoard();
    for (let i = 0; i < 5; i++) await board.append("inv-1", event({ event_id: `e-${String(i)}` }));
    const error = await board.append("inv-1", event({ event_id: "e-x" })).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(BoardLimitError);
    expect((error as BoardLimitError).code).toBe("case_full");
    // A replay of a stored event is still answered when the case is full.
    await expect(board.append("inv-1", event({ event_id: "e-0" }))).resolves.toMatchObject({
      duplicate: true,
    });
  });

  it("caps events in the whole store", async () => {
    const board = memoryBoard();
    for (let i = 0; i < 8; i++) await board.append(`inv-${String(i)}`, event());
    const error = await board.append("inv-new", event()).catch((e: unknown) => e);
    expect((error as BoardLimitError).code).toBe("store_full");
  });
});

describe("CaseBoard reads", () => {
  it("returns the timeline in append order with the event fields", async () => {
    const board = memoryBoard();
    await board.append("inv-1", event());
    await board.append(
      "inv-1",
      event({ event_id: "e-0002", kind: "gap_found", agent: "evaluator", subjects: {} }),
    );
    const timeline = board.timeline("inv-1") ?? [];
    expect(timeline.map((e) => [e.seq, e.event_id, e.kind])).toEqual([
      [1, "e-0001", "evidence_found"],
      [2, "e-0002", "gap_found"],
    ]);
    expect(timeline[0]).toEqual({ ...event(), seq: 1 });
  });

  it("returns undefined for an unknown case", () => {
    expect(memoryBoard().timeline("nope")).toBeUndefined();
  });

  it("links subjects as graph entities with case references", async () => {
    const board = memoryBoard();
    await board.append("inv-1", event());
    await board.append(
      "inv-2",
      event({ event_id: "e-9", subjects: { supplier: "S0001", country: "DE" } }),
    );
    const supplier = board.entity("supplier:erp:S0001");
    expect(supplier?.type).toBe("supplier");
    expect(supplier?.references).toEqual([
      { case_id: "inv-1", event_id: "e-0001", kind: "evidence_found" },
      { case_id: "inv-2", event_id: "e-9", kind: "evidence_found" },
    ]);
    expect(supplier?.truncated).toBe(false);
    expect(board.entity("country:iso:DE")?.references).toHaveLength(1);
    expect(board.entity("supplier:erp:S9999")).toBeUndefined();
  });

  it("caps the references of an entity", async () => {
    const board = memoryBoard();
    for (let i = 0; i < 5; i++) await board.append("inv-1", event({ event_id: `e-${String(i)}` }));
    const supplier = board.entity("supplier:erp:S0001");
    expect(supplier?.references).toHaveLength(3);
    expect(supplier?.truncated).toBe(true);
  });

  it("returns the case entity itself", async () => {
    const board = memoryBoard();
    await board.append("inv-1", event());
    expect(board.entity("case:board:inv-1")?.references).toHaveLength(1);
  });
});

describe("CaseBoard persistence", () => {
  it("survives a restart from the data directory", async () => {
    const dir = tempDir();
    const first = CaseBoard.open({ ...limits, dataDir: dir });
    await first.append("inv-1", event());
    await first.append("inv-1", event({ event_id: "e-0002" }));
    await first.append("inv-2", event());

    const second = CaseBoard.open({ ...limits, dataDir: dir });
    expect(second.eventCount).toBe(3);
    expect(second.timeline("inv-1")?.map((e) => e.seq)).toEqual([1, 2]);
    expect(second.entity("supplier:erp:S0001")?.references).toHaveLength(3);
    await expect(second.append("inv-1", event())).resolves.toMatchObject({ duplicate: true });
    await expect(second.append("inv-1", event({ event_id: "e-0003" }))).resolves.toMatchObject({
      seq: 3,
    });
  });

  it("keeps one store file per case (SEC-074: an append rewrites only its own case)", async () => {
    const dir = tempDir();
    const board = CaseBoard.open({ ...limits, dataDir: dir });
    await board.append("inv-1", event());
    await board.append("inv-2", event());
    expect(existsSync(caseFilePath(dir, "inv-1"))).toBe(true);
    expect(existsSync(caseFilePath(dir, "inv-2"))).toBe(true);
    const before = statSync(caseFilePath(dir, "inv-1")).mtimeMs;
    await board.append("inv-2", event({ event_id: "e-0002" }));
    expect(statSync(caseFilePath(dir, "inv-1")).mtimeMs).toBe(before);
  });

  it("caps the bytes of one case (SEC-074)", async () => {
    const dir = tempDir();
    const board = CaseBoard.open({ ...big, dataDir: dir, maxCaseBytes: 20_000 });
    let error: unknown;
    for (let i = 0; i < 50 && !error; i++) {
      error = await board
        .append("inv-1", event({ event_id: `e-${String(i)}`, summary: "x".repeat(500) }))
        .then(
          () => undefined,
          (e: unknown) => e,
        );
    }
    expect((error as BoardLimitError).code).toBe("case_full");
    expect(statSync(caseFilePath(dir, "inv-1")).size).toBeLessThan(40_000);
  });

  it("caps the bytes of the whole store (SEC-074)", async () => {
    const dir = tempDir();
    const board = CaseBoard.open({ ...big, dataDir: dir, maxStoreBytes: 30_000 });
    let error: unknown;
    for (let i = 0; i < 50 && !error; i++) {
      error = await board.append(`inv-${String(i)}`, event({ summary: "x".repeat(500) })).then(
        () => undefined,
        (e: unknown) => e,
      );
    }
    expect((error as BoardLimitError).code).toBe("store_full");
    // The byte count survives a restart.
    const reopened = CaseBoard.open({ ...big, dataDir: dir, maxStoreBytes: 30_000 });
    await expect(reopened.append("inv-new", event())).rejects.toMatchObject({ code: "store_full" });
  });

  it.skipIf(process.getuid?.() === 0)(
    "a retry after a failed write is stored, not reported as a duplicate (SEC-075)",
    async () => {
      const dir = tempDir();
      const board = CaseBoard.open({ ...limits, dataDir: dir });
      await board.append("inv-1", event());
      const file = caseFilePath(dir, "inv-1");
      chmodSync(file, 0o444);
      await expect(board.append("inv-1", event({ event_id: "e-0002" }))).rejects.toBeInstanceOf(
        StoreWriteError,
      );
      chmodSync(file, 0o644);
      await expect(board.append("inv-1", event({ event_id: "e-0002" }))).resolves.toMatchObject({
        seq: 2,
        duplicate: false,
      });
      expect(board.timeline("inv-1")?.map((e) => e.seq)).toEqual([1, 2]);
      expect(board.eventCount).toBe(2);
      const reopened = CaseBoard.open({ ...limits, dataDir: dir });
      expect(reopened.timeline("inv-1")?.map((e) => [e.event_id, e.seq])).toEqual([
        ["e-0001", 1],
        ["e-0002", 2],
      ]);
    },
  );

  it("moves a corrupt case file aside, logs CRITICAL and starts that case empty", async () => {
    const dir = tempDir();
    const good = CaseBoard.open({ ...limits, dataDir: dir });
    await good.append("inv-1", event());
    await good.append("inv-2", event());
    const file = caseFilePath(dir, "inv-1");
    const content = readFileSync(file, "utf-8");
    const truncated = content.slice(0, Math.floor(content.length / 2));
    writeFileSync(file, truncated, "utf-8"); // a write cut off half way

    const logger = { fatal: vi.fn(), error: vi.fn() };
    const now = () => new Date("2026-10-27T12:34:56.789Z");
    const board = CaseBoard.open({ ...limits, dataDir: dir, logger, now });

    expect(board.timeline("inv-1")).toBeUndefined();
    expect(board.timeline("inv-2")).toHaveLength(1); // other cases are not affected
    const moved = path.join(dir, "corrupt-2026-10-27T12-34-56-789Z-inv-1.json");
    expect(readFileSync(moved, "utf-8")).toBe(truncated);
    expect(logger.fatal).toHaveBeenCalledTimes(1);
    const [fields] = logger.fatal.mock.calls[0] as [Record<string, unknown>, string];
    expect(fields).toMatchObject({
      event: "memory_store_corrupt",
      moved_to: moved,
      investigation_id: "inv-1",
      error_type: "SyntaxError",
    });
    // The case starts again and is persisted again.
    await board.append("inv-1", event());
    expect(existsSync(file)).toBe(true);
    expect(readdirSync(dir).filter((f) => f.startsWith("corrupt-"))).toHaveLength(1);
  });

  it("treats a file that parses but cannot be replayed as corrupt", () => {
    const dir = tempDir();
    mkdirSync(path.dirname(caseFilePath(dir, "inv-1")), { recursive: true });
    writeFileSync(caseFilePath(dir, "inv-1"), "null", "utf-8");
    const logger = { fatal: vi.fn(), error: vi.fn() };
    const board = CaseBoard.open({ ...limits, dataDir: dir, logger });
    expect(board.eventCount).toBe(0);
    expect(logger.fatal).toHaveBeenCalledTimes(1);
  });

  it.skipIf(process.getuid?.() === 0)(
    "fails startup on an unreadable store instead of moving it aside (SEC-076)",
    async () => {
      const dir = tempDir();
      await CaseBoard.open({ ...limits, dataDir: dir }).append("inv-1", event());
      const file = caseFilePath(dir, "inv-1");
      chmodSync(file, 0o000);
      const logger = { fatal: vi.fn(), error: vi.fn() };
      try {
        expect(() => CaseBoard.open({ ...limits, dataDir: dir, logger })).toThrow();
      } finally {
        chmodSync(file, 0o644);
      }
      expect(existsSync(file)).toBe(true);
      expect(readdirSync(dir).filter((f) => f.startsWith("corrupt-"))).toHaveLength(0);
    },
  );

  it("refuses a symlinked case file (SEC-076)", () => {
    const dir = tempDir();
    mkdirSync(path.dirname(caseFilePath(dir, "inv-1")), { recursive: true });
    const target = path.join(dir, "elsewhere.json");
    writeFileSync(target, "{}", "utf-8");
    symlinkSync(target, caseFilePath(dir, "inv-1"));
    expect(() => CaseBoard.open({ ...limits, dataDir: dir })).toThrow(/symlink/);
    expect(readFileSync(target, "utf-8")).toBe("{}");
  });

  it("ignores files in the store directory that are not case files", () => {
    const dir = tempDir();
    mkdirSync(path.dirname(caseFilePath(dir, "inv-1")), { recursive: true });
    writeFileSync(path.join(path.dirname(caseFilePath(dir, "inv-1")), "notes.txt"), "x", "utf-8");
    expect(CaseBoard.open({ ...limits, dataDir: dir }).eventCount).toBe(0);
  });
});
