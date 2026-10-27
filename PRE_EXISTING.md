# Pre-existing and third-party components

All project code in this repository was written during the build window (from 22 Oct 2026, 00:00 UTC).
The following existing components are used or adapted and are declared here and in the submission form.

| Component | Source | Licence | How it is used |
|---|---|---|---|
| Zetaris getting-started recipes and helper patterns | Zetaris getting-started repository | Apache 2.0 | Recipe SQL patterns (EDGAR, Eurostat, NACE) adapted in data_pipeline/zetaris_sql/; REST call shape follows scripts/zetaris_sql.py |
| Meterless World Model reference implementation and conformance suite | github.com/Meterless/Meterless, engines/world-model/ (reference/, conformance/, LICENSE) at commit 1742dec56a66e58eef9dda2d2b865823758ca8cd | Apache 2.0 | Vendored unchanged in context-service/vendor/world-model/ (sha256 manifest checked in CI); wrapped by context-service/src; conformance suite runs in CI |
| Meterless "Add Meterless as a provider" Power prompt | meterless.ai/engines.html | as published | Provider contract for backend/app/llm |
| Open-source libraries | pyproject.toml, uv.lock, context-service/package.json and package-lock.json | various (permissive) | Dependencies |
