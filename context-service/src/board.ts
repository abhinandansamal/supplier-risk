import { existsSync, lstatSync, readdirSync, renameSync } from "node:fs";
import path from "node:path";

import { WorldModel, canonicalJson, sha256Hex, type Entity, type Provenance } from "./engine.ts";
import type { CaseEvent, SubjectKind } from "./schema.ts";

/** Engine namespace prefix; each case is its own namespace, so its own file. */
const NAMESPACE_PREFIX = "case-";
const CASE_FILE = /^case-([A-Za-z0-9_-]{1,64})\.json$/;

/** Graph type and external-key system per subject kind (ids: `<type>:<system>:<value>`). */
const SUBJECT_KEYS: Record<SubjectKind, { type: string; system: string }> = {
  supplier: { type: "supplier", system: "erp" },
  part: { type: "part", system: "erp" },
  site: { type: "site", system: "erp" },
  country: { type: "country", system: "iso" },
};

/**
 * Path of a case's engine store inside a data directory (the reference writes `.world/<ns>.json`).
 *
 * @param dataDir - The data directory.
 * @param caseId - The (validated) case id.
 * @returns The store file path.
 */
export function caseFilePath(dataDir: string, caseId: string): string {
  return path.join(dataDir, ".world", `${NAMESPACE_PREFIX}${caseId}.json`);
}

/** A stored event with its position in the case. */
export type StoredEvent = CaseEvent & { seq: number };

/** Outcome of an append. */
export interface AppendResult {
  caseId: string;
  eventId: string;
  seq: number;
  /** True when the event id was already stored with the same body (nothing written). */
  duplicate: boolean;
}

/** A case that mentions an entity. */
export interface EntityReference {
  case_id: string;
  event_id: string;
  kind: string;
}

/** Graph view of one entity: the record plus the case events that point at it. */
export interface EntityView {
  id: string;
  type: string;
  attrs: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  references: EntityReference[];
  truncated: boolean;
}

/** Minimal logger the board needs (pino-compatible). */
export interface BoardLogger {
  fatal(fields: Record<string, unknown>, message: string): void;
  error(fields: Record<string, unknown>, message: string): void;
}

/** Options for {@link CaseBoard.open}. */
export interface BoardOptions {
  /** Persist under this directory; undefined keeps the store in memory. */
  dataDir?: string | undefined;
  maxEventsPerCase: number;
  maxEventsTotal: number;
  maxEntityRefs: number;
  /** Largest store file of one case (the engine rewrites it on every append). */
  maxCaseBytes?: number;
  /** Largest sum of all case files (the whole store is held in memory). */
  maxStoreBytes?: number;
  logger?: BoardLogger;
  /** Clock for the corrupt-file name and operator actions (tests inject one). */
  now?: () => Date;
}

/** Raised when an event id is reused with a different body. */
export class EventConflictError extends Error {
  constructor() {
    super("An event with this id already exists with a different body.");
    this.name = "EventConflictError";
  }
}

/** Raised when a case or the whole store is full (events or bytes). */
export class BoardLimitError extends Error {
  /** Which cap was reached. */
  readonly code: "case_full" | "store_full";

  /**
   * @param code - The cap that was reached.
   */
  constructor(code: "case_full" | "store_full") {
    super(
      code === "case_full"
        ? "The case holds the maximum number of events."
        : "The case board store is full.",
    );
    this.name = "BoardLimitError";
    this.code = code;
  }
}

/** Raised when persisting an event failed; the case was reloaded from disk. */
export class StoreWriteError extends Error {
  constructor() {
    super("The event could not be stored.");
    this.name = "StoreWriteError";
  }
}

interface EventAttrs {
  case_id: string;
  event_id: string;
  seq: number;
  checksum: string;
  event: CaseEvent;
}

/** One case: its engine instance, event entity ids in seq order, and its file size. */
interface CaseStore {
  world: WorldModel;
  ids: string[];
  bytes: number;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** True when the path exists and is a symlink (dangling ones included). */
function isSymlink(file: string): boolean {
  try {
    return lstatSync(file).isSymbolicLink();
  } catch {
    return false;
  }
}

/** File-system errors (EACCES, EISDIR, ...) carry a code; parse and replay errors do not. */
function isFsError(err: unknown): boolean {
  return err instanceof Error && typeof (err as NodeJS.ErrnoException).code === "string";
}

/** Reads our attrs back from a case_event entity; throws if the store holds something else. */
function eventAttrs(entity: Entity): EventAttrs {
  const { case_id, event_id, seq, checksum, event } = entity.attrs;
  if (
    typeof case_id !== "string" ||
    typeof event_id !== "string" ||
    typeof seq !== "number" ||
    typeof checksum !== "string" ||
    !isRecord(event)
  ) {
    throw new Error(`case board: malformed event entity ${entity.id}`);
  }
  return { case_id, event_id, seq, checksum, event: event as CaseEvent };
}

/**
 * Case boards (one per investigation) on top of the vendored World Model.
 *
 * Each case is its own engine namespace and file (SEC-074: the engine rewrites its whole file on
 * every mutation, so a shared file made every append cost the size of all cases). In a case, the
 * case is the entity `case:board:<case_id>`; each event is the entity
 * `case_event:<case_id>:<event_id>` with a `part-of` edge to its case and one `about-<kind>` edge
 * per subject. Writes run one at a time, so the duplicate check and the write cannot race.
 */
export class CaseBoard {
  private readonly opts: BoardOptions;
  private readonly now: () => Date;
  private readonly cases = new Map<string, CaseStore>();
  private lock: Promise<unknown> = Promise.resolve();

  private constructor(opts: BoardOptions) {
    this.opts = opts;
    this.now = opts.now ?? (() => new Date());
  }

  /**
   * Opens the board and loads every case file. A case file that cannot be parsed or replayed is
   * moved to `<dataDir>/corrupt-<timestamp>-<case>.json`, logged as CRITICAL
   * (memory_store_corrupt), and that case starts empty. File-system errors are not corruption:
   * they are thrown, so a store that is merely unreadable is never moved aside.
   *
   * @param opts - Storage location, limits, logger and clock.
   * @returns The opened board.
   * @throws Error when the data directory or a case file cannot be read, or a case file is a symlink.
   */
  static open(opts: BoardOptions): CaseBoard {
    const board = new CaseBoard(opts);
    const dataDir = opts.dataDir;
    if (dataDir === undefined) return board;
    const worldDir = path.dirname(caseFilePath(dataDir, "x"));
    if (!existsSync(worldDir)) return board;
    for (const name of readdirSync(worldDir).sort()) {
      const match = CASE_FILE.exec(name);
      if (!match?.[1]) continue;
      const store = board.loadCase(match[1]);
      if (store) board.cases.set(match[1], store);
    }
    return board;
  }

  /** Number of events in all cases. */
  get eventCount(): number {
    let total = 0;
    for (const store of this.cases.values()) total += store.ids.length;
    return total;
  }

  private get storeBytes(): number {
    let total = 0;
    for (const store of this.cases.values()) total += store.bytes;
    return total;
  }

  private newWorld(caseId: string): WorldModel {
    const namespace = `${NAMESPACE_PREFIX}${caseId}`;
    return this.opts.dataDir === undefined
      ? new WorldModel({ storage: "memory", namespace, clock: this.now })
      : new WorldModel({
          storage: "local",
          namespace,
          directory: this.opts.dataDir,
          clock: this.now,
        });
  }

  private fileBytes(caseId: string): number {
    if (this.opts.dataDir === undefined) return 0;
    const file = caseFilePath(this.opts.dataDir, caseId);
    return existsSync(file) ? lstatSync(file).size : 0;
  }

  /** Builds a case from its engine state; throws if the state is not a case board. */
  private indexCase(caseId: string, world: WorldModel): CaseStore {
    const events = world
      .view("graph")
      .query({ type: "case_event" })
      .map((entity) => eventAttrs(entity))
      .sort((a, b) => a.seq - b.seq);
    if (events.some((e, i) => e.case_id !== caseId || e.seq !== i + 1)) {
      throw new Error(`case board: inconsistent events in case ${caseId}`);
    }
    return {
      world,
      ids: events.map((e) => eventEntityId(caseId, e.event_id)),
      bytes: this.fileBytes(caseId),
    };
  }

  /** Loads one case from disk, moving a corrupt file aside (returns undefined then). */
  private loadCase(caseId: string): CaseStore | undefined {
    const dataDir = this.opts.dataDir;
    if (dataDir === undefined) return undefined;
    const file = caseFilePath(dataDir, caseId);
    // Security: never follow a symlink out of the data directory.
    if (isSymlink(file)) {
      throw new Error(`case board: refusing symlink store file for case ${caseId}`);
    }
    try {
      return this.indexCase(caseId, this.newWorld(caseId));
    } catch (err) {
      if (isFsError(err)) throw err;
      const errorType = err instanceof Error ? err.name : typeof err;
      const stamp = this.now().toISOString().replace(/[:.]/g, "-");
      const movedTo = path.join(dataDir, `corrupt-${stamp}-${caseId}.json`);
      const fields = {
        event: "memory_store_corrupt",
        investigation_id: caseId,
        moved_to: movedTo,
        error_type: errorType,
      };
      try {
        renameSync(file, movedTo);
      } catch (renameErr) {
        this.opts.logger?.fatal(
          { ...fields, moved_to: undefined, rename_error: (renameErr as Error).name },
          "Case store is corrupt and could not be moved aside",
        );
        throw renameErr;
      }
      this.opts.logger?.fatal(
        fields,
        "Case store could not be read; moved aside, case starts empty",
      );
      return undefined;
    }
  }

  /**
   * Appends an event to a case (append-only, idempotent by event id).
   *
   * @param caseId - The investigation id.
   * @param event - The validated event.
   * @returns The event's seq and whether it was a duplicate.
   * @throws EventConflictError when the id exists with a different body.
   * @throws BoardLimitError when the case or the store is full.
   * @throws StoreWriteError when the event could not be persisted.
   */
  append(caseId: string, event: CaseEvent): Promise<AppendResult> {
    const next = this.lock.then(() => this.appendNow(caseId, event));
    this.lock = next.catch(() => undefined);
    return next;
  }

  private async appendNow(caseId: string, event: CaseEvent): Promise<AppendResult> {
    const id = eventEntityId(caseId, event.event_id);
    const checksum = sha256Hex(canonicalJson(event));
    const existingCase = this.cases.get(caseId);
    const existing = existingCase?.world.view("graph").entity(id);
    if (existing) {
      const attrs = eventAttrs(existing);
      if (attrs.checksum !== checksum) throw new EventConflictError();
      return { caseId, eventId: event.event_id, seq: attrs.seq, duplicate: true };
    }
    this.checkLimits(existingCase);

    const store = existingCase ?? { world: this.newWorld(caseId), ids: [], bytes: 0 };
    const seq = store.ids.length + 1;
    try {
      await this.writeEvent(store.world, caseId, event, { checksum, seq });
    } catch (err) {
      // The engine changes memory before it writes the file; rebuild from disk so the index,
      // the caps and the seq numbers match what is really stored (SEC-075).
      this.cases.delete(caseId);
      const reloaded = this.loadCase(caseId);
      if (reloaded && reloaded.ids.length > 0) this.cases.set(caseId, reloaded);
      this.opts.logger?.error(
        {
          event: "store_write_failed",
          investigation_id: caseId,
          error_type: err instanceof Error ? err.name : typeof err,
        },
        "Case event could not be stored; case reloaded from disk",
      );
      throw new StoreWriteError();
    }
    store.ids.push(id);
    store.bytes = this.fileBytes(caseId);
    this.cases.set(caseId, store);
    return { caseId, eventId: event.event_id, seq, duplicate: false };
  }

  private checkLimits(store: CaseStore | undefined): void {
    const { maxEventsPerCase, maxEventsTotal, maxCaseBytes, maxStoreBytes } = this.opts;
    if (store && store.ids.length >= maxEventsPerCase) throw new BoardLimitError("case_full");
    if (store && maxCaseBytes !== undefined && store.bytes >= maxCaseBytes) {
      throw new BoardLimitError("case_full");
    }
    if (this.eventCount >= maxEventsTotal) throw new BoardLimitError("store_full");
    if (maxStoreBytes !== undefined && this.storeBytes >= maxStoreBytes) {
      throw new BoardLimitError("store_full");
    }
  }

  private async writeEvent(
    world: WorldModel,
    caseId: string,
    event: CaseEvent,
    meta: { checksum: string; seq: number },
  ): Promise<void> {
    const graph = world.view("graph");
    const id = eventEntityId(caseId, event.event_id);
    const source: Provenance = { kind: "agent", by: event.agent, at: event.at, runId: caseId };
    const caseEntity = caseEntityId(caseId);
    if (!graph.entity(caseEntity)) {
      await world.upsertEntity({
        type: "case",
        externalKey: { system: "board", id: caseId },
        attrs: { case_id: caseId },
        source,
      });
    }
    const attrs: EventAttrs = { case_id: caseId, event_id: event.event_id, ...meta, event };
    await world.upsertEntity({
      type: "case_event",
      externalKey: { system: caseId, id: event.event_id },
      attrs: { ...attrs },
      source,
    });
    await world.relate({ from: id, type: "part-of", to: caseEntity, source });
    for (const kind of Object.keys(SUBJECT_KEYS) as SubjectKind[]) {
      const value = event.subjects[kind];
      if (value === undefined) continue;
      const key = SUBJECT_KEYS[kind];
      const subjectId = `${key.type}:${key.system}:${value}`;
      if (!graph.entity(subjectId)) {
        await world.upsertEntity({
          type: key.type,
          externalKey: { system: key.system, id: value },
          attrs: {},
          source,
        });
      }
      // One edge type per kind: the engine closes an open edge with the same (from, type).
      await world.relate({ from: id, type: `about-${kind}`, to: subjectId, source });
    }
  }

  /**
   * The events of a case in append order.
   *
   * @param caseId - The investigation id.
   * @returns The events, or undefined for an unknown case.
   */
  timeline(caseId: string): StoredEvent[] | undefined {
    const store = this.cases.get(caseId);
    if (!store) return undefined;
    const graph = store.world.view("graph");
    return store.ids.flatMap((id) => {
      const entity = graph.entity(id);
      if (!entity) return [];
      const attrs = eventAttrs(entity);
      return [{ ...attrs.event, seq: attrs.seq }];
    });
  }

  /**
   * Graph view of a case or subject entity with the (capped) case events that point at it,
   * across all cases.
   *
   * @param id - Entity id, e.g. `supplier:erp:S0001`.
   * @returns The entity view, or undefined when unknown.
   */
  entity(id: string): EntityView | undefined {
    let found: Entity | undefined;
    const references: (EntityReference & { seq: number })[] = [];
    for (const store of this.cases.values()) {
      const graph = store.world.view("graph");
      const entity = graph.entity(id);
      if (!entity || entity.type === "case_event") continue;
      if (!found || entity.createdAt < found.createdAt) found = entity;
      for (const ref of graph.incoming(entity.id)) {
        if (ref.type !== "case_event") continue;
        const a = eventAttrs(ref);
        references.push({
          case_id: a.case_id,
          event_id: a.event_id,
          kind: a.event.kind,
          seq: a.seq,
        });
      }
    }
    if (!found) return undefined;
    references.sort((a, b) => a.case_id.localeCompare(b.case_id) || a.seq - b.seq);
    return {
      id: found.id,
      type: found.type,
      attrs: found.attrs,
      created_at: found.createdAt,
      updated_at: found.updatedAt,
      references: references
        .slice(0, this.opts.maxEntityRefs)
        .map(({ case_id, event_id, kind }) => ({ case_id, event_id, kind })),
      truncated: references.length > this.opts.maxEntityRefs,
    };
  }
}

function caseEntityId(caseId: string): string {
  return `case:board:${caseId}`;
}

function eventEntityId(caseId: string, eventId: string): string {
  return `case_event:${caseId}:${eventId}`;
}
