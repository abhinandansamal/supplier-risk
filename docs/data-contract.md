# Data contract

One row per table the agent may use. Grain, keys, freshness, licence and the risk component each
feeds. Sections for the public sources (EDGAR, Eurostat) are added with the Zetaris pipeline.

## Synthetic ERP (Postgres 16, host port 5433)

Generated with seed 42 by `data_pipeline/synthetic_erp/generate.py`; labelled synthetic everywhere
(see `docs/data-card.md`). Zetaris reads it with the read-only role `zetaris_reader` (SELECT only).

| Table | Grain | Keys | Freshness | Licence | Feeds |
| --- | --- | --- | --- | --- | --- |
| suppliers | one row per supplier | PK supplier_id | regenerated on demand | synthetic | all components |
| parts | one row per part | PK part_id | regenerated on demand | synthetic | concentration |
| part_sourcing | one row per part and supplier | PK (part_id, supplier_id); FKs to parts, suppliers | regenerated on demand | synthetic | concentration |
| purchase_orders | one row per purchase order | PK po_id; FKs to suppliers, parts, part_sourcing | last 18 months up to the run date, plus open orders due within 45 days | synthetic | concentration, delivery |
| deliveries | one row per non-cancelled purchase order | PK/FK po_id; delivered_date is NULL while open | as purchase_orders | synthetic | delivery |
| contracts | one row per supplier | PK/FK supplier_id; buyer_notes is free text (untrusted) | regenerated on demand | synthetic | concentration (value, end date) |

## SEC CIK checks

Public suppliers in `data_pipeline/suppliers.yaml` were verified on 2026-10-27 with
`scripts/verify_ciks.py` against SEC EDGAR:

1. `GET https://data.sec.gov/submissions/CIK<10 digits>.json` must answer 200 (the entity name is recorded).
2. `GET https://data.sec.gov/api/xbrl/companyconcept/CIK<10 digits>/us-gaap/<tag>.json` must answer 200 for
   `Revenues` or, if that is missing, `RevenueFromContractWithCustomerExcludingAssessedTax`.

The declared User-Agent was passed through the environment only and is not stored in the repository.
Requests were limited to 2 per second. CIKs were looked up from the SEC ticker file
(`https://www.sec.gov/files/company_tickers.json`), not typed from memory.

| Supplier | Name in suppliers.yaml | CIK | SEC entity name | Revenue tag found | Result |
| --- | --- | --- | --- | --- | --- |
| S0001 | Texas Instruments Incorporated | 0000097476 | TEXAS INSTRUMENTS INC | RevenueFromContractWithCustomerExcludingAssessedTax | PASS |
| S0002 | Analog Devices, Inc. | 0000006281 | ANALOG DEVICES INC | Revenues | PASS |
| S0003 | Microchip Technology Incorporated | 0000827054 | MICROCHIP TECHNOLOGY INC | Revenues | PASS |
| S0004 | Dow Inc. | 0001751788 | DOW INC. | Revenues | PASS |
| S0005 | Celanese Corporation | 0001306830 | Celanese Corp | RevenueFromContractWithCustomerExcludingAssessedTax | PASS |
| S0006 | Alcoa Corporation | 0001675149 | Alcoa Corp | RevenueFromContractWithCustomerExcludingAssessedTax | PASS |
| S0007 | Nucor Corporation | 0000073309 | NUCOR CORP | Revenues | PASS |
| S0008 | United Parcel Service, Inc. | 0001090727 | UNITED PARCEL SERVICE INC | Revenues | PASS |

All 8 picks passed; none had to be replaced. The 4 private suppliers (S0009 to S0012) are fictional
and have no CIK. Note: the check proves the revenue tag exists, not that it has current values;
the EDGAR feature checks freshness when it registers the REST tables.
