-- Verification for 10_erp_datasource_create.sql. Read-only; safe to run with the runner.
-- Compare each count with the erp-db (backend/tests/integration/test_erp_db.py).

SHOW DATASOURCES;

SHOW DATASOURCE TABLES erp_pg;

SELECT COUNT(1) AS row_count FROM erp_pg.suppliers;

SELECT COUNT(1) AS row_count FROM erp_pg.parts;

SELECT COUNT(1) AS row_count FROM erp_pg.part_sourcing;

SELECT COUNT(1) AS row_count FROM erp_pg.purchase_orders;

SELECT COUNT(1) AS row_count FROM erp_pg.deliveries;

SELECT COUNT(1) AS row_count FROM erp_pg.contracts;
