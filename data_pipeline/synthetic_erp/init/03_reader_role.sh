#!/bin/bash
# Create the read-only role zetaris_reader (SELECT only). Runs once, after the schema and the seed.
# The password arrives as a psql variable (never pasted into SQL text) from ERP_READER_PASSWORD.
set -euo pipefail

psql -v ON_ERROR_STOP=1 -v reader_pw="$ERP_READER_PASSWORD" \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
CREATE ROLE zetaris_reader LOGIN PASSWORD :'reader_pw'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
ALTER ROLE zetaris_reader SET default_transaction_read_only = on;
-- Security: the read-only default is only a session setting the reader can undo (SEC-032), so the
-- real controls are privileges: no large-object writes (they persist) and no temp tables (SEC-031).
REVOKE EXECUTE ON FUNCTION
  lo_create(oid), lo_creat(integer), lo_from_bytea(oid, bytea), lo_put(oid, bigint, bytea),
  lowrite(integer, bytea), lo_open(oid, integer), lo_import(text), lo_import(text, oid),
  lo_export(oid, text), lo_unlink(oid), lo_truncate(integer, integer),
  lo_truncate64(integer, bigint)
  FROM PUBLIC;
REVOKE TEMP ON DATABASE erp FROM PUBLIC;
-- Security: bound what one reader can consume (SEC-033). Zetaris and the API enforce their own
-- timeouts too, because the reader can raise its session timeout.
ALTER ROLE zetaris_reader CONNECTION LIMIT 10;
ALTER ROLE zetaris_reader SET statement_timeout = '15s';
ALTER ROLE zetaris_reader SET idle_in_transaction_session_timeout = '30s';
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT CONNECT ON DATABASE erp TO zetaris_reader;
GRANT USAGE ON SCHEMA public TO zetaris_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO zetaris_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO zetaris_reader;
SQL
