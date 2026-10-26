# Supplier Risk Investigator

An explainable multi-agent assistant for procurement teams: it investigates why a supplier is risky,
answers with numbered claims backed by evidence from Zetaris, and recommends actions that a human approves.

Open Agent Hackathon 2026, track: "The Agent That Can Explain Why". **Status: in development.**

## Sponsor technology
- **Zetaris**: the single data layer (synthetic ERP + SEC EDGAR + Eurostat + industry codes), role-scoped views.
- **Meterless Nightshift**: the primary AI provider (OpenAI-compatible Runtime).
- **Meterless engines**: World Model (shared case board for the agents) and H-MEM (memory of verified past cases).
- **NVIDIA**: Nemotron 3.5 Lightning, Nemotron 3 Ultra, Nemotron 3.5 Bolt and Nemotron Embed through Nightshift.
- **Coding tool**: Claude Code (Cursor was made optional by the organisers).

## Setup
Work in progress. See docs/devlog.md for the current state. Copy `.env.example` to `.env` and fill in your
own local values; never commit `.env`.
