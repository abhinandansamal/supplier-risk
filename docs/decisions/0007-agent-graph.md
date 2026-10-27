# 0007: Agent graph, claim verifier and evidence facts

Date: 27 Oct 2026. Status: accepted (plan approved by the user, including choices 1-4 below).

## Context
- Feature 7a builds the agent team of architecture §6 as a LangGraph graph. Nothing may reach the
  user unless it is backed by evidence that a Python check can confirm.
- The NVIDIA free tier allows 40 requests/minute, and the budget is 15 model calls per investigation.
  Ultra is slow, and Zetaris snapshot loads take seconds to minutes.
- SEC-066: 20 sample rows cannot prove an aggregate over 57 deliveries. SEC-061: callers must not
  wait on a cold load, or on the whole investigation, without a limit. SEC-084: the World Model
  does not scope reads.

## Decision
- **Graph.** guard -> planner -> research -> evaluator -> (research | score) -> recommender ->
  writer -> verifier -> (writer once more | end | escalation).
  - LangGraph state only routes. Evidence, findings, scores, proposals, report and trace live on a
    per-investigation `Run`, so a stop at any point still returns what was checked.
  - Entry point: `Investigator(settings, llm=..., cache=..., board_client=...).run_investigation(user,
    question)`. It returns `InvestigationResult` with status completed, incomplete, escalated or
    rejected.
- **Choices approved with the plan.**
  1. Extra research rounds go straight back to Research for the retryable gaps, without another
     Planner call: at most 2 extra rounds.
  2. If the Evaluator is still unsatisfied, the case is scored and escalated with the Python
     fallback report and the gap list. The Writer is not called.
  3. The director gets no `propose_action` (roles in §6.3), and the Recommender call is skipped.
  4. H-MEM recall waits for the H-MEM client. The Planner and Recommender run without past cases.
- **Model routes are settings.** `AGENT_MODEL_<STEP>` takes default, deep or fast, with defaults from §6.4
  (guard fast, planner/recommender/writer deep, research/evaluator default). The exact model ids
  still come only from `METERLESS_*_MODEL`, so a step can never send a routing mode.
- **One model path.** `call_model()` applies:
  - the step route and the call budget (15);
  - a per-call timeout (`MODEL_CALL_TIMEOUT_SECONDS`, 240 s), capped by the time left before
    `INVESTIGATION_DEADLINE_SECONDS` (900 s);
  - one fallback from deep to default for timeouts, 5xx, 429 and bad responses.

  It records a TraceEvent per call: route, model id, routed model, receipt id, cache status,
  tokens, latency and prompt version. A rejected token, the budget used up or the deadline passed
  ends the investigation as incomplete. Other failures degrade the step:
  - guard -> rules only;
  - planner -> all allowed dimensions;
  - research -> a retryable gap;
  - evaluator -> the Python verdict;
  - recommender -> no proposals;
  - writer -> the Python fallback report.
- **Research is single-turn.** The model picks tool calls, and Python checks and runs them:
  - the tool must be offered for this dimension and role;
  - any supplier or country in the arguments must belong to the question;
  - at most 8 calls per step;
  - each call is bounded by `TOOL_CALL_TIMEOUT_SECONDS` (20 s).

  Tool results never go back to a model. Weather needs no model, so it runs directly. An empty
  `delivery_history` is retryable (§8.6). Any other empty result is a known gap.
- **The Writer sees facts, not rows.** It gets each evidence record's id, tool, facts, note and parameters.
  ERP free text (buyer notes, names, descriptions) therefore never reaches the writer. The research
  model sees no tool output at all.
- **SEC-066: evidence facts.** Every `EvidenceRecord` carries `facts` (the numbers the tool computed
  from all rows, flat, at most 64 keys, finite) and `rows_digest` (SHA-256 of every row the result
  rests on). The score step adds one `computed` record per supplier: total, coverage, each sub-score
  and its weight.
- **Verifier (Python)**, tightened after the security review (SEC-087..097).
  - Every cited id must exist in this investigation. A cited record about a supplier must be about
    a supplier the claim names.
  - Every number in a claim must be backed by a fact of the evidence it cites. The unit next to the
    number decides which facts can back it:
    - percent: rate or share facts x 100, or `*_pct`, `*_pp` and change facts;
    - money (M, bn, USD, EUR): revenue and value facts;
    - window ("6 months"): window parameters and facts;
    - anything else: facts only, and a word near the number must match a word of the fact key.
  - Integer facts match exactly. Floats may be rounded to the written precision, within 5 %. A
    non-zero fact never backs a written 0. An explicit sign must match. For changes, direction
    words must agree with the sign.
  - Text is NFKC-normalised first. Non-ASCII digits, number words, bad digit grouping, hidden
    characters, links, bare domains and markup are refused.
  - A rating word, synonyms included, must match the computed rating. On a claim about
    suppliers with different ratings it is refused.
  - Proposal rationales pass the same checks. "Missing" is Python-built only.
  - On failure there is one rewrite with the problem list. If it fails again, the failing claims
    are marked unverified and the case is escalated.
- **Injection visibility.** The guard's detector (`app/security/injection.py`) also scans every
  `untrusted_text` field of tool results. A hit records the trace event and World Model event
  `injection_detected`: field name, rule and evidence id, never the text. The report notes it, e.g.
  "1 buyer note contained instruction-like text and was ignored". The context service accepts the
  new event kind.
- **Case board.** Every step appends events through the World Model client and keeps an in-process copy.
  - After the first `memory_unavailable`, the service is not asked again for this investigation.
  - Summaries are fixed Python templates; model output never becomes a summary.
  - SEC-084 (agent side): the agent reads only its own case, and drops events about suppliers
    outside the resolved scope. `scope_entity_view()` is ready for the API in Feature 7b.

## Consequences
- The headline, rating, score, notes and the score-based missing items are always Python. The model
  can only add claims, and they must pass the verifier.
- A happy path takes 9 model calls (guard, planner, 4 research steps, evaluator, recommender, writer).
  A rewrite or an extra round adds 1-2 calls. Two extra rounds plus a rewrite still fit in 15.
- The research model cannot react to what a tool returned, for example by retrying with a wider
  window, inside one step. That happens only through the Evaluator's extra round.
- The verifier's tolerance is "rounded to the written precision, within 5 %": "36" passes for a
  total of 35.9. Percent and money numbers are matched by unit, not by noun. For example,
  "45 % late" passes when the on-time rate is 45 %. The evidence panel shows the fact behind
  every claim.
- The backend and the context service must be deployed together. An older service refuses
  `injection_detected` as an invalid event, which the client treats as a bug and raises.
