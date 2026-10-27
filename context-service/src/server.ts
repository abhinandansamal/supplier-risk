/**
 * Entry point: load settings, open the case board, listen. Exits non-zero when it cannot serve.
 */
import { buildApp } from "./app.ts";
import { CaseBoard } from "./board.ts";
import { ConfigError, loadConfig, type Config } from "./config.ts";
import { createLogger } from "./logger.ts";

async function main(): Promise<void> {
  let config: Config;
  try {
    config = loadConfig(process.env);
  } catch (err) {
    const variables = err instanceof ConfigError ? err.variables : [];
    createLogger("info").fatal(
      { event: "config_invalid", variables },
      "Invalid settings; not starting",
    );
    process.exit(1);
  }
  const logger = createLogger(config.logLevel);
  const board = CaseBoard.open({
    dataDir: config.dataDir,
    maxEventsPerCase: config.maxEventsPerCase,
    maxEventsTotal: config.maxEventsTotal,
    maxEntityRefs: config.maxEntityRefs,
    maxCaseBytes: config.maxCaseBytes,
    maxStoreBytes: config.maxStoreBytes,
    logger,
  });
  const app = buildApp({ config, board, logger });
  await app.listen({ host: config.host, port: config.port });
  logger.info(
    { event: "service_started", host: config.host, port: config.port, events: board.eventCount },
    "Context service started",
  );

  const stop = (signal: string) => {
    logger.info({ event: "service_stopping", signal }, "Context service stopping");
    app.close().then(
      () => process.exit(0),
      () => process.exit(1),
    );
  };
  process.once("SIGTERM", () => {
    stop("SIGTERM");
  });
  process.once("SIGINT", () => {
    stop("SIGINT");
  });
}

main().catch((err: unknown) => {
  createLogger("info").fatal(
    {
      event: "service_start_failed",
      error_type: err instanceof Error ? err.name : typeof err,
      err,
    },
    "Context service could not start",
  );
  process.exit(1);
});
