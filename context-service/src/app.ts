import { createHash, randomUUID, timingSafeEqual } from "node:crypto";

import Fastify, {
  LogController,
  type FastifyBaseLogger,
  type FastifyError,
  type FastifyInstance,
  type FastifyReply,
} from "fastify";
import type { DestinationStream, Logger } from "pino";

import { BoardLimitError, EventConflictError, StoreWriteError, type CaseBoard } from "./board.ts";
import type { Config } from "./config.ts";
import { createLogger } from "./logger.ts";
import { caseEventSchema, caseIdSchema, entityIdSchema } from "./schema.ts";

/** Options for {@link buildApp}. */
export interface AppOptions {
  config: Config;
  board: CaseBoard;
  /** Shared logger (the server passes one); otherwise one is created on logStream. */
  logger?: Logger;
  logStream?: DestinationStream;
}

const REQUEST_ID = /^[A-Za-z0-9_-]{1,64}$/;
/** SEC-083: at most this many auth_failed lines per window; the rest are counted. */
const AUTH_LOG_LIMIT = 5;
const AUTH_LOG_WINDOW_MS = 60_000;
const SCHEMA_FIELDS = new Set(Object.keys(caseEventSchema.shape));

function digest(value: string): Buffer {
  return createHash("sha256").update(value, "utf-8").digest();
}

/** Constant-time token check (hashing first gives equal lengths). */
function tokenMatches(given: string | string[] | undefined, expected: Buffer): boolean {
  return typeof given === "string" && timingSafeEqual(digest(given), expected);
}

function sendError(
  reply: FastifyReply,
  status: number,
  code: string,
  message: string,
): FastifyReply {
  return reply.code(status).send({ code, message, request_id: reply.request.id });
}

/** Names of the top-level schema fields that failed (never user-supplied values or keys). */
function invalidFields(issues: { path: PropertyKey[] }[]): string {
  const fields = [
    ...new Set(issues.map((i) => String(i.path[0] ?? "")).filter((f) => SCHEMA_FIELDS.has(f))),
  ];
  return fields.length ? `Invalid fields: ${fields.sort().join(", ")}.` : "Invalid request body.";
}

/**
 * Builds the HTTP API of the case board.
 *
 * Every route except GET /health needs the X-Internal-Token header. Errors use
 * `{code, message, request_id}`; details stay in the logs.
 *
 * @param opts - Config, board and logging.
 * @returns The Fastify app (not listening yet).
 */
export function buildApp(opts: AppOptions): FastifyInstance {
  const { config, board } = opts;
  const expectedToken = digest(config.token);
  const logger: FastifyBaseLogger = opts.logger ?? createLogger(config.logLevel, opts.logStream);
  const app = Fastify({
    loggerInstance: logger,
    bodyLimit: config.maxBodyBytes,
    // SEC-083: slow or idle clients are cut off.
    connectionTimeout: 10_000,
    requestTimeout: 10_000,
    keepAliveTimeout: 5_000,
    // Longer ids reach our validator and get a 400, not a router 404.
    routerOptions: { maxParamLength: 300 },
    // Our onResponse hook logs one http_request_done line instead of Fastify's two.
    logController: new LogController({
      disableRequestLogging: true,
      requestIdLogLabel: "request_id",
    }),
    genReqId: (req) => {
      const header = req.headers["x-request-id"];
      return typeof header === "string" && REQUEST_ID.test(header) ? header : randomUUID();
    },
  });
  // Only JSON bodies are accepted.
  app.removeContentTypeParser(["text/plain"]);

  const authLog = { windowStart: 0, logged: 0, suppressed: 0 };

  app.addHook("onRequest", async (request, reply) => {
    void reply.header("x-request-id", request.id);
    if ((request.method === "GET" || request.method === "HEAD") && request.url === "/health") {
      return;
    }
    // Security: checked before the body is parsed.
    if (!tokenMatches(request.headers["x-internal-token"], expectedToken)) {
      const now = Date.now();
      if (now - authLog.windowStart >= AUTH_LOG_WINDOW_MS) {
        if (authLog.suppressed > 0) {
          request.log.warn(
            { event: "auth_failed_suppressed", count: authLog.suppressed },
            "Further requests without a valid token were not logged",
          );
        }
        Object.assign(authLog, { windowStart: now, logged: 0, suppressed: 0 });
      }
      if (authLog.logged < AUTH_LOG_LIMIT) {
        authLog.logged += 1;
        request.log.warn(
          {
            event: "auth_failed",
            method: request.method,
            path: request.routeOptions.url ?? "unmatched",
          },
          "Request without a valid internal token",
        );
      } else {
        authLog.suppressed += 1;
      }
      await sendError(reply, 401, "unauthorized", "Missing or invalid internal token.");
    }
  });

  app.addHook("onResponse", async (request, reply) => {
    // 401s are covered by the throttled auth_failed lines (SEC-083).
    if (reply.statusCode === 401) return;
    request.log.info(
      {
        event: "http_request_done",
        method: request.method,
        path: request.routeOptions.url ?? "unmatched",
        status_code: reply.statusCode,
        duration_ms: Math.round(reply.elapsedTime * 10) / 10,
      },
      "HTTP request done",
    );
  });

  app.setErrorHandler(async (error: FastifyError, request, reply) => {
    switch (error.code) {
      case "FST_ERR_CTP_BODY_TOO_LARGE":
        return sendError(reply, 413, "payload_too_large", "Request body is too large.");
      case "FST_ERR_CTP_INVALID_MEDIA_TYPE":
        return sendError(reply, 415, "unsupported_media_type", "Send application/json.");
      default:
        break;
    }
    const status = error.statusCode ?? 500;
    if (status < 500) {
      request.log.warn({ event: "request_rejected", error_type: error.code }, "Request rejected");
      return sendError(reply, 400, "invalid_request", "Request body is not valid JSON.");
    }
    request.log.error({ event: "request_failed", err: error }, "Request failed");
    return sendError(reply, 500, "internal_error", "Internal error.");
  });

  app.setNotFoundHandler(async (_request, reply) =>
    sendError(reply, 404, "not_found", "Not found."),
  );

  app.get("/health", () => ({ status: "ok" }));

  app.post<{ Params: { id: string } }>("/cases/:id/events", async (request, reply) => {
    const caseId = caseIdSchema.safeParse(request.params.id);
    if (!caseId.success) return sendError(reply, 400, "invalid_request", "Invalid case id.");
    const log = request.log.child({ investigation_id: caseId.data });
    const parsed = caseEventSchema.safeParse(request.body);
    if (!parsed.success) {
      log.warn({ event: "event_rejected", error_type: "validation" }, "Invalid case event");
      return sendError(reply, 400, "invalid_request", invalidFields(parsed.error.issues));
    }
    const started = performance.now();
    try {
      const result = await board.append(caseId.data, parsed.data);
      log.info(
        {
          event: "event_appended",
          event_id: result.eventId,
          kind: parsed.data.kind,
          agent: parsed.data.agent,
          seq: result.seq,
          duplicate: result.duplicate,
          duration_ms: Math.round((performance.now() - started) * 10) / 10,
        },
        result.duplicate ? "Case event replayed (duplicate)" : "Case event appended",
      );
      return await reply.code(result.duplicate ? 200 : 201).send({
        case_id: result.caseId,
        event_id: result.eventId,
        seq: result.seq,
        duplicate: result.duplicate,
      });
    } catch (err) {
      if (err instanceof EventConflictError) {
        log.warn({ event: "event_conflict", event_id: parsed.data.event_id }, "Event id reused");
        return sendError(reply, 409, "event_conflict", err.message);
      }
      if (err instanceof StoreWriteError) {
        return sendError(reply, 503, "store_unavailable", err.message);
      }
      if (err instanceof BoardLimitError) {
        log.warn(
          { event: "board_limit_reached", error_type: err.code },
          "Case board limit reached",
        );
        return sendError(reply, 409, err.code, err.message);
      }
      throw err;
    }
  });

  app.get<{ Params: { id: string } }>("/cases/:id/timeline", async (request, reply) => {
    const caseId = caseIdSchema.safeParse(request.params.id);
    if (!caseId.success) return sendError(reply, 400, "invalid_request", "Invalid case id.");
    const events = board.timeline(caseId.data);
    if (!events) return sendError(reply, 404, "case_not_found", "Unknown case.");
    return { case_id: caseId.data, count: events.length, events };
  });

  app.get<{ Params: { id: string } }>("/entities/:id", async (request, reply) => {
    const entityId = entityIdSchema.safeParse(request.params.id);
    if (!entityId.success) return sendError(reply, 400, "invalid_request", "Invalid entity id.");
    const entity = board.entity(entityId.data);
    if (!entity) return sendError(reply, 404, "entity_not_found", "Unknown entity.");
    return entity;
  });

  return app;
}
