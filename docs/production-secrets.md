# Production secrets

The hackathon build reads its secrets from `.env`. The file is git-ignored and set to chmod 600. Each
value is a pydantic `SecretStr`, redacted from logs and errors, and gitleaks runs on every commit. That
is enough for one machine. A production deployment needs a secrets manager and rotation.

## Secrets in this app

| Setting | What it unlocks | Used by |
| --- | --- | --- |
| `METERLESS_RUNTIME_TOKEN` | The Nightshift runtime (model calls) | API at runtime |
| NVIDIA API key | NVIDIA hosted models | Nightshift only; the app never sees it |
| `ZETARIS_KEY_BUYER`, `ZETARIS_KEY_RISK` | Read-only role users svc_buyer and svc_risk | API at runtime |
| `ZETARIS_ADMIN_API_KEY` | Zetaris administration | Setup only. It still sits in the runtime `.env` (SEC-046) |
| `ERP_DB_PASSWORD`, `ERP_READER_PASSWORD`, `ERP_DATABASE_URL` | The synthetic ERP Postgres (owner and read-only reader) | ERP setup; Zetaris datasource |
| `CONTEXT_SERVICE_TOKEN` | The World Model case board (X-Internal-Token) | API and context service |
| `JWT_SECRET` | Signing user sessions (HS256). Whoever has it can log in as anyone. | API |
| `DEMO_BUYER_PASSWORD`, `DEMO_RISK_PASSWORD`, `DEMO_DIRECTOR_PASSWORD` | The demo logins | API (scrypt-hashed at startup) |

## What production would need
- **A secrets manager** (for example AWS Secrets Manager, GCP Secret Manager, Azure Key Vault or HashiCorp
  Vault).
  - Secrets are injected at start as environment variables or files, never baked into images or the
    repository.
  - Access is per service, with least privilege: the API does not get the admin key, and the context
    service gets only its own token.
- **Separate setup and runtime.** The Zetaris admin key and the ERP owner password belong in a
  setup-only profile or a break-glass vault entry, not in the runtime environment (SEC-046).
- **Rotation.**
  - `JWT_SECRET`: rotate on a schedule. Support two keys (current and previous) for one session
    lifetime, so users are not logged out all at once.
  - Role keys and the context-service token: rotate at least quarterly and on any suspicion, using
    overlapping keys where the system supports them.
  - Database passwords: rotate with the datasource update in Zetaris in one change.
- **Real identity.** SSO/OIDC replaces the demo passwords. Then there are no shared passwords left to
  manage.
- **Audit.** Log every access to the secrets manager and alert on unusual reads.
- **No secrets in SQL text.** Create Zetaris datasources through the UI connection form or a secret
  reference, not through `CREATE DATASOURCE ... password "..."`. That statement is kept in the SQL
  Editor history (SEC-044, SEC-060).

## After the event (do this)
1. **Rotate the Zetaris admin API key.** It has been in the local `.env` during development (SEC-046).
2. **Change the ERP reader password** (`zetaris_reader`, `ERP_READER_PASSWORD`). Then update the Zetaris
   datasource through the UI form, and delete the SQL Editor history entries that contain the old
   password (SEC-044).
3. Rotate the role keys (svc_buyer, svc_risk), the Nightshift runtime token, the context-service token,
   `JWT_SECRET` and the demo passwords, if they were shown or shared during the demo.
4. If the NVIDIA key was ever shared outside Nightshift, revoke it at NVIDIA and create a new one.
5. Run `gitleaks detect` on the full history once more before making the repository public.
