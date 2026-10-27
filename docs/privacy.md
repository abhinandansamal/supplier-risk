# Privacy notes (GDPR-oriented)

**This is not legal advice.** These notes describe what the hackathon build does with data. They also
list what a production deployment would need, so a data protection officer or lawyer can assess it.
State: 27 Oct 2026 (decision 0008).

## What personal data there is
The business data is **synthetic** (docs/data-card.md). Suppliers are companies: S0001 to S0008 are real
public companies, and everything the ERP says about them is invented. No natural persons are modelled
in the ERP.

Personal data appears only through the people who use the app:

| Data | Example | Why it is needed |
| --- | --- | --- |
| User identity | username `buyer`, role, category | Access control (scope) and ownership |
| Question text | "Why is Microchip (S0003) risky?" | It is the investigation itself. Users could type personal data into it; nothing stops them. |
| Decisions | who approved or rejected a proposal, when, with an optional comment | Human-in-the-loop accountability |
| Session cookie | signed token with the username, valid 30 minutes | Login. No tracking cookies, no browser storage. |

The app does not log client IP addresses, does not profile users and has no analytics.

## Where data goes

| Place | What is stored or sent | Kept for |
| --- | --- | --- |
| App database (SQLite, `data/local/app.db`, this machine) | Investigations (user, role, category, question, status, times); results; proposals; decisions. Results hold the report, claims, scores, evidence metadata and facts, and the trace; evidence rows are removed first. | Until deleted, or `RETENTION_DAYS` (30) at the next startup |
| Agent trace files (`runs/<id>.jsonl`) | Step, model id, Nightshift receipt id, tokens, latency, tool names, evidence ids, fixed codes. No question text, no rows. | Deleted with the investigation and by the purge |
| App logs (`logs/*.jsonl`) | Events with request and investigation ids, role, path, status and duration. Only ids and counts: no question text, prompts, rows, passwords or tokens (redaction filter, tested). | Rotating, 10 MB x 5 files; not purged by date |
| World Model case board (context service, Docker, this machine) | Per investigation: fixed-template summaries, supplier ids, evidence ids and counts. No question text, no data text. | **Not erased** by DELETE or the purge (SEC-106) |
| Meterless Nightshift (runtime on this machine) | Every model call | Nightshift keeps a response cache (the receipts show `cache_status: exact`); its retention is set by Nightshift |
| **NVIDIA** (cloud, through Nightshift) | Nightshift forwards each call to NVIDIA's hosted Nemotron models. What leaves the machine: the **question text** (guard, planner, writer); the risk scores, evidence facts and tool parameters (writer, recommender); the plan, coverage and fixed case-board summaries (evaluator); the dimension, supplier ids, countries and gap codes (research). ERP free text such as buyer notes never goes to a model. | Under NVIDIA's terms for its API (free tier). Check those terms: this build has no data processing agreement with NVIDIA. |
| Zetaris (local install) | One constant SELECT per view, sent with the role key; no parameters, so no user input and no personal data in SQL. | Zetaris query history and logs; the admin history also holds the ERP datasource password (SEC-044) |
| LangSmith / LangChain tracing | Nothing. Startup refuses to run when `LANGCHAIN_TRACING_V2`, `LANGSMITH_TRACING` or `LANGCHAIN_TRACING` is true, and otherwise logs `langchain_tracing_off`. | None |

## Retention
- `RETENTION_DAYS` (default 30): investigations older than this are deleted at startup, with their
  results, proposals, decisions and trace files. The purge runs **only at startup**; a long-running
  process keeps older data until it restarts.
- Logs rotate by size, not by age.
- Investigations interrupted by a restart are marked `failed` and follow the same retention.

## Erasure (right to erasure)
`DELETE /api/v1/investigations/{id}` erases the user's own investigation: the investigation row, result,
proposals, decisions and the trace file. A queued investigation leaves the queue; a running one is
cancelled. Known limits:
- **World Model** events of the case stay (SEC-106). They hold no question text, but they do hold ids
  and counts.
- **Other users' decisions** on the proposals are deleted as well. This keeps erasure simple, but it
  loses accountability (SEC-105). Production should keep a pseudonymised audit record instead.
- **Logs** keep the investigation id and the role (ids only).
- **Nightshift's cache and NVIDIA** are outside the app's control.
- **Demo identities** are configuration (`DEMO_*_PASSWORD`). Removing a password disables the user.

## What production would need
- **Lawful basis and records.** A legal basis for each purpose (likely legitimate interest or contract
  for procurement staff), a record of processing (Art. 30) and an information notice for users.
- **Processors and transfers.**
  - Data processing agreements with the model provider (NVIDIA, or Meterless if it hosts).
  - A transfer assessment for processing outside the EU, or an EU-hosted or self-hosted model.
  - Zero-retention or no-training settings where offered.
  - Prefer sending as little as possible: redact personal data from questions before model calls.
- **Identity.** A real identity provider (SSO/OIDC) instead of demo passwords, an access review and a
  joiners/leavers process.
- **Storage.** The database encrypted at rest (Postgres with managed keys), backups with the same
  retention, and a scheduled retention job instead of startup-only purging. Logs retained by age.
- **Accountability.**
  - An append-only, hash-chained audit log that survives erasure in pseudonymised form (SEC-105).
  - Erasure that reaches the case board (SEC-106) and the model cache.
  - A data-subject request process (access, export, erasure) with deadlines.
- **Assessment.** A DPIA if questions may contain personal data at scale, plus a breach-notification
  process.
- **Secrets.** See [production-secrets.md](production-secrets.md).
