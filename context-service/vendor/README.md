# Vendored Meterless engines (do not edit)

| Directory | Source | Commit | Licence |
|---|---|---|---|
| `world-model/` (`reference/`, `conformance/`, `LICENSE`) | https://github.com/Meterless/Meterless, `engines/world-model/` | `1742dec56a66e58eef9dda2d2b865823758ca8cd` (19 Oct 2026) | Apache-2.0 (`world-model/LICENSE`) |

The files are byte-identical to upstream. `world-model.sha256` lists every file, and
`npm run vendor:verify` (run in CI) fails if a file was edited, added or removed. Our code wraps the
engine through `src/engine.ts` and does not change it. See docs/decisions/0006-world-model-case-board.md.

To update: copy the new upstream files, run `node scripts/verify-vendor.mjs --write`, record the new
commit here and in PRE_EXISTING.md, and run both conformance scripts.
