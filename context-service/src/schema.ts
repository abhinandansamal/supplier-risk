import { z } from "zod";

/** Case board event kinds (architecture §6.1, plus decision_made for approvals). */
export const EVENT_KINDS = [
  "input_refused",
  "plan_created",
  "plan_revised",
  "task_dispatched",
  "task_failed",
  "task_recovered",
  "evidence_found",
  "no_data",
  "gap_found",
  "contradiction_found",
  "verdict",
  "action_proposed",
  "claim_made",
  "escalated",
  "decision_made",
] as const;

/** Agents (and the approval step) that write to the case board. */
export const AGENTS = [
  "guard",
  "planner",
  "executor",
  "erp",
  "financial",
  "country",
  "weather",
  "evaluator",
  "recommender",
  "writer",
  "verifier",
  "escalation",
  "approval",
] as const;

/** Case ids are investigation ids; no ':' so entity ids stay unambiguous. */
export const caseIdSchema = z.string().regex(/^[A-Za-z0-9_-]{1,64}$/);

/** Entity ids the read API accepts: case and subject entities, never internal event entities. */
export const entityIdSchema = z
  .string()
  .regex(/^(case:board|supplier:erp|part:erp|site:erp|country:iso):[A-Za-z0-9_-]{1,64}$/);

/**
 * Text an agent may write: well-formed Unicode without control or format characters
 * (SEC-079: lone surrogates break readers; bidi overrides spoof the UI timeline).
 */
const safeText = (max: number) =>
  z
    .string()
    .max(max)
    .refine((value) => value.isWellFormed() && !/[\p{Cc}\p{Cf}]/u.test(value), {
      message: "control or malformed characters",
    });

/** SEC-080: integers beyond 2^53 lose precision, so two different bodies could look equal. */
const safeNumber = z
  .number()
  .refine((value) => !Number.isInteger(value) || Number.isSafeInteger(value), {
    message: "integer out of the safe range",
  });

const scalar = z.union([safeText(200), safeNumber, z.boolean(), z.null()]);

const subjectsSchema = z.strictObject({
  supplier: z
    .string()
    .regex(/^S[0-9]{4}$/)
    .optional(),
  part: z
    .string()
    .regex(/^P[0-9]{4}$/)
    .optional(),
  site: z
    .string()
    .regex(/^[A-Za-z0-9_-]{1,32}$/)
    .optional(),
  country: z
    .string()
    .regex(/^[A-Z]{2}$/)
    .optional(),
});

/** One case board event as posted by the backend. */
export const caseEventSchema = z.strictObject({
  /** Idempotency key, chosen by the writer (a UUID in the backend). */
  event_id: z.string().regex(/^[A-Za-z0-9_-]{1,64}$/),
  kind: z.enum(EVENT_KINDS),
  agent: z.enum(AGENTS),
  /** Observation time, UTC ISO 8601 with Z. */
  // Normalised, so the same instant with or without milliseconds is the same event (SEC-086).
  at: z.iso.datetime().transform((value) => new Date(value).toISOString()),
  summary: safeText(500).pipe(z.string().min(1)),
  evidence_ids: z
    .array(z.string().regex(/^[A-Za-z0-9_:.-]{1,64}$/))
    .max(50)
    .default([]),
  /** At most one subject per kind; each becomes a graph entity. */
  subjects: subjectsSchema.default({}),
  /** Small flat key-value details (no nesting). */
  data: z
    .record(z.string().regex(/^[a-z][a-z0-9_]{0,39}$/), scalar)
    .refine((value) => Object.keys(value).length <= 20, { message: "at most 20 keys" })
    .default({}),
});

/** A validated case board event. */
export type CaseEvent = z.infer<typeof caseEventSchema>;

/** Subject kinds an event can link to. */
export type SubjectKind = keyof CaseEvent["subjects"];
