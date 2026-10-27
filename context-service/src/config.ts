import { z } from "zod";

/** Runtime settings of the context service, read from the environment. */
export interface Config {
  /** Shared secret expected in the X-Internal-Token header. */
  token: string;
  host: string;
  port: number;
  /** Directory for the persisted store; undefined keeps the store in memory (tests). */
  dataDir: string | undefined;
  maxBodyBytes: number;
  maxEventsPerCase: number;
  maxEventsTotal: number;
  /** Most case references returned by GET /entities/:id. */
  maxEntityRefs: number;
  /** Largest store file of one case (SEC-074: rewritten on every append). */
  maxCaseBytes: number;
  /** Largest sum of all case files (SEC-074: all held in memory). */
  maxStoreBytes: number;
  logLevel: "debug" | "info" | "warn" | "error" | "fatal";
}

/** Raised when settings are missing or invalid; names the variables, never their values. */
export class ConfigError extends Error {
  /** The offending environment variable names. */
  readonly variables: string[];

  /**
   * @param variables - Names of the invalid environment variables.
   */
  constructor(variables: string[]) {
    super(`Invalid or missing settings: ${variables.join(", ")}`);
    this.name = "ConfigError";
    this.variables = variables;
  }
}

const PLACEHOLDERS = new Set(["replace-me", "changeme", "change-me"]);

const intInRange = (min: number, max: number, fallback: number) =>
  z.coerce.number().int().min(min).max(max).default(fallback);

const envSchema = z.object({
  // Security: visible ASCII only (no spaces/CR/LF) and long enough to resist guessing.
  CONTEXT_SERVICE_TOKEN: z
    .string()
    .regex(/^[\x21-\x7e]{32,256}$/)
    .refine((value) => !PLACEHOLDERS.has(value.toLowerCase())),
  CONTEXT_SERVICE_HOST: z.string().min(1).default("127.0.0.1"),
  CONTEXT_SERVICE_PORT: intInRange(0, 65535, 7070),
  CONTEXT_SERVICE_DATA_DIR: z.string().min(1).default("data"),
  CONTEXT_SERVICE_MAX_BODY_BYTES: intInRange(1024, 1024 * 1024, 16384),
  CONTEXT_SERVICE_MAX_EVENTS_PER_CASE: intInRange(1, 10000, 500),
  CONTEXT_SERVICE_MAX_EVENTS_TOTAL: intInRange(1, 100000, 5000),
  CONTEXT_SERVICE_MAX_ENTITY_REFS: intInRange(1, 1000, 100),
  // Sized for the 256 MB container with a 160 MB heap (measured in the security review).
  CONTEXT_SERVICE_MAX_CASE_BYTES: intInRange(64 * 1024, 8 * 1024 * 1024, 1024 * 1024),
  CONTEXT_SERVICE_MAX_STORE_BYTES: intInRange(1024 * 1024, 64 * 1024 * 1024, 16 * 1024 * 1024),
  LOG_LEVEL: z
    .string()
    .default("info")
    .transform((value) => value.toLowerCase())
    .pipe(z.enum(["debug", "info", "warn", "warning", "error", "critical", "fatal"]))
    .transform((value) => (value === "warning" ? "warn" : value === "critical" ? "fatal" : value)),
});

/**
 * Reads and validates the settings.
 *
 * @param env - The environment (usually process.env).
 * @returns The validated config.
 * @throws ConfigError when a variable is missing or invalid.
 */
export function loadConfig(env: Record<string, string | undefined>): Config {
  const parsed = envSchema.safeParse(env);
  if (!parsed.success) {
    const names = [...new Set(parsed.error.issues.map((issue) => String(issue.path[0])))];
    throw new ConfigError(names.sort());
  }
  const values = parsed.data;
  return {
    token: values.CONTEXT_SERVICE_TOKEN,
    host: values.CONTEXT_SERVICE_HOST,
    port: values.CONTEXT_SERVICE_PORT,
    dataDir: values.CONTEXT_SERVICE_DATA_DIR,
    maxBodyBytes: values.CONTEXT_SERVICE_MAX_BODY_BYTES,
    maxEventsPerCase: values.CONTEXT_SERVICE_MAX_EVENTS_PER_CASE,
    maxEventsTotal: values.CONTEXT_SERVICE_MAX_EVENTS_TOTAL,
    maxEntityRefs: values.CONTEXT_SERVICE_MAX_ENTITY_REFS,
    maxCaseBytes: values.CONTEXT_SERVICE_MAX_CASE_BYTES,
    maxStoreBytes: values.CONTEXT_SERVICE_MAX_STORE_BYTES,
    logLevel: values.LOG_LEVEL,
  };
}
