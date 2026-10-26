# 0003: Memory and retrieval design

Date: 25 Oct 2026. Status: accepted.

## Context
Answers must be exact and verifiable (track "The Agent That Can Explain Why"). Business facts are structured
(ERP, EDGAR, Eurostat) and queried with SQL through Zetaris. The hackathon requires at least one Meterless engine
in a working feature. NVIDIA free tier allows 40 requests/minute.

## Decision
- Facts come only from Zetaris SQL; no vector search over business data.
- Memory in three layers: LangGraph checkpoints (one investigation), Meterless World Model (shared case board),
  Meterless H-MEM (verified past cases, approved decisions, feedback).
- H-MEM embeddings come from Nemotron Embed through Nightshift; H-MEM's hybrid ranking is the reranker.
  No separate vector database or reranker model.
- Speed comes from caching tool results (TTL, role-scoped) and Zetaris CACHE TABLE for external sources.

## Consequences
Fewer components and fewer model calls. Memory is scoped by role and only stores verified content.
Scale path if recall becomes a bottleneck: pgvector in the app Postgres (decided by the load test).
