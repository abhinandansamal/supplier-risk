import { pino, type DestinationStream, type Logger } from "pino";

import type { Config } from "./config.ts";

/** pino level label -> level name used by the backend logs (.claude/rules/logging.md). */
const LEVEL_NAMES: Record<string, string> = {
  trace: "DEBUG",
  debug: "DEBUG",
  info: "INFO",
  warn: "WARNING",
  error: "ERROR",
  fatal: "CRITICAL",
};

/** Header and field paths removed from every record. */
const REDACT_PATHS = [
  'req.headers["x-internal-token"]',
  "req.headers.authorization",
  'headers["x-internal-token"]',
  "headers.authorization",
  "token",
  "*.token",
];

/**
 * Creates the JSON logger: one object per line with timestamp, level, logger, message and event.
 *
 * @param level - Minimum level.
 * @param stream - Destination (stdout when omitted; tests pass a sink).
 * @returns The logger.
 */
export function createLogger(level: Config["logLevel"], stream?: DestinationStream): Logger {
  const options = {
    level,
    base: { logger: "context-service" },
    messageKey: "message",
    timestamp: () => `,"timestamp":"${new Date().toISOString()}"`,
    formatters: {
      level: (label: string) => ({ level: LEVEL_NAMES[label] ?? label.toUpperCase() }),
    },
    redact: { paths: REDACT_PATHS, censor: "[REDACTED]" },
  };
  return stream ? pino(options, stream) : pino(options);
}
