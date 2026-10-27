-- Synthetic ERP (Postgres 16 in docker compose, host port 5433) as a JDBC datasource.
-- Not run by scripts: the user runs these statements as admin in the Zetaris SQL Editor,
-- replacing <ERP_READER_PASSWORD> with the real password of the read-only role
-- zetaris_reader (SELECT only, see data_pipeline/synthetic_erp/init/03_reader_role.sh).
-- Zetaris runs in Docker, so the host database is reached via host.docker.internal.
-- Run one statement at a time, without the semicolon.

CREATE DATASOURCE erp_pg DESCRIBE BY "Synthetic ERP, read-only role zetaris_reader" OPTIONS (
  jdbcdriver "org.postgresql.Driver",
  jdbcurl "jdbc:postgresql://host.docker.internal:5433/erp",
  username "zetaris_reader",
  password "<ERP_READER_PASSWORD>",
  fetchsize "1000"
);

REGISTER DATASOURCE TABLES FROM erp_pg;
