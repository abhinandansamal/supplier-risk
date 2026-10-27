# Supplier Risk Investigator

An explainable multi-agent assistant for procurement teams: it investigates why a supplier is risky,
answers with numbered claims backed by evidence from Zetaris, and recommends actions that a human approves.

Open Agent Hackathon 2026, track: "The Agent That Can Explain Why". **Status: in development.**

## Sponsor technology
- **Zetaris**: the single data layer (synthetic ERP + SEC EDGAR + Eurostat + industry codes), role-scoped views.
- **Meterless Nightshift**: the primary AI provider (OpenAI-compatible Runtime).
- **Meterless engines**: World Model (shared case board for the agents), vendored unchanged and served by
  `context-service/` (conformance suite in CI). H-MEM was cut from the scope.
- **NVIDIA**: Nemotron 3.5 Lightning, Nemotron 3 Ultra, Nemotron 3.5 Bolt and Nemotron Embed through Nightshift.
- **Coding tool**: Claude Code (Cursor was made optional by the organisers).

## Setup
Work in progress. See docs/devlog.md for the current state. Copy `.env.example` to `.env` and fill in your
own local values; never commit `.env`.

Context service (World Model case board): set `CONTEXT_SERVICE_TOKEN` in `.env` (e.g. `openssl rand -hex 32`),
then `docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d context-service`. The dev overlay
publishes it on 127.0.0.1:7070 for a backend running on the host; the main compose file keeps it internal.

## Run the app (API + UI)
1. In `.env`, set `APP_DATABASE_URL=sqlite:///data/local/app.db`, a `JWT_SECRET` of at least 32 characters
   (`openssl rand -hex 32`), and `DEMO_BUYER_PASSWORD`, `DEMO_RISK_PASSWORD`, `DEMO_DIRECTOR_PASSWORD`
   (8+ characters each; an empty password disables that user). Keep `LANGCHAIN_TRACING_V2=false`.
2. `scripts/run_app.sh`, then open http://127.0.0.1:8000. It runs one uvicorn worker on purpose
   (decision 0008).
3. Log in as `buyer` (electronics only), `risk_manager` or `director`. Use an example button (S0003, S0005,
   or the S0011 injection demo) and watch the agent timeline. Click an evidence id to see the evidence. A
   banner shows while the data cache is still warming.
