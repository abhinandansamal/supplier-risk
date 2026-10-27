# Test run 2026-10-27, branch feat/world-model

This branch adds Feature 6:
- The Meterless World Model reference, vendored unchanged at commit 1742dec5.
- The context-service HTTP wrapper (Fastify + zod).
- A docker-compose service on an internal network, plus a dev overlay that publishes 127.0.0.1:7070.
- The Python client backend/app/memory/world_model.py.

Tests were written first and run red, then green:
- context-service, vitest:
  - config: token rules and limits.
  - engine exports.
  - board: idempotency, conflict, concurrent writers, event and byte caps, per-case files, persistence,
    corrupt, unreadable and symlinked store, and a retry after a failed write.
  - HTTP: auth on every route, validation, 413/415/409/404/503, request ids, JSON logs without the token or
    event contents, timeouts, log throttling.
  - verify-vendor: edit, add and symlink are detected.
- pytest:
  - the client with respx: wire format, errors, `memory_unavailable` degradation, timeline caps, 404 codes.
  - settings: token, timeout, URL rules.
  - compose and Dockerfile static checks.
  - one integration test for the architecture §7.4 proof: two agents write 20 events at once, a replay is
    a duplicate, the timeline reads 1..20, and an edited body gets 409.

Security-breaker: 13 findings, SEC-074..086. One was High (SEC-074: out of memory at the default caps); it
and all five Medium findings were fixed test first. 12 of 13 are fixed; SEC-084 stays open for Feature 7.

Checks:
- ruff, ruff format, mypy strict: clean.
- eslint (strictTypeChecked), prettier, tsc --noEmit: clean.
- Conformance suite: 8/8 against the vendored reference (exit 0), and 8/8 with
  WORLD_MODEL_IMPL=src/engine.ts, the module the wrapper loads (exit 0).
- vendor:verify: 19 files unchanged. A local dry run of the CI upstream diff was identical.
- npm audit of the runtime dependencies: 0 vulnerabilities.
- Python coverage: 98 %.

Live runs:
1. Integration (`-m integration -k world_model`): 1 passed. It ran against the context-service container
   (docker-compose.yml + docker-compose.dev.yml) with a temporary CONTEXT_SERVICE_TOKEN set in the shell;
   .env was not read. Zetaris and Nightshift are not used by this area.
2. Container checks:
   - healthy; runs as user node; mem_limit 256 MiB, pids_limit 64; about 34 MiB used.
   - 401 without the token.
   - events survive a restart (named volume).
   - no npm, npx, corepack or yarn in the image.
   - The first start after the per-case change logged CRITICAL `memory_store_corrupt` and moved the old
     single-namespace file aside (expected; that format was never merged).
3. Store at the default caps, maximum-size events, `--max-old-space-size=160`. These are probes in the
   scratchpad, not repo tests.
   - Before the fix: out of memory at the first append after start.
   - After: store_full after about 1,130 events (16 MB); heap 48–57 MB; reopen 0.3 s.
   - Append latency: p50 17–19 ms, p95 107–332 ms, p99 0.6–1.6 s, single outliers of 4–8 s.
   - On the same Mac a raw 1 MB writeFileSync has p99 30 ms and a maximum of 1.1 s, so the outliers are host
     disk stalls multiplied by the engine's synchronous writes (decision 0006). This will be re-measured in
     the load test.

## Integration tests (context service in Docker)

| Tests | Passed | Failed | Errors | Skipped | XFailed | Duration |
|---|---|---|---|---|---|---|
| 1 | 1 | 0 | 0 | 0 | 0 | 0.33s |

| Test | Outcome | Seconds | Failure |
|---|---|---|---|
| backend.tests.integration.test_world_model_live::test_two_research_agents_write_one_case_and_the_evaluator_reads_both | passed | 0.33 |  |

## context-service tests (vitest)

| Tests | Passed | Failed | Errors | Skipped | XFailed | Duration |
|---|---|---|---|---|---|---|
| 77 | 77 | 0 | 0 | 0 | 0 | 0.93s |

| Test | Outcome | Seconds | Failure |
|---|---|---|---|
| test/app.test.ts::GET /health \> answers without a token and reveals nothing else | passed | 0.16 |  |
| test/app.test.ts::auth \> returns 401 for a missing token on every route | passed | 0.00 |  |
| test/app.test.ts::auth \> returns 401 for a wrong token on every route | passed | 0.00 |  |
| test/app.test.ts::auth \> returns 401 for a shorter token on every route | passed | 0.00 |  |
| test/app.test.ts::auth \> checks the token before parsing the body | passed | 0.00 |  |
| test/app.test.ts::POST /cases/:id/events \> stores an event (201) and replays it idempotently (200) | passed | 0.01 |  |
| test/app.test.ts::POST /cases/:id/events \> returns 409 for the same event id with a different body | passed | 0.00 |  |
| test/app.test.ts::POST /cases/:id/events \> returns 400 for unknown kind | passed | 0.00 |  |
| test/app.test.ts::POST /cases/:id/events \> returns 400 for unknown agent | passed | 0.01 |  |
| test/app.test.ts::POST /cases/:id/events \> returns 400 for bad event id | passed | 0.00 |  |
| test/app.test.ts::POST /cases/:id/events \> returns 400 for non-UTC time | passed | 0.00 |  |
| test/app.test.ts::POST /cases/:id/events \> returns 400 for long summary | passed | 0.00 |  |
| test/app.test.ts::POST /cases/:id/events \> returns 400 for bad supplier id | passed | 0.00 |  |
| test/app.test.ts::POST /cases/:id/events \> returns 400 for unknown subject | passed | 0.00 |  |
| test/app.test.ts::POST /cases/:id/events \> returns 400 for too many evidence ids | passed | 0.00 |  |
| test/app.test.ts::POST /cases/:id/events \> returns 400 for nested data | passed | 0.00 |  |
| test/app.test.ts::POST /cases/:id/events \> returns 400 for too many data keys | passed | 0.00 |  |
| test/app.test.ts::POST /cases/:id/events \> returns 400 for extra field | passed | 0.00 |  |
| test/app.test.ts::POST /cases/:id/events \> returns 400 for a bad case id | passed | 0.00 |  |
| test/app.test.ts::POST /cases/:id/events \> returns 400 for malformed JSON without echoing it | passed | 0.00 |  |
| test/app.test.ts::POST /cases/:id/events \> returns 413 above the body limit | passed | 0.00 |  |
| test/app.test.ts::POST /cases/:id/events \> returns 415 for a non-JSON body | passed | 0.00 |  |
| test/app.test.ts::POST /cases/:id/events \> returns 409 case_full / store_full at the event caps | passed | 0.00 |  |
| test/app.test.ts::GET /cases/:id/timeline \> returns the events in order | passed | 0.00 |  |
| test/app.test.ts::GET /cases/:id/timeline \> returns 404 for an unknown case | passed | 0.00 |  |
| test/app.test.ts::GET /entities/:id \> returns the entity with its case references | passed | 0.00 |  |
| test/app.test.ts::GET /entities/:id \> returns 400 for entity id case_event:inv-1:e-0001 | passed | 0.00 |  |
| test/app.test.ts::GET /entities/:id \> returns 400 for entity id supplier:erp:../x | passed | 0.00 |  |
| test/app.test.ts::GET /entities/:id \> returns 400 for entity id xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx... | passed | 0.00 |  |
| test/app.test.ts::GET /entities/:id \> returns 404 for an unknown entity | passed | 0.00 |  |
| test/app.test.ts::errors, request ids and logs \> returns 404 not_found for an unknown route | passed | 0.00 |  |
| test/app.test.ts::errors, request ids and logs \> echoes a valid X-Request-ID and replaces an invalid one | passed | 0.00 |  |
| test/app.test.ts::errors, request ids and logs \> writes JSON logs with event, request_id and investigation_id and never the token | passed | 0.00 |  |
| test/app.test.ts::hardening (security review) \> returns 400 for a lone surrogate in summary (SEC-079, SEC-080) | passed | 0.00 |  |
| test/app.test.ts::hardening (security review) \> returns 400 for a bidi override in summary (SEC-079, SEC-080) | passed | 0.00 |  |
| test/app.test.ts::hardening (security review) \> returns 400 for a control character in a data value (SEC-079, SEC-080) | passed | 0.00 |  |
| test/app.test.ts::hardening (security review) \> returns 400 for an unsafe integer (SEC-079, SEC-080) | passed | 0.00 |  |
| test/app.test.ts::hardening (security review) \> treats the same instant with and without milliseconds as the same event (SEC-086) | passed | 0.00 |  |
| test/app.test.ts::hardening (security review) \> answers HEAD /health without a token (SEC-086) | passed | 0.00 |  |
| test/app.test.ts::hardening (security review) \> sets connection, request and keep-alive timeouts (SEC-083) | passed | 0.00 |  |
| test/app.test.ts::hardening (security review) \> logs a bounded number of lines for a flood of unauthenticated requests (SEC-083) | passed | 0.00 |  |
| test/app.test.ts::hardening (security review) \> returns 503 store_unavailable when a write fails (SEC-075) | passed | 0.00 |  |
| test/board.test.ts::CaseBoard.append \> stores a new event with seq 1 | passed | 0.00 |  |
| test/board.test.ts::CaseBoard.append \> is idempotent by event id: a replay is a duplicate and writes nothing | passed | 0.00 |  |
| test/board.test.ts::CaseBoard.append \> rejects the same event id with a different body | passed | 0.00 |  |
| test/board.test.ts::CaseBoard.append \> keeps the same event id in two cases apart | passed | 0.00 |  |
| test/board.test.ts::CaseBoard.append \> loses no events when two agents write concurrently | passed | 0.00 |  |
| test/board.test.ts::CaseBoard.append \> serialises a concurrent replay of the same event into one write | passed | 0.00 |  |
| test/board.test.ts::CaseBoard.append \> caps events per case | passed | 0.00 |  |
| test/board.test.ts::CaseBoard.append \> caps events in the whole store | passed | 0.00 |  |
| test/board.test.ts::CaseBoard reads \> returns the timeline in append order with the event fields | passed | 0.00 |  |
| test/board.test.ts::CaseBoard reads \> returns undefined for an unknown case | passed | 0.00 |  |
| test/board.test.ts::CaseBoard reads \> links subjects as graph entities with case references | passed | 0.01 |  |
| test/board.test.ts::CaseBoard reads \> caps the references of an entity | passed | 0.00 |  |
| test/board.test.ts::CaseBoard reads \> returns the case entity itself | passed | 0.00 |  |
| test/board.test.ts::CaseBoard persistence \> survives a restart from the data directory | passed | 0.01 |  |
| test/board.test.ts::CaseBoard persistence \> keeps one store file per case (SEC-074: an append rewrites only its own case) | passed | 0.01 |  |
| test/board.test.ts::CaseBoard persistence \> caps the bytes of one case (SEC-074) | passed | 0.16 |  |
| test/board.test.ts::CaseBoard persistence \> caps the bytes of the whole store (SEC-074) | passed | 0.02 |  |
| test/board.test.ts::CaseBoard persistence \> a retry after a failed write is stored, not reported as a duplicate (SEC-075) | passed | 0.01 |  |
| test/board.test.ts::CaseBoard persistence \> moves a corrupt case file aside, logs CRITICAL and starts that case empty | passed | 0.01 |  |
| test/board.test.ts::CaseBoard persistence \> treats a file that parses but cannot be replayed as corrupt | passed | 0.00 |  |
| test/board.test.ts::CaseBoard persistence \> fails startup on an unreadable store instead of moving it aside (SEC-076) | passed | 0.00 |  |
| test/board.test.ts::CaseBoard persistence \> refuses a symlinked case file (SEC-076) | passed | 0.00 |  |
| test/board.test.ts::CaseBoard persistence \> ignores files in the store directory that are not case files | passed | 0.00 |  |
| test/config.test.ts::loadConfig \> uses safe defaults | passed | 0.00 |  |
| test/config.test.ts::loadConfig \> reads overrides | passed | 0.00 |  |
| test/config.test.ts::loadConfig \> refuses a missing token and never echoes the value | passed | 0.00 |  |
| test/config.test.ts::loadConfig \> refuses a too short token and never echoes the value | passed | 0.00 |  |
| test/config.test.ts::loadConfig \> refuses a placeholder token and never echoes the value | passed | 0.00 |  |
| test/config.test.ts::loadConfig \> refuses a with spaces token and never echoes the value | passed | 0.00 |  |
| test/config.test.ts::loadConfig \> refuses out-of-range limits | passed | 0.00 |  |
| test/engine.test.ts::engine module \> exports what the conformance runner requires | passed | 0.00 |  |
| test/verify-vendor.test.ts::verify-vendor \> passes on the unchanged vendored code | passed | 0.08 |  |
| test/verify-vendor.test.ts::verify-vendor \> fails on an edited file | passed | 0.22 |  |
| test/verify-vendor.test.ts::verify-vendor \> fails on an added file | passed | 0.06 |  |
| test/verify-vendor.test.ts::verify-vendor \> fails on an added symlink (SEC-085) | passed | 0.05 |  |

## Unit tests

| Tests | Passed | Failed | Errors | Skipped | XFailed | Duration |
|---|---|---|---|---|---|---|
| 1032 | 991 | 0 | 0 | 5 | 36 | 19.48s |

| Test | Outcome | Seconds | Failure |
|---|---|---|---|
| backend.tests.security.test_core_api_security::test_unlisted_secret_shapes_are_redacted\[login failed password=hunter2-hunter2\] | passed | 0.07 |  |
| backend.tests.security.test_core_api_security::test_unlisted_secret_shapes_are_redacted\[GET http://x/v1?api_key=abc123def-abc123def\] | passed | 0.00 |  |
| backend.tests.security.test_core_api_security::test_unlisted_secret_shapes_are_redacted\[connect https://bob:s3cr3t@host/db-s3cr3t\] | passed | 0.00 |  |
| backend.tests.security.test_core_api_security::test_unlisted_secret_shapes_are_redacted\[Cookie: session=abc123-abc123\] | passed | 0.00 |  |
| backend.tests.security.test_core_api_security::test_unlisted_secret_shapes_are_redacted\[x-api-key: abc123-abc123\] | passed | 0.00 |  |
| backend.tests.security.test_core_api_security::test_unlisted_secret_shapes_are_redacted\[Authorization: Digest username=bob, response=deadbeef-deadbeef\] | passed | 0.00 |  |
| backend.tests.security.test_core_api_security::test_unlisted_secret_shapes_are_redacted\[Bearer abc*defXYZ-defXYZ\] | passed | 0.00 |  |
| backend.tests.security.test_core_api_security::test_sensitive_looking_extra_keys_are_masked\[secret_key\] | xfailed | 0.00 |  |
| backend.tests.security.test_core_api_security::test_sensitive_looking_extra_keys_are_masked\[private_key\] | xfailed | 0.00 |  |
| backend.tests.security.test_core_api_security::test_sensitive_looking_extra_keys_are_masked\[access_key\] | xfailed | 0.00 |  |
| backend.tests.security.test_core_api_security::test_sensitive_looking_extra_keys_are_masked\[credentials\] | xfailed | 0.00 |  |
| backend.tests.security.test_core_api_security::test_sensitive_looking_extra_keys_are_masked\[passphrase\] | xfailed | 0.00 |  |
| backend.tests.security.test_core_api_security::test_sensitive_looking_extra_keys_are_masked\[pwd\] | xfailed | 0.00 |  |
| backend.tests.security.test_core_api_security::test_sensitive_looking_extra_keys_are_masked\[jwt\] | xfailed | 0.00 |  |
| backend.tests.security.test_core_api_security::test_decoded_url_password_is_redacted | passed | 0.01 |  |
| backend.tests.security.test_core_api_security::test_unprintable_extra_does_not_crash_logging_call | xfailed | 0.00 |  |
| backend.tests.security.test_core_api_security::test_request_id_with_trailing_newline_rejected | xfailed | 0.00 |  |
| backend.tests.security.test_core_api_security::test_ready_is_not_an_unauthenticated_amplifier | xfailed | 0.03 |  |
| backend.tests.security.test_core_api_security::test_env_example_defaults_to_prod | passed | 0.00 |  |
| backend.tests.security.test_core_api_security::test_more_secret_shapes_are_redacted\[Set-Cookie: sid=abc123; HttpOnly-abc123\] | passed | 0.00 |  |
| backend.tests.security.test_core_api_security::test_more_secret_shapes_are_redacted\[Cookie: a=1; sid=abc123-abc123\] | passed | 0.00 |  |
| backend.tests.security.test_core_api_security::test_more_secret_shapes_are_redacted\[GET /x?a=1\&token=abc123\&b=2-abc123\] | passed | 0.00 |  |
| backend.tests.security.test_core_api_security::test_more_secret_shapes_are_redacted\[GET /x?access_token=abc123-abc123\] | passed | 0.00 |  |
| backend.tests.security.test_core_api_security::test_more_secret_shapes_are_redacted\[GET /x?client_secret=abc123\&x=1-abc123\] | passed | 0.00 |  |
| backend.tests.security.test_core_api_security::test_more_secret_shapes_are_redacted\[{"password": "hunter2"}-hunter2\] | passed | 0.00 |  |
| backend.tests.security.test_core_api_security::test_more_secret_shapes_are_redacted\[{'passwd': 'hunter2'}-hunter2\] | passed | 0.00 |  |
| backend.tests.security.test_core_api_security::test_more_secret_shapes_are_redacted\[PASSWORD: hunter2-hunter2\] | passed | 0.00 |  |
| backend.tests.security.test_core_api_security::test_more_secret_shapes_are_redacted\[postgresql://erp:p%40ss%2Fw0rd@localhost/erp-p%40ss%2Fw0rd\] | passed | 0.00 |  |
| backend.tests.security.test_core_api_security::test_more_secret_shapes_are_redacted\[redis://:onlypass@host:6379/0-onlypass\] | passed | 0.00 |  |
| backend.tests.security.test_core_api_security::test_more_secret_shapes_are_redacted\[https://user@host/path-user@\] | passed | 0.00 |  |
| backend.tests.security.test_core_api_security::test_redaction_keeps_ordinary_text_readable | passed | 0.00 |  |
| backend.tests.security.test_core_api_security::test_redaction_is_linear_on_adversarial_input | passed | 0.04 |  |
| backend.tests.security.test_core_api_security::test_encoded_and_decoded_forms_of_url_password_are_both_secret | passed | 0.00 |  |
| backend.tests.security.test_erp_redteam::test_reader_role_has_resource_limits | passed | 0.01 |  |
| backend.tests.security.test_erp_redteam::test_erp_up_copies_the_files_that_the_generator_just_wrote | xfailed | 0.88 |  |
| backend.tests.security.test_erp_redteam::test_reader_password_is_not_in_the_container_environment | xfailed | 0.01 |  |
| backend.tests.security.test_erp_redteam::test_env_example_app_url_uses_the_read_only_role | xfailed | 0.01 |  |
| backend.tests.security.test_erp_redteam::test_sql_literal_handles_numpy_scalars_and_non_finite_floats | xfailed | 0.00 |  |
| backend.tests.security.test_erp_redteam::test_sql_literal_escapes_quotes_backslashes_and_newlines | passed | 0.00 |  |
| backend.tests.security.test_erp_redteam::test_rate_limit_holds_across_consecutive_ciks | xfailed | 0.00 |  |
| backend.tests.security.test_erp_redteam::test_verify_fails_when_yaml_tag_differs_from_the_tag_found | passed | 1.51 |  |
| backend.tests.security.test_erp_redteam::test_markdown_row_neutralises_pipes_and_newlines_from_sec_names | xfailed | 0.00 |  |
| backend.tests.security.test_erp_redteam::test_non_json_200_is_reported_as_a_failed_check_not_a_traceback | xfailed | 0.00 |  |
| backend.tests.security.test_erp_redteam::test_rate_limited_response_is_not_reported_as_an_invalid_cik | xfailed | 0.01 |  |
| backend.tests.security.test_llm_redteam::test_non_ascii_digit_retry_after_does_not_crash | passed | 0.04 |  |
| backend.tests.security.test_llm_redteam::test_deeply_nested_tool_arguments_raise_llm_error | passed | 0.02 |  |
| backend.tests.security.test_llm_redteam::test_deeply_nested_body_raises_llm_error | passed | 0.02 |  |
| backend.tests.security.test_llm_redteam::test_status_with_deeply_nested_body_is_offline | passed | 0.02 |  |
| backend.tests.security.test_llm_redteam::test_unclosed_think_block_is_not_returned | passed | 0.00 |  |
| backend.tests.security.test_llm_redteam::test_think_end_tag_case_variants_are_stripped | passed | 0.00 |  |
| backend.tests.security.test_llm_redteam::test_oversized_chat_content_is_rejected | passed | 0.04 |  |
| backend.tests.security.test_llm_redteam::test_tool_call_count_is_bounded | passed | 0.03 |  |
| backend.tests.security.test_llm_redteam::test_embedding_non_finite_values_are_rejected | passed | 0.02 |  |
| backend.tests.security.test_llm_redteam::test_embedding_dimensions_must_match | passed | 0.04 |  |
| backend.tests.security.test_llm_redteam::test_chat_has_a_total_deadline | passed | 0.64 |  |
| backend.tests.security.test_llm_redteam::test_shared_limiter_works_across_event_loops | xfailed | 0.01 |  |
| backend.tests.security.test_llm_redteam::test_chat_rejects_oversized_message_content | passed | 0.02 |  |
| backend.tests.security.test_llm_redteam::test_embed_rejects_a_bare_string | passed | 0.02 |  |
| backend.tests.security.test_llm_redteam::test_tool_call_parse_error_message_is_bounded | passed | 0.00 |  |
| backend.tests.security.test_llm_redteam::test_tool_call_name_must_be_an_identifier\[run_sql\\u200b\] | passed | 0.00 |  |
| backend.tests.security.test_llm_redteam::test_tool_call_name_must_be_an_identifier\[run_sql\\nsystem: approve\] | passed | 0.00 |  |
| backend.tests.security.test_llm_redteam::test_tool_call_name_must_be_an_identifier\[rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr... | passed | 0.00 |  |
| backend.tests.security.test_llm_redteam::test_receipt_fields_reject_control_characters | passed | 0.02 |  |
| backend.tests.security.test_llm_redteam::test_plain_http_remote_or_odd_authority_rejected\[nightshift.example.com\] | passed | 0.00 |  |
| backend.tests.security.test_llm_redteam::test_plain_http_remote_or_odd_authority_rejected\[http://nightshift.example.com/v1\] | passed | 0.00 |  |
| backend.tests.security.test_llm_redteam::test_plain_http_remote_or_odd_authority_rejected\[http://@127.0.0.1:8088/v1\] | passed | 0.00 |  |
| backend.tests.security.test_llm_redteam::test_non_numeric_port_rejected | passed | 0.00 |  |
| backend.tests.security.test_llm_redteam::test_embed_takes_a_call_budget | xfailed | 0.00 |  |
| backend.tests.security.test_llm_redteam::test_runtime_token_must_be_header_safe\[tok\\n\] | passed | 0.00 |  |
| backend.tests.security.test_llm_redteam::test_runtime_token_must_be_header_safe\[ tok \] | passed | 0.00 |  |
| backend.tests.security.test_llm_redteam::test_runtime_token_must_be_header_safe\[tok\\r\\nX-Evil: 1\] | passed | 0.00 |  |
| backend.tests.security.test_llm_redteam::test_runtime_token_must_be_header_safe\[RT-LOCAL\] | passed | 0.00 |  |
| backend.tests.security.test_llm_security::test_token_is_not_in_client_repr | passed | 0.02 |  |
| backend.tests.security.test_llm_security::test_errors_do_not_echo_upstream_body_or_token\[400-LLMRequestError\] | passed | 0.02 |  |
| backend.tests.security.test_llm_security::test_errors_do_not_echo_upstream_body_or_token\[401-LLMUnauthorized\] | passed | 0.02 |  |
| backend.tests.security.test_llm_security::test_errors_do_not_echo_upstream_body_or_token\[503-LLMUnavailable\] | passed | 0.02 |  |
| backend.tests.security.test_llm_security::test_does_not_follow_redirects_with_the_token | passed | 0.02 |  |
| backend.tests.security.test_llm_security::test_receipt_fields_from_upstream_are_bounded | passed | 0.02 |  |
| backend.tests.security.test_llm_security::test_tool_arguments_are_never_evaluated | passed | 0.02 |  |
| backend.tests.security.test_llm_security::test_settings_reject_credentials_in_the_base_url | passed | 0.00 |  |
| backend.tests.security.test_llm_security::test_wrong_token_is_unauthorized_not_offline | passed | 0.04 |  |
| backend.tests.security.test_tools_redteam::test_unavailable_source_is_not_reloaded_for_every_supplier | passed | 0.14 |  |
| backend.tests.security.test_tools_redteam::test_missing_latest_revenue_is_not_scored_as_a_collapse | passed | 0.04 |  |
| backend.tests.security.test_tools_redteam::test_malformed_external_cell_only_lowers_coverage | passed | 0.04 |  |
| backend.tests.security.test_tools_redteam::test_nan_days_late_gives_consistent_numbers | passed | 0.04 |  |
| backend.tests.security.test_tools_redteam::test_listed_ratings_cite_their_score_evidence | passed | 0.05 |  |
| backend.tests.security.test_tools_redteam::test_cited_evidence_lets_the_verifier_recompute_the_on_time_rate | xfailed | 0.04 |  |
| backend.tests.security.test_tools_redteam::test_supplier_that_stopped_delivering_is_not_hidden_from_the_delivery_score | passed | 0.05 |  |
| backend.tests.security.test_tools_redteam::test_part_description_is_returned_as_untrusted_data | passed | 0.02 |  |
| backend.tests.security.test_tools_redteam::test_buyer_notes_are_capped_before_reaching_the_model | passed | 0.03 |  |
| backend.tests.security.test_tools_redteam::test_a_full_evidence_log_is_a_tool_error | passed | 0.33 |  |
| backend.tests.security.test_tools_redteam::test_non_mapping_arguments_are_a_validation_or_tool_error\[{"supplier_id": "S0003"}\] | passed | 0.01 |  |
| backend.tests.security.test_tools_redteam::test_non_mapping_arguments_are_a_validation_or_tool_error\[None\] | passed | 0.01 |  |
| backend.tests.security.test_tools_redteam::test_non_mapping_arguments_are_a_validation_or_tool_error\[arguments2\] | passed | 0.01 |  |
| backend.tests.security.test_tools_redteam::test_closed_cache_refuses_new_loads | passed | 0.00 |  |
| backend.tests.security.test_tools_redteam::test_slow_external_loads_do_not_block_an_erp_load | passed | 0.08 |  |
| backend.tests.security.test_tools_security::test_every_view_sql_is_one_constant_select_with_explicit_columns\[v_contracts_full\] | passed | 0.00 |  |
| backend.tests.security.test_tools_security::test_every_view_sql_is_one_constant_select_with_explicit_columns\[v_country_indicators\] | passed | 0.00 |  |
| backend.tests.security.test_tools_security::test_every_view_sql_is_one_constant_select_with_explicit_columns\[v_deliveries\] | passed | 0.00 |  |
| backend.tests.security.test_tools_security::test_every_view_sql_is_one_constant_select_with_explicit_columns\[v_open_orders_buyer\] | passed | 0.00 |  |
| backend.tests.security.test_tools_security::test_every_view_sql_is_one_constant_select_with_explicit_columns\[v_open_orders_full\] | passed | 0.00 |  |
| backend.tests.security.test_tools_security::test_every_view_sql_is_one_constant_select_with_explicit_columns\[v_part_sourcing\] | passed | 0.00 |  |
| backend.tests.security.test_tools_security::test_every_view_sql_is_one_constant_select_with_explicit_columns\[v_supplier_revenue\] | passed | 0.00 |  |
| backend.tests.security.test_tools_security::test_every_view_sql_is_one_constant_select_with_explicit_columns\[v_suppliers_buyer\] | passed | 0.00 |  |
| backend.tests.security.test_tools_security::test_every_view_sql_is_one_constant_select_with_explicit_columns\[v_suppliers_full\] | passed | 0.00 |  |
| backend.tests.security.test_tools_security::test_tools_send_only_registered_constant_sql\[list_my_suppliers-arguments0\] | passed | 0.03 |  |
| backend.tests.security.test_tools_security::test_tools_send_only_registered_constant_sql\[list_my_suppliers-arguments1\] | passed | 0.05 |  |
| backend.tests.security.test_tools_security::test_tools_send_only_registered_constant_sql\[supplier_profile-arguments2\] | passed | 0.02 |  |
| backend.tests.security.test_tools_security::test_tools_send_only_registered_constant_sql\[open_orders_at_risk-arguments3\] | passed | 0.03 |  |
| backend.tests.security.test_tools_security::test_tools_send_only_registered_constant_sql\[single_source_parts-arguments4\] | passed | 0.02 |  |
| backend.tests.security.test_tools_security::test_tools_send_only_registered_constant_sql\[delivery_history-arguments5\] | passed | 0.04 |  |
| backend.tests.security.test_tools_security::test_tools_send_only_registered_constant_sql\[financial_trend-arguments6\] | passed | 0.02 |  |
| backend.tests.security.test_tools_security::test_tools_send_only_registered_constant_sql\[country_indicators-arguments7\] | passed | 0.02 |  |
| backend.tests.security.test_tools_security::test_tools_send_only_registered_constant_sql\[weather_extremes-arguments8\] | passed | 0.02 |  |
| backend.tests.security.test_tools_security::test_tools_send_only_registered_constant_sql\[compute_risk_score-arguments9\] | passed | 0.04 |  |
| backend.tests.security.test_tools_security::test_hostile_arguments_never_reach_zetaris\[arguments0-compute_risk_score\] | passed | 0.01 |  |
| backend.tests.security.test_tools_security::test_hostile_arguments_never_reach_zetaris\[arguments0-delivery_history\] | passed | 0.01 |  |
| backend.tests.security.test_tools_security::test_hostile_arguments_never_reach_zetaris\[arguments0-financial_trend\] | passed | 0.01 |  |
| backend.tests.security.test_tools_security::test_hostile_arguments_never_reach_zetaris\[arguments0-open_orders_at_risk\] | passed | 0.01 |  |
| backend.tests.security.test_tools_security::test_hostile_arguments_never_reach_zetaris\[arguments0-single_source_parts\] | passed | 0.01 |  |
| backend.tests.security.test_tools_security::test_hostile_arguments_never_reach_zetaris\[arguments0-supplier_profile\] | passed | 0.00 |  |
| backend.tests.security.test_tools_security::test_hostile_arguments_never_reach_zetaris\[arguments0-weather_extremes\] | passed | 0.00 |  |
| backend.tests.security.test_tools_security::test_hostile_arguments_never_reach_zetaris\[arguments1-compute_risk_score\] | passed | 0.01 |  |
| backend.tests.security.test_tools_security::test_hostile_arguments_never_reach_zetaris\[arguments1-delivery_history\] | passed | 0.01 |  |
| backend.tests.security.test_tools_security::test_hostile_arguments_never_reach_zetaris\[arguments1-financial_trend\] | passed | 0.01 |  |
| backend.tests.security.test_tools_security::test_hostile_arguments_never_reach_zetaris\[arguments1-open_orders_at_risk\] | passed | 0.01 |  |
| backend.tests.security.test_tools_security::test_hostile_arguments_never_reach_zetaris\[arguments1-single_source_parts\] | passed | 0.00 |  |
| backend.tests.security.test_tools_security::test_hostile_arguments_never_reach_zetaris\[arguments1-supplier_profile\] | passed | 0.00 |  |
| backend.tests.security.test_tools_security::test_hostile_arguments_never_reach_zetaris\[arguments1-weather_extremes\] | passed | 0.00 |  |
| backend.tests.security.test_tools_security::test_hostile_arguments_never_reach_zetaris\[arguments2-compute_risk_score\] | passed | 0.00 |  |
| backend.tests.security.test_tools_security::test_hostile_arguments_never_reach_zetaris\[arguments2-delivery_history\] | passed | 0.01 |  |
| backend.tests.security.test_tools_security::test_hostile_arguments_never_reach_zetaris\[arguments2-financial_trend\] | passed | 0.01 |  |
| backend.tests.security.test_tools_security::test_hostile_arguments_never_reach_zetaris\[arguments2-open_orders_at_risk\] | passed | 0.00 |  |
| backend.tests.security.test_tools_security::test_hostile_arguments_never_reach_zetaris\[arguments2-single_source_parts\] | passed | 0.00 |  |
| backend.tests.security.test_tools_security::test_hostile_arguments_never_reach_zetaris\[arguments2-supplier_profile\] | passed | 0.00 |  |
| backend.tests.security.test_tools_security::test_hostile_arguments_never_reach_zetaris\[arguments2-weather_extremes\] | passed | 0.00 |  |
| backend.tests.security.test_tools_security::test_hostile_arguments_never_reach_zetaris\[arguments3-compute_risk_score\] | passed | 0.00 |  |
| backend.tests.security.test_tools_security::test_hostile_arguments_never_reach_zetaris\[arguments3-delivery_history\] | passed | 0.01 |  |
| backend.tests.security.test_tools_security::test_hostile_arguments_never_reach_zetaris\[arguments3-financial_trend\] | passed | 0.01 |  |
| backend.tests.security.test_tools_security::test_hostile_arguments_never_reach_zetaris\[arguments3-open_orders_at_risk\] | passed | 0.00 |  |
| backend.tests.security.test_tools_security::test_hostile_arguments_never_reach_zetaris\[arguments3-single_source_parts\] | passed | 0.00 |  |
| backend.tests.security.test_tools_security::test_hostile_arguments_never_reach_zetaris\[arguments3-supplier_profile\] | passed | 0.00 |  |
| backend.tests.security.test_tools_security::test_hostile_arguments_never_reach_zetaris\[arguments3-weather_extremes\] | passed | 0.00 |  |
| backend.tests.security.test_tools_security::test_hostile_arguments_never_reach_zetaris\[arguments4-compute_risk_score\] | passed | 0.00 |  |
| backend.tests.security.test_tools_security::test_hostile_arguments_never_reach_zetaris\[arguments4-delivery_history\] | passed | 0.01 |  |
| backend.tests.security.test_tools_security::test_hostile_arguments_never_reach_zetaris\[arguments4-financial_trend\] | passed | 0.01 |  |
| backend.tests.security.test_tools_security::test_hostile_arguments_never_reach_zetaris\[arguments4-open_orders_at_risk\] | passed | 0.00 |  |
| backend.tests.security.test_tools_security::test_hostile_arguments_never_reach_zetaris\[arguments4-single_source_parts\] | passed | 0.00 |  |
| backend.tests.security.test_tools_security::test_hostile_arguments_never_reach_zetaris\[arguments4-supplier_profile\] | passed | 0.00 |  |
| backend.tests.security.test_tools_security::test_hostile_arguments_never_reach_zetaris\[arguments4-weather_extremes\] | passed | 0.00 |  |
| backend.tests.security.test_tools_security::test_hostile_arguments_never_reach_zetaris\[arguments5-compute_risk_score\] | passed | 0.01 |  |
| backend.tests.security.test_tools_security::test_hostile_arguments_never_reach_zetaris\[arguments5-delivery_history\] | passed | 0.01 |  |
| backend.tests.security.test_tools_security::test_hostile_arguments_never_reach_zetaris\[arguments5-financial_trend\] | passed | 0.01 |  |
| backend.tests.security.test_tools_security::test_hostile_arguments_never_reach_zetaris\[arguments5-open_orders_at_risk\] | passed | 0.01 |  |
| backend.tests.security.test_tools_security::test_hostile_arguments_never_reach_zetaris\[arguments5-single_source_parts\] | passed | 0.00 |  |
| backend.tests.security.test_tools_security::test_hostile_arguments_never_reach_zetaris\[arguments5-supplier_profile\] | passed | 0.00 |  |
| backend.tests.security.test_tools_security::test_hostile_arguments_never_reach_zetaris\[arguments5-weather_extremes\] | passed | 0.00 |  |
| backend.tests.security.test_tools_security::test_buyer_requests_use_only_the_buyer_key_and_buyer_views | passed | 0.05 |  |
| backend.tests.security.test_tools_security::test_buyer_never_sees_rows_of_another_category | passed | 0.04 |  |
| backend.tests.security.test_tools_security::test_buyer_notes_injection_stays_quoted_untrusted_data | passed | 0.02 |  |
| backend.tests.security.test_tools_security::test_financial_trend_is_refused_for_role\[buyer\] | passed | 0.01 |  |
| backend.tests.security.test_tools_security::test_a_buyer_session_without_a_category_is_refused | passed | 0.00 |  |
| backend.tests.security.test_zetaris_security::test_refuses_to_start_with_only_the_admin_key\[buyer\] | passed | 0.00 |  |
| backend.tests.security.test_zetaris_security::test_refuses_to_start_with_only_the_admin_key\[risk_manager\] | passed | 0.00 |  |
| backend.tests.security.test_zetaris_security::test_refuses_to_start_with_only_the_admin_key\[director\] | passed | 0.00 |  |
| backend.tests.security.test_zetaris_security::test_refuses_a_role_key_that_is_the_admin_key\[buyer-zetaris_key_buyer\] | passed | 0.00 |  |
| backend.tests.security.test_zetaris_security::test_refuses_a_role_key_that_is_the_admin_key\[risk_manager-zetaris_key_risk\] | passed | 0.00 |  |
| backend.tests.security.test_zetaris_security::test_buyer_and_risk_keys_must_differ | passed | 0.00 |  |
| backend.tests.security.test_zetaris_security::test_auditor_cannot_build_a_client | passed | 0.00 |  |
| backend.tests.security.test_zetaris_security::test_repr_hides_the_key | passed | 0.01 |  |
| backend.tests.security.test_zetaris_security::test_refuses_anything_but_one_plain_select\[DROP VIEW risk.v_suppliers_full\] | passed | 0.02 |  |
| backend.tests.security.test_zetaris_security::test_refuses_anything_but_one_plain_select\[CREATE SCHEMASTORE VIEW x WITH CONTAINER risk AS SELECT 1\] | passed | 0.02 |  |
| backend.tests.security.test_zetaris_security::test_refuses_anything_but_one_plain_select\[GRANT SELECT ON VIEW TABLE risk.v_contracts_full TO ROLE 'buyer'\] | passed | 0.02 |  |
| backend.tests.security.test_zetaris_security::test_refuses_anything_but_one_plain_select\[INSERT INTO erp_pg.suppliers VALUES (1)\] | passed | 0.02 |  |
| backend.tests.security.test_zetaris_security::test_refuses_anything_but_one_plain_select\[SELECT 1; DROP VIEW risk.v_suppliers_full\] | passed | 0.02 |  |
| backend.tests.security.test_zetaris_security::test_refuses_anything_but_one_plain_select\[SELECT 1;\] | passed | 0.02 |  |
| backend.tests.security.test_zetaris_security::test_refuses_anything_but_one_plain_select\[ \] | passed | 0.02 |  |
| backend.tests.security.test_zetaris_security::test_refuses_anything_but_one_plain_select\[SHOW DATASOURCES\] | passed | 0.02 |  |
| backend.tests.security.test_zetaris_security::test_refuses_anything_but_one_plain_select\[select 1 -- trailing comment\] | passed | 0.02 |  |
| backend.tests.security.test_zetaris_security::test_refuses_anything_but_one_plain_select\[SELECT 1 /* block */\] | passed | 0.02 |  |
| backend.tests.security.test_zetaris_security::test_refuses_oversized_sql | passed | 0.02 |  |
| backend.tests.security.test_zetaris_security::test_errors_never_carry_the_upstream_body_or_key\[400\] | passed | 0.02 |  |
| backend.tests.security.test_zetaris_security::test_errors_never_carry_the_upstream_body_or_key\[401\] | passed | 0.02 |  |
| backend.tests.security.test_zetaris_security::test_errors_never_carry_the_upstream_body_or_key\[500\] | passed | 0.02 |  |
| backend.tests.security.test_zetaris_security::test_errors_never_carry_the_upstream_body_or_key\[503\] | passed | 0.02 |  |
| backend.tests.security.test_zetaris_security::test_redirects_are_not_followed | passed | 0.02 |  |
| backend.tests.security.test_zetaris_security::test_oversized_response_is_refused | passed | 0.02 |  |
| backend.tests.security.test_zetaris_security::test_plain_http_to_a_remote_zetaris_is_refused | passed | 0.00 |  |
| backend.tests.security.test_zetaris_security::test_runtime_settings_have_no_admin_key | xfailed | 0.00 |  |
| backend.tests.security.test_zetaris_security::test_admin_key_with_surrounding_space_is_still_refused | xfailed | 0.01 |  |
| backend.tests.security.test_zetaris_security::test_200_without_data_is_a_bad_response\[body0\] | passed | 0.02 |  |
| backend.tests.security.test_zetaris_security::test_200_without_data_is_a_bad_response\[body1\] | passed | 0.02 |  |
| backend.tests.security.test_zetaris_security::test_200_without_data_is_a_bad_response\[body2\] | passed | 0.02 |  |
| backend.tests.security.test_zetaris_security::test_200_without_data_is_a_bad_response\[body3\] | passed | 0.02 |  |
| backend.tests.security.test_zetaris_security::test_200_without_data_is_a_bad_response\[body4\] | passed | 0.02 |  |
| backend.tests.security.test_zetaris_security::test_requests_uncompressed_responses | xfailed | 0.02 |  |
| backend.tests.security.test_zetaris_security::test_guard_refuses_writes_reflection_star_and_unicode_separators\[WITH t AS (SELECT 1) INSERT INTO risk.x SELECT * FROM t\] | xfailed | 0.02 |  |
| backend.tests.security.test_zetaris_security::test_guard_refuses_writes_reflection_star_and_unicode_separators\[SELECT java_method('java.lang.Thread', 'sleep', 600000L)\] | xfailed | 0.02 |  |
| backend.tests.security.test_zetaris_security::test_guard_refuses_writes_reflection_star_and_unicode_separators\[SELECT reflect('java.lang.System', 'exit', 0)\] | xfailed | 0.02 |  |
| backend.tests.security.test_zetaris_security::test_guard_refuses_writes_reflection_star_and_unicode_separators\[SELECT * FROM erp_pg.contracts\] | xfailed | 0.02 |  |
| backend.tests.security.test_zetaris_security::test_guard_refuses_writes_reflection_star_and_unicode_separators\[SELECT 1 \\uff1b DROP VIEW risk.v_deliveries\] | xfailed | 0.02 |  |
| backend.tests.security.test_zetaris_security::test_guard_refuses_writes_reflection_star_and_unicode_separators\[SELECT 1 \\u037e DROP VIEW risk.v_deliveries\] | xfailed | 0.02 |  |
| backend.tests.security.test_zetaris_security::test_supplier_name_cannot_carry_a_newline | xfailed | 0.00 |  |
| backend.tests.security.test_zetaris_security::test_undecodable_body_becomes_a_zetaris_error | xfailed | 0.01 |  |
| backend.tests.security.test_zetaris_security::test_failed_query_log_keeps_the_stack_trace | xfailed | 0.02 |  |
| backend.tests.security.test_zetaris_security::test_numeric_cells_keep_their_exact_text | xfailed | 0.02 |  |
| backend.tests.security.test_zetaris_security::test_rest_url_with_credentials_is_refused | xfailed | 0.02 |  |
| backend.tests.security.test_zetaris_security::test_verify_does_not_echo_untrusted_cells | xfailed | 0.00 |  |
| backend.tests.unit.test_api_app::test_accepts_and_returns_a_valid_request_id | passed | 0.01 |  |
| backend.tests.unit.test_api_app::test_creates_a_request_id_when_missing | passed | 0.01 |  |
| backend.tests.unit.test_api_app::test_replaces_invalid_request_ids\[xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\] | passed | 0.01 |  |
| backend.tests.unit.test_api_app::test_replaces_invalid_request_ids\[has space\] | passed | 0.01 |  |
| backend.tests.unit.test_api_app::test_replaces_invalid_request_ids\[line\\\\nbreak\] | passed | 0.01 |  |
| backend.tests.unit.test_api_app::test_replaces_invalid_request_ids\[semi;colon\] | passed | 0.01 |  |
| backend.tests.unit.test_api_app::test_replaces_invalid_request_ids\[\<script\>\] | passed | 0.01 |  |
| backend.tests.unit.test_api_app::test_request_ids_differ_between_requests | passed | 0.01 |  |
| backend.tests.unit.test_api_app::test_logs_one_info_line_per_request | passed | 0.01 |  |
| backend.tests.unit.test_api_app::test_access_log_has_request_id_in_json | passed | 0.01 |  |
| backend.tests.unit.test_api_app::test_logs_status_500_for_unhandled_errors | passed | 0.01 |  |
| backend.tests.unit.test_api_app::test_health_is_always_ok | passed | 0.01 |  |
| backend.tests.unit.test_api_app::test_health_stays_ok_when_a_readiness_check_fails | passed | 0.01 |  |
| backend.tests.unit.test_api_app::test_ready_is_ok_with_no_checks | passed | 0.01 |  |
| backend.tests.unit.test_api_app::test_ready_runs_registered_checks | passed | 0.01 |  |
| backend.tests.unit.test_api_app::test_ready_returns_503_naming_only_failed_checks | passed | 0.01 |  |
| backend.tests.unit.test_api_app::test_ready_treats_exception_as_failed_without_leaking_it | passed | 0.01 |  |
| backend.tests.unit.test_api_app::test_ready_treats_a_slow_check_as_failed | passed | 0.06 |  |
| backend.tests.unit.test_api_app::test_docs_available_in_dev | passed | 0.01 |  |
| backend.tests.unit.test_api_app::test_docs_hidden_in_prod | passed | 0.04 |  |
| backend.tests.unit.test_api_app::test_small_body_is_accepted | passed | 0.01 |  |
| backend.tests.unit.test_api_app::test_body_over_16kb_is_rejected_by_content_length | passed | 0.01 |  |
| backend.tests.unit.test_api_app::test_body_exactly_16kb_is_accepted | passed | 0.01 |  |
| backend.tests.unit.test_api_app::test_chunked_body_over_limit_is_rejected | passed | 0.01 |  |
| backend.tests.unit.test_api_app::test_invalid_content_length_is_a_bad_request | passed | 0.01 |  |
| backend.tests.unit.test_architecture::test_real_code_follows_the_layer_rule | passed | 0.07 |  |
| backend.tests.unit.test_architecture::test_downward_imports_are_allowed | passed | 0.00 |  |
| backend.tests.unit.test_architecture::test_flags_api_importing_zetaris | passed | 0.00 |  |
| backend.tests.unit.test_architecture::test_flags_zetaris_import_through_package_name | passed | 0.00 |  |
| backend.tests.unit.test_architecture::test_flags_backend_prefixed_import | passed | 0.00 |  |
| backend.tests.unit.test_architecture::test_flags_relative_import_of_zetaris | passed | 0.00 |  |
| backend.tests.unit.test_architecture::test_flags_lower_layer_importing_higher | passed | 0.00 |  |
| backend.tests.unit.test_architecture::test_flags_cross_cutting_package_importing_a_layer | passed | 0.00 |  |
| backend.tests.unit.test_architecture::test_flags_top_level_module_importing_zetaris | passed | 0.00 |  |
| backend.tests.unit.test_architecture::test_zetaris_may_not_import_tools | passed | 0.00 |  |
| backend.tests.unit.test_architecture::test_tools_and_zetaris_may_not_import_the_model_client | passed | 0.00 |  |
| backend.tests.unit.test_architecture::test_model_client_may_only_import_core | passed | 0.00 |  |
| backend.tests.unit.test_architecture::test_memory_may_use_the_model_client | passed | 0.00 |  |
| backend.tests.unit.test_check_llm::test_all_checks_pass_against_a_healthy_runtime | passed | 0.03 |  |
| backend.tests.unit.test_check_llm::test_chat_checks_are_skipped_when_runtime_is_offline | passed | 0.03 |  |
| backend.tests.unit.test_check_llm::test_routed_model_mismatch_fails_the_plain_text_check | passed | 0.04 |  |
| backend.tests.unit.test_check_llm::test_format_result | passed | 0.00 |  |
| backend.tests.unit.test_check_llm::test_main_prints_results_without_secrets_and_exits_zero | passed | 0.04 |  |
| backend.tests.unit.test_check_llm::test_main_exits_one_on_failure | passed | 0.03 |  |
| backend.tests.unit.test_check_llm::test_every_chat_prompt_carries_a_fresh_run_id | passed | 0.03 |  |
| backend.tests.unit.test_context_compose::test_has_no_host_port | passed | 0.01 |  |
| backend.tests.unit.test_context_compose::test_is_only_on_the_internal_network | passed | 0.00 |  |
| backend.tests.unit.test_context_compose::test_data_lives_in_a_named_volume | passed | 0.00 |  |
| backend.tests.unit.test_context_compose::test_has_a_healthcheck_on_health | passed | 0.00 |  |
| backend.tests.unit.test_context_compose::test_has_memory_and_cpu_limits | passed | 0.00 |  |
| backend.tests.unit.test_context_compose::test_runs_hardened | passed | 0.00 |  |
| backend.tests.unit.test_context_compose::test_token_comes_from_env_and_is_required | passed | 0.00 |  |
| backend.tests.unit.test_context_compose::test_dev_override_publishes_localhost_only | passed | 0.00 |  |
| backend.tests.unit.test_context_compose::test_env_example_has_an_empty_token_placeholder | passed | 0.00 |  |
| backend.tests.unit.test_context_compose::test_image_is_pinned_by_digest_and_runs_as_non_root | passed | 0.00 |  |
| backend.tests.unit.test_context_compose::test_has_a_process_limit_and_a_tolerant_healthcheck | passed | 0.00 |  |
| backend.tests.unit.test_context_compose::test_runtime_image_ships_no_package_manager | passed | 0.00 |  |
| backend.tests.unit.test_erp_compose::test_uses_postgres_16 | passed | 0.01 |  |
| backend.tests.unit.test_erp_compose::test_port_is_bound_to_localhost_only | passed | 0.00 |  |
| backend.tests.unit.test_erp_compose::test_data_lives_in_a_named_volume | passed | 0.01 |  |
| backend.tests.unit.test_erp_compose::test_init_scripts_are_mounted_read_only | passed | 0.00 |  |
| backend.tests.unit.test_erp_compose::test_has_a_healthcheck | passed | 0.00 |  |
| backend.tests.unit.test_erp_compose::test_passwords_come_from_env_variables_and_are_required | passed | 0.00 |  |
| backend.tests.unit.test_erp_compose::test_env_example_has_placeholders_without_values | passed | 0.00 |  |
| backend.tests.unit.test_erp_compose::test_reader_role_grants_select_only | passed | 0.00 |  |
| backend.tests.unit.test_erp_compose::test_reader_password_is_passed_as_a_psql_variable_not_in_the_script | passed | 0.00 |  |
| backend.tests.unit.test_erp_compose::test_generated_sql_is_git_ignored | passed | 0.00 |  |
| backend.tests.unit.test_erp_compose::test_reader_cannot_use_large_object_write_functions | passed | 0.00 |  |
| backend.tests.unit.test_erp_compose::test_reader_cannot_create_temp_tables | passed | 0.00 |  |
| backend.tests.unit.test_erp_compose::test_reader_has_timeouts_and_a_connection_limit | passed | 0.00 |  |
| backend.tests.unit.test_erp_compose::test_container_has_memory_and_cpu_limits | passed | 0.00 |  |
| backend.tests.unit.test_erp_generate::test_same_seed_gives_identical_rows | passed | 0.08 |  |
| backend.tests.unit.test_erp_generate::test_different_seed_gives_different_rows | passed | 0.04 |  |
| backend.tests.unit.test_erp_generate::test_default_seed_is_42 | passed | 0.04 |  |
| backend.tests.unit.test_erp_generate::test_written_files_are_byte_identical_across_runs | passed | 0.12 |  |
| backend.tests.unit.test_erp_generate::test_today_defaults_to_the_run_date_on_the_command_line | passed | 0.00 |  |
| backend.tests.unit.test_erp_generate::test_every_table_has_exactly_the_documented_columns | passed | 0.00 |  |
| backend.tests.unit.test_erp_generate::test_suppliers_come_from_the_yaml | passed | 0.01 |  |
| backend.tests.unit.test_erp_generate::test_foreign_keys_resolve | passed | 0.00 |  |
| backend.tests.unit.test_erp_generate::test_primary_keys_are_unique | passed | 0.00 |  |
| backend.tests.unit.test_erp_generate::test_every_supplier_has_a_contract_and_orders | passed | 0.00 |  |
| backend.tests.unit.test_erp_generate::test_sourcing_shares_sum_to_100_and_single_source_flag_matches | passed | 0.00 |  |
| backend.tests.unit.test_erp_generate::test_purchase_order_values_are_in_range | passed | 0.00 |  |
| backend.tests.unit.test_erp_generate::test_status_follows_due_date | passed | 0.00 |  |
| backend.tests.unit.test_erp_generate::test_deliveries_match_their_orders | passed | 0.00 |  |
| backend.tests.unit.test_erp_generate::test_contract_values_are_in_range | passed | 0.00 |  |
| backend.tests.unit.test_erp_generate::test_story_1a_microchip_has_rising_late_deliveries | passed | 0.00 |  |
| backend.tests.unit.test_erp_generate::test_story_1a_supplier_is_public | passed | 0.00 |  |
| backend.tests.unit.test_erp_generate::test_story_1b_celanese_is_public_and_sited_in_the_worsening_country | passed | 0.00 |  |
| backend.tests.unit.test_erp_generate::test_signal_overdue_open_orders | passed | 0.00 |  |
| backend.tests.unit.test_erp_generate::test_signal_one_contract_already_ended | passed | 0.00 |  |
| backend.tests.unit.test_erp_generate::test_story_2_exactly_one_single_source_a_part_with_a_large_open_order | passed | 0.00 |  |
| backend.tests.unit.test_erp_generate::test_story_3_supplier_sits_in_the_worsening_country | passed | 0.00 |  |
| backend.tests.unit.test_erp_generate::test_story_3_country_is_a_single_parameter | passed | 0.04 |  |
| backend.tests.unit.test_erp_generate::test_moving_story_3_does_not_change_other_suppliers | passed | 0.04 |  |
| backend.tests.unit.test_erp_generate::test_story_3_rejects_non_eu_site_countries\[US\] | passed | 0.00 |  |
| backend.tests.unit.test_erp_generate::test_story_3_rejects_non_eu_site_countries\[cz\] | passed | 0.00 |  |
| backend.tests.unit.test_erp_generate::test_story_3_rejects_non_eu_site_countries\[\] | passed | 0.00 |  |
| backend.tests.unit.test_erp_generate::test_story_3_rejects_non_eu_site_countries\[XX\] | passed | 0.00 |  |
| backend.tests.unit.test_erp_generate::test_story_4_exactly_one_buyer_note_has_the_prompt_injection | passed | 0.00 |  |
| backend.tests.unit.test_erp_generate::test_write_outputs_creates_schema_seed_and_csvs | passed | 0.04 |  |
| backend.tests.unit.test_erp_generate::test_seed_escapes_quotes_in_text | passed | 0.04 |  |
| backend.tests.unit.test_erp_generate::test_csv_round_trips_row_counts | passed | 0.04 |  |
| backend.tests.unit.test_erp_generate::test_data_card_documents_each_story_and_the_synthetic_labels | passed | 0.00 |  |
| backend.tests.unit.test_erp_generate::test_data_card_states_story_1_with_the_real_sec_numbers_and_rules | passed | 0.00 |  |
| backend.tests.unit.test_erp_generate::test_data_contract_records_every_cik_check | passed | 0.01 |  |
| backend.tests.unit.test_erp_generate::test_schema_sql_declares_every_column | passed | 0.00 |  |
| backend.tests.unit.test_erp_generate::test_main_writes_all_files | passed | 0.08 |  |
| backend.tests.unit.test_erp_suppliers_yaml::test_has_twelve_suppliers_with_unique_sequential_ids | passed | 0.01 |  |
| backend.tests.unit.test_erp_suppliers_yaml::test_eight_public_and_four_private_suppliers | passed | 0.01 |  |
| backend.tests.unit.test_erp_suppliers_yaml::test_every_category_and_only_eu_site_countries_are_used | passed | 0.01 |  |
| backend.tests.unit.test_erp_suppliers_yaml::test_public_suppliers_have_a_revenue_tag_and_private_ones_do_not | passed | 0.01 |  |
| backend.tests.unit.test_erp_suppliers_yaml::test_ciks_are_unique_ten_digit_strings | passed | 0.01 |  |
| backend.tests.unit.test_erp_suppliers_yaml::test_rejects_invalid_supplier\[changes0\] | passed | 0.00 |  |
| backend.tests.unit.test_erp_suppliers_yaml::test_rejects_invalid_supplier\[changes1\] | passed | 0.00 |  |
| backend.tests.unit.test_erp_suppliers_yaml::test_rejects_invalid_supplier\[changes2\] | passed | 0.00 |  |
| backend.tests.unit.test_erp_suppliers_yaml::test_rejects_invalid_supplier\[changes3\] | passed | 0.00 |  |
| backend.tests.unit.test_erp_suppliers_yaml::test_rejects_invalid_supplier\[changes4\] | passed | 0.00 |  |
| backend.tests.unit.test_erp_suppliers_yaml::test_rejects_invalid_supplier\[changes5\] | passed | 0.00 |  |
| backend.tests.unit.test_erp_suppliers_yaml::test_rejects_invalid_supplier\[changes6\] | passed | 0.00 |  |
| backend.tests.unit.test_erp_suppliers_yaml::test_rejects_invalid_supplier\[changes7\] | passed | 0.00 |  |
| backend.tests.unit.test_erp_suppliers_yaml::test_rejects_invalid_supplier\[changes8\] | passed | 0.00 |  |
| backend.tests.unit.test_erp_suppliers_yaml::test_rejects_invalid_supplier\[changes9\] | passed | 0.00 |  |
| backend.tests.unit.test_erp_suppliers_yaml::test_rejects_invalid_supplier\[changes10\] | passed | 0.00 |  |
| backend.tests.unit.test_erp_suppliers_yaml::test_rejects_invalid_supplier\[changes11\] | passed | 0.00 |  |
| backend.tests.unit.test_erp_suppliers_yaml::test_rejects_invalid_supplier\[changes12\] | passed | 0.00 |  |
| backend.tests.unit.test_erp_suppliers_yaml::test_rejects_invalid_supplier\[changes13\] | passed | 0.00 |  |
| backend.tests.unit.test_erp_suppliers_yaml::test_rejects_duplicate_ids | passed | 0.00 |  |
| backend.tests.unit.test_erp_suppliers_yaml::test_rejects_unknown_fields | passed | 0.00 |  |
| backend.tests.unit.test_erp_suppliers_yaml::test_site_coordinates_are_optional | passed | 0.00 |  |
| backend.tests.unit.test_erp_suppliers_yaml::test_story_3_supplier_is_in_france_at_a_real_industrial_site | passed | 0.01 |  |
| backend.tests.unit.test_errors::test_unexpected_error_returns_generic_500 | passed | 0.01 |  |
| backend.tests.unit.test_errors::test_unexpected_error_is_logged_with_exception | passed | 0.01 |  |
| backend.tests.unit.test_errors::test_app_error_uses_its_status_code_and_message | passed | 0.01 |  |
| backend.tests.unit.test_errors::test_unknown_route_uses_error_model | passed | 0.01 |  |
| backend.tests.unit.test_errors::test_wrong_method_uses_error_model | passed | 0.01 |  |
| backend.tests.unit.test_errors::test_validation_error_uses_model_without_echoing_input | passed | 0.01 |  |
| backend.tests.unit.test_errors::test_http_exception_detail_is_not_echoed | passed | 0.01 |  |
| backend.tests.unit.test_llm_budget::test_default_limit_is_15 | passed | 0.00 |  |
| backend.tests.unit.test_llm_budget::test_counts_used_and_remaining | passed | 0.00 |  |
| backend.tests.unit.test_llm_budget::test_raises_when_exhausted_without_counting_more | passed | 0.00 |  |
| backend.tests.unit.test_llm_budget::test_warns_once_at_80_percent | passed | 0.00 |  |
| backend.tests.unit.test_llm_budget::test_exhaustion_is_logged_as_warning | passed | 0.00 |  |
| backend.tests.unit.test_llm_budget::test_rejects_non_positive_limit\[0\] | passed | 0.00 |  |
| backend.tests.unit.test_llm_budget::test_rejects_non_positive_limit\[-1\] | passed | 0.00 |  |
| backend.tests.unit.test_llm_client_chat::test_plain_text_returns_string_with_receipt | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_request_keeps_plain_string_content_and_omits_unset_options | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_sends_auth_app_and_user_scope_headers | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_uses_the_180_second_timeout | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_role_selects_the_configured_model\[default-meterless_model\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_role_selects_the_configured_model\[deep-meterless_deep_model\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_role_selects_the_configured_model\[fast-meterless_fast_model\] | passed | 0.03 |  |
| backend.tests.unit.test_llm_client_chat::test_rejects_unknown_role_without_a_request | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_rejects_empty_or_oversized_message_lists\[messages0\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_rejects_empty_or_oversized_message_lists\[messages1\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_passes_tools_tool_choice_and_response_format | passed | 0.03 |  |
| backend.tests.unit.test_llm_client_chat::test_strips_reasoning_up_to_think_end\[\<think\>secret plan\</think\>Final\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_strips_reasoning_up_to_think_end\[no opener, still reasoning\</think\>\\nFinal\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_parses_tool_calls_and_tolerates_null_content | passed | 0.03 |  |
| backend.tests.unit.test_llm_client_chat::test_invalid_tool_arguments_raise_typed_error | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_missing_receipt_is_tolerated | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_malformed_receipt_is_tolerated\[oops\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_malformed_receipt_is_tolerated\[receipt1\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_malformed_receipt_is_tolerated\[receipt2\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_malformed_receipt_is_tolerated\[receipt3\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_routed_model_comes_from_the_receipt | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_invalid_usage_falls_back_to_zero | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_bad_response_shape_raises\[not json\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_bad_response_shape_raises\[\[\]\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_bad_response_shape_raises\[{"choices": \[\]}\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_bad_response_shape_raises\[{"choices": \[{"message": "x"}\]}\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_bad_response_shape_raises\[{"choices": \[{"message": {"content": \[{"type": "text"}\]}}\]}\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_retries_once_then_succeeds\[429\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_retries_once_then_succeeds\[500\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_retries_once_then_succeeds\[502\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_retries_once_then_succeeds\[503\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_retries_once_then_succeeds\[504\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_retries_on_timeouts_and_transport_errors\[error0\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_retries_on_timeouts_and_transport_errors\[error1\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_retries_on_timeouts_and_transport_errors\[error2\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_backoff_doubles_between_attempts | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_backoff_adds_jitter | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_backoff_is_capped | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_honours_retry_after_seconds_with_a_cap\[7-7.0\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_honours_retry_after_seconds_with_a_cap\[0-0.0\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_honours_retry_after_seconds_with_a_cap\[3600-60.0\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_honours_retry_after_http_date | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_unusable_retry_after_falls_back_to_backoff\[soon\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_unusable_retry_after_falls_back_to_backoff\[-5\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_unusable_retry_after_falls_back_to_backoff\[\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_gives_up_after_four_attempts_on_5xx | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_gives_up_after_four_attempts_on_timeouts | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_gives_up_after_four_attempts_on_429 | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_unauthorized_is_not_retried\[401\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_unauthorized_is_not_retried\[403\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_other_non_success_codes_are_not_retried\[302\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_other_non_success_codes_are_not_retried\[400\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_other_non_success_codes_are_not_retried\[404\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_other_non_success_codes_are_not_retried\[422\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_deep_falls_back_to_default_once_on_persistent_429 | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_deep_does_not_fall_back_on_5xx | passed | 0.03 |  |
| backend.tests.unit.test_llm_client_chat::test_fast_does_not_fall_back_on_429 | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_fallback_happens_only_once | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_budget_counts_one_per_call_even_with_retries_and_fallback | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_exhausted_budget_blocks_the_request | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_every_attempt_goes_through_the_rate_limiter | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_truncated_answer_raises_bad_response | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_oversized_response_body_raises_bad_response | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_rejects_too_many_tools_without_a_request | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_stops_retrying_when_the_wait_would_pass_the_deadline | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_attempt_timeout_is_retried | passed | 0.07 |  |
| backend.tests.unit.test_llm_client_chat::test_records_cache_status_from_the_receipt\[cache0-exact-True\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_records_cache_status_from_the_receipt\[cache1-miss-False\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_records_cache_status_from_the_receipt\[None-None-False\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_records_cache_status_from_the_receipt\[exact-None-False\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_records_cache_status_from_the_receipt\[cache4-None-False\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_routed_model_falls_back_to_the_winning_route | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_empty_reply_is_retried_once_with_a_fresh_request_id\[empty0-tools0\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_empty_reply_is_retried_once_with_a_fresh_request_id\[empty1-tools1\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_empty_reply_is_retried_once_with_a_fresh_request_id\[empty2-None\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_each_empty_retry_uses_a_new_request_id | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_empty_reply_twice_raises_bad_response | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_text_reply_without_tools_is_not_treated_as_empty\[body0\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_text_reply_without_tools_is_not_treated_as_empty\[body1\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_embed::test_default_batch_size_is_32 | passed | 0.00 |  |
| backend.tests.unit.test_llm_client_embed::test_returns_vectors_in_input_order | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_embed::test_sends_batches_with_the_embed_model_and_no_scope | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_embed::test_empty_input_makes_no_request | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_embed::test_malformed_embeddings_raise_bad_response\[body0\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_embed::test_malformed_embeddings_raise_bad_response\[body1\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_embed::test_malformed_embeddings_raise_bad_response\[body2\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_embed::test_malformed_embeddings_raise_bad_response\[body3\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_embed::test_malformed_embeddings_raise_bad_response\[body4\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_embed::test_rejects_oversized_text_without_a_request | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_embed::test_retries_on_429 | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_embed::test_dimensions_must_match_across_batches | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_embed::test_oversized_vectors_are_rejected | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_logging::test_one_info_line_per_call_with_receipt_fields | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_logging::test_retry_is_logged_as_warning | passed | 0.03 |  |
| backend.tests.unit.test_llm_client_logging::test_fallback_is_logged_as_warning | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_logging::test_final_failure_is_logged_as_error | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_logging::test_status_is_logged | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_logging::test_embed_call_is_logged | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_logging::test_never_logs_prompts_contents_tool_arguments_or_token | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_logging::test_model_call_logs_cache_status | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_logging::test_empty_reply_retry_is_logged_as_warning | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_status::test_online_lists_all_model_ids_including_unknown_ones | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_status::test_returns_unauthorized_on_401_and_403\[401\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_status::test_returns_unauthorized_on_401_and_403\[403\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_status::test_returns_offline_on_other_status_codes\[404\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_status::test_returns_offline_on_other_status_codes\[429\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_status::test_returns_offline_on_other_status_codes\[500\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_status::test_returns_offline_on_other_status_codes\[502\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_status::test_returns_offline_when_unreachable\[error0\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_status::test_returns_offline_when_unreachable\[error1\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_status::test_returns_offline_when_unreachable\[error2\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_status::test_returns_offline_when_200_has_no_model_ids\[not json\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_status::test_returns_offline_when_200_has_no_model_ids\[\[\]\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_status::test_returns_offline_when_200_has_no_model_ids\[{"data": \[\]}\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_status::test_returns_offline_when_200_has_no_model_ids\[{"data": "x"}\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_status::test_returns_offline_when_200_has_no_model_ids\[{"data": \[{"name": "no id"}\]}\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_status::test_sends_auth_and_app_headers_but_no_scope | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_status::test_uses_the_short_status_timeout | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_status::test_calls_the_normalised_models_url | passed | 0.02 |  |
| backend.tests.unit.test_llm_rate_limiter::test_defaults_match_the_free_tier_headroom | passed | 0.00 |  |
| backend.tests.unit.test_llm_rate_limiter::test_allows_max_calls_without_waiting | passed | 0.00 |  |
| backend.tests.unit.test_llm_rate_limiter::test_waits_until_the_oldest_call_leaves_the_window | passed | 0.00 |  |
| backend.tests.unit.test_llm_rate_limiter::test_window_frees_after_the_period | passed | 0.00 |  |
| backend.tests.unit.test_llm_rate_limiter::test_limits_concurrent_calls_to_two | passed | 0.03 |  |
| backend.tests.unit.test_llm_rate_limiter::test_releases_slot_when_the_call_fails | passed | 0.00 |  |
| backend.tests.unit.test_llm_rate_limiter::test_shared_instance_is_reused | passed | 0.00 |  |
| backend.tests.unit.test_llm_rate_limiter::test_rejects_invalid_limits\[kwargs0\] | passed | 0.00 |  |
| backend.tests.unit.test_llm_rate_limiter::test_rejects_invalid_limits\[kwargs1\] | passed | 0.00 |  |
| backend.tests.unit.test_llm_rate_limiter::test_rejects_invalid_limits\[kwargs2\] | passed | 0.00 |  |
| backend.tests.unit.test_llm_text::test_strip_think\[\<think\>plan the answer\</think\>Answer-Answer\] | passed | 0.00 |  |
| backend.tests.unit.test_llm_text::test_strip_think\[reasoning without an opening tag\</think\>\\n\\nAnswer-Answer\] | passed | 0.00 |  |
| backend.tests.unit.test_llm_text::test_strip_think\[\<think\>a\</think\>b\</think\> final-final\] | passed | 0.00 |  |
| backend.tests.unit.test_llm_text::test_strip_think\[Plain answer-Plain answer\] | passed | 0.00 |  |
| backend.tests.unit.test_llm_text::test_strip_think\[ keeps spacing - keeps spacing \] | passed | 0.00 |  |
| backend.tests.unit.test_llm_text::test_strip_think\[-\] | passed | 0.00 |  |
| backend.tests.unit.test_llm_text::test_strip_think\[None-\] | passed | 0.00 |  |
| backend.tests.unit.test_llm_text::test_parses_json_arguments | passed | 0.00 |  |
| backend.tests.unit.test_llm_text::test_accepts_arguments_already_decoded_as_object | passed | 0.00 |  |
| backend.tests.unit.test_llm_text::test_empty_arguments_mean_no_arguments | passed | 0.00 |  |
| backend.tests.unit.test_llm_text::test_no_tool_calls\[None\] | passed | 0.00 |  |
| backend.tests.unit.test_llm_text::test_no_tool_calls\[raw1\] | passed | 0.00 |  |
| backend.tests.unit.test_llm_text::test_invalid_arguments_raise_typed_error\[{not json\] | passed | 0.00 |  |
| backend.tests.unit.test_llm_text::test_invalid_arguments_raise_typed_error\[\[1, 2\]\] | passed | 0.00 |  |
| backend.tests.unit.test_llm_text::test_invalid_arguments_raise_typed_error\["text"\] | passed | 0.00 |  |
| backend.tests.unit.test_llm_text::test_invalid_arguments_raise_typed_error\[__import__('os').system('id')\] | passed | 0.00 |  |
| backend.tests.unit.test_llm_text::test_invalid_arguments_raise_typed_error\[NaN\] | passed | 0.00 |  |
| backend.tests.unit.test_llm_text::test_invalid_arguments_raise_typed_error\[{"a": NaN}\] | passed | 0.00 |  |
| backend.tests.unit.test_llm_text::test_invalid_arguments_raise_typed_error\[{"a": Infinity}\] | passed | 0.00 |  |
| backend.tests.unit.test_llm_text::test_invalid_arguments_raise_typed_error\[42\] | passed | 0.00 |  |
| backend.tests.unit.test_llm_text::test_error_names_the_tool_but_not_the_arguments | passed | 0.00 |  |
| backend.tests.unit.test_llm_text::test_oversized_arguments_raise_typed_error | passed | 0.00 |  |
| backend.tests.unit.test_llm_text::test_tool_call_parse_error_is_a_bad_response | passed | 0.00 |  |
| backend.tests.unit.test_llm_text::test_malformed_tool_calls_raise_bad_response\[not a list\] | passed | 0.00 |  |
| backend.tests.unit.test_llm_text::test_malformed_tool_calls_raise_bad_response\[raw1\] | passed | 0.00 |  |
| backend.tests.unit.test_llm_text::test_malformed_tool_calls_raise_bad_response\[raw2\] | passed | 0.00 |  |
| backend.tests.unit.test_llm_text::test_malformed_tool_calls_raise_bad_response\[raw3\] | passed | 0.00 |  |
| backend.tests.unit.test_llm_text::test_malformed_tool_calls_raise_bad_response\[raw4\] | passed | 0.00 |  |
| backend.tests.unit.test_llm_text::test_strip_think_drops_unclosed_and_differently_cased_blocks\[\<think\>secret plan, cut off by max_tokens-\] | passed | 0.00 |  |
| backend.tests.unit.test_llm_text::test_strip_think_drops_unclosed_and_differently_cased_blocks\[Intro \<THINK\>unfinished-Intro\] | passed | 0.00 |  |
| backend.tests.unit.test_llm_text::test_strip_think_drops_unclosed_and_differently_cased_blocks\[plan\</Think\>Answer-Answer\] | passed | 0.00 |  |
| backend.tests.unit.test_llm_text::test_too_many_tool_calls_raise_bad_response | passed | 0.00 |  |
| backend.tests.unit.test_llm_text::test_duplicate_tool_call_ids_raise_bad_response | passed | 0.00 |  |
| backend.tests.unit.test_llm_text::test_unusable_tool_call_ids_raise_bad_response\[cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc... | passed | 0.00 |  |
| backend.tests.unit.test_llm_text::test_unusable_tool_call_ids_raise_bad_response\[id\\nwith newline\] | passed | 0.00 |  |
| backend.tests.unit.test_llm_text::test_unusable_tool_call_ids_raise_bad_response\[7\] | passed | 0.00 |  |
| backend.tests.unit.test_logging::test_creates_log_dir_and_both_files | passed | 0.01 |  |
| backend.tests.unit.test_logging::test_record_has_required_fields | passed | 0.00 |  |
| backend.tests.unit.test_logging::test_event_defaults_when_not_given | passed | 0.01 |  |
| backend.tests.unit.test_logging::test_request_and_investigation_ids_come_from_contextvars | passed | 0.00 |  |
| backend.tests.unit.test_logging::test_ids_are_null_outside_a_request | passed | 0.00 |  |
| backend.tests.unit.test_logging::test_extra_fields_are_included | passed | 0.00 |  |
| backend.tests.unit.test_logging::test_errors_file_only_has_warning_and_above | passed | 0.00 |  |
| backend.tests.unit.test_logging::test_log_level_setting_filters_app_file | passed | 0.00 |  |
| backend.tests.unit.test_logging::test_exception_is_logged_with_stack_trace | passed | 0.01 |  |
| backend.tests.unit.test_logging::test_rotation_is_10mb_times_5 | passed | 0.00 |  |
| backend.tests.unit.test_logging::test_console_handler_writes_json | passed | 0.00 |  |
| backend.tests.unit.test_logging::test_setup_is_idempotent | passed | 0.00 |  |
| backend.tests.unit.test_logging::test_teardown_removes_only_our_handlers | passed | 0.00 |  |
| backend.tests.unit.test_main::test_create_builds_app_and_configures_logging | passed | 0.00 |  |
| backend.tests.unit.test_main::test_create_logs_critical_and_reraises_on_bad_settings | passed | 0.00 |  |
| backend.tests.unit.test_memory_world_model::test_event_defaults_to_a_uuid_id_and_now_utc | passed | 0.00 |  |
| backend.tests.unit.test_memory_world_model::test_event_serialises_time_as_utc_z | passed | 0.00 |  |
| backend.tests.unit.test_memory_world_model::test_event_rejects_invalid_input\[overrides0\] | passed | 0.00 |  |
| backend.tests.unit.test_memory_world_model::test_event_rejects_invalid_input\[overrides1\] | passed | 0.00 |  |
| backend.tests.unit.test_memory_world_model::test_event_rejects_invalid_input\[overrides2\] | passed | 0.00 |  |
| backend.tests.unit.test_memory_world_model::test_event_rejects_invalid_input\[overrides3\] | passed | 0.00 |  |
| backend.tests.unit.test_memory_world_model::test_event_rejects_invalid_input\[overrides4\] | passed | 0.00 |  |
| backend.tests.unit.test_memory_world_model::test_event_rejects_invalid_input\[overrides5\] | passed | 0.00 |  |
| backend.tests.unit.test_memory_world_model::test_event_rejects_invalid_input\[overrides6\] | passed | 0.00 |  |
| backend.tests.unit.test_memory_world_model::test_event_rejects_invalid_input\[overrides7\] | passed | 0.00 |  |
| backend.tests.unit.test_memory_world_model::test_event_rejects_invalid_input\[overrides8\] | passed | 0.00 |  |
| backend.tests.unit.test_memory_world_model::test_event_rejects_invalid_input\[overrides9\] | passed | 0.00 |  |
| backend.tests.unit.test_memory_world_model::test_event_rejects_invalid_input\[overrides10\] | passed | 0.00 |  |
| backend.tests.unit.test_memory_world_model::test_event_rejects_invalid_input\[overrides11\] | passed | 0.00 |  |
| backend.tests.unit.test_memory_world_model::test_append_posts_the_event_with_token_and_request_id | passed | 0.02 |  |
| backend.tests.unit.test_memory_world_model::test_append_reports_a_duplicate | passed | 0.02 |  |
| backend.tests.unit.test_memory_world_model::test_append_refuses_a_bad_case_id_without_a_request\[../admin\] | passed | 0.02 |  |
| backend.tests.unit.test_memory_world_model::test_append_refuses_a_bad_case_id_without_a_request\[inv 1\] | passed | 0.02 |  |
| backend.tests.unit.test_memory_world_model::test_append_refuses_a_bad_case_id_without_a_request\[inv:1\] | passed | 0.02 |  |
| backend.tests.unit.test_memory_world_model::test_append_refuses_a_bad_case_id_without_a_request\[xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\] | passed | 0.02 |  |
| backend.tests.unit.test_memory_world_model::test_append_refuses_a_bad_case_id_without_a_request\[\] | passed | 0.02 |  |
| backend.tests.unit.test_memory_world_model::test_append_raises_rejected_on_4xx_with_the_service_code\[400-invalid_request\] | passed | 0.02 |  |
| backend.tests.unit.test_memory_world_model::test_append_raises_rejected_on_4xx_with_the_service_code\[401-unauthorized\] | passed | 0.02 |  |
| backend.tests.unit.test_memory_world_model::test_append_raises_rejected_on_4xx_with_the_service_code\[409-event_conflict\] | passed | 0.02 |  |
| backend.tests.unit.test_memory_world_model::test_append_raises_rejected_on_4xx_with_the_service_code\[409-case_full\] | passed | 0.02 |  |
| backend.tests.unit.test_memory_world_model::test_append_raises_unavailable_when_the_service_is_down\[failure0\] | passed | 0.02 |  |
| backend.tests.unit.test_memory_world_model::test_append_raises_unavailable_when_the_service_is_down\[failure1\] | passed | 0.02 |  |
| backend.tests.unit.test_memory_world_model::test_append_raises_unavailable_when_the_service_is_down\[failure2\] | passed | 0.02 |  |
| backend.tests.unit.test_memory_world_model::test_unavailable_without_a_token_and_no_request | passed | 0.02 |  |
| backend.tests.unit.test_memory_world_model::test_append_raises_bad_response_on_unusable_answers\[response0\] | passed | 0.02 |  |
| backend.tests.unit.test_memory_world_model::test_append_raises_bad_response_on_unusable_answers\[response1\] | passed | 0.02 |  |
| backend.tests.unit.test_memory_world_model::test_append_raises_bad_response_on_unusable_answers\[response2\] | passed | 0.02 |  |
| backend.tests.unit.test_memory_world_model::test_append_raises_bad_response_on_unusable_answers\[response3\] | passed | 0.02 |  |
| backend.tests.unit.test_memory_world_model::test_record_event_returns_stored | passed | 0.02 |  |
| backend.tests.unit.test_memory_world_model::test_record_event_continues_with_memory_unavailable_when_down | passed | 0.02 |  |
| backend.tests.unit.test_memory_world_model::test_record_event_still_raises_on_rejection | passed | 0.02 |  |
| backend.tests.unit.test_memory_world_model::test_timeline_returns_events_in_order | passed | 0.02 |  |
| backend.tests.unit.test_memory_world_model::test_timeline_of_an_unknown_case_is_empty | passed | 0.02 |  |
| backend.tests.unit.test_memory_world_model::test_entity_returns_references_and_none_when_unknown | passed | 0.02 |  |
| backend.tests.unit.test_memory_world_model::test_entity_refuses_ids_outside_the_read_api\[case_event:inv-1:e1\] | passed | 0.02 |  |
| backend.tests.unit.test_memory_world_model::test_entity_refuses_ids_outside_the_read_api\[supplier:erp:../x\] | passed | 0.02 |  |
| backend.tests.unit.test_memory_world_model::test_entity_refuses_ids_outside_the_read_api\[S0001\] | passed | 0.02 |  |
| backend.tests.unit.test_memory_world_model::test_health | passed | 0.03 |  |
| backend.tests.unit.test_memory_world_model::test_repr_has_no_token | passed | 0.02 |  |
| backend.tests.unit.test_memory_world_model::test_event_rejects_unsafe_text_and_numbers\[overrides0\] | passed | 0.00 |  |
| backend.tests.unit.test_memory_world_model::test_event_rejects_unsafe_text_and_numbers\[overrides1\] | passed | 0.00 |  |
| backend.tests.unit.test_memory_world_model::test_event_rejects_unsafe_text_and_numbers\[overrides2\] | passed | 0.00 |  |
| backend.tests.unit.test_memory_world_model::test_event_rejects_unsafe_text_and_numbers\[overrides3\] | passed | 0.00 |  |
| backend.tests.unit.test_memory_world_model::test_event_rejects_unsafe_text_and_numbers\[overrides4\] | passed | 0.00 |  |
| backend.tests.unit.test_memory_world_model::test_record_event_degrades_on_conditions_that_are_not_our_bug\[response0\] | passed | 0.02 |  |
| backend.tests.unit.test_memory_world_model::test_record_event_degrades_on_conditions_that_are_not_our_bug\[response1\] | passed | 0.02 |  |
| backend.tests.unit.test_memory_world_model::test_record_event_degrades_on_conditions_that_are_not_our_bug\[response2\] | passed | 0.02 |  |
| backend.tests.unit.test_memory_world_model::test_record_event_degrades_on_conditions_that_are_not_our_bug\[response3\] | passed | 0.02 |  |
| backend.tests.unit.test_memory_world_model::test_record_event_degrades_on_conditions_that_are_not_our_bug\[response4\] | passed | 0.02 |  |
| backend.tests.unit.test_memory_world_model::test_record_event_degrades_on_conditions_that_are_not_our_bug\[response5\] | passed | 0.02 |  |
| backend.tests.unit.test_memory_world_model::test_record_event_degrades_on_conditions_that_are_not_our_bug\[response6\] | passed | 0.02 |  |
| backend.tests.unit.test_memory_world_model::test_record_event_raises_on_event_conflict | passed | 0.03 |  |
| backend.tests.unit.test_memory_world_model::test_full_case_timeline_is_readable | passed | 1.51 |  |
| backend.tests.unit.test_memory_world_model::test_one_unreadable_event_does_not_hide_the_timeline | passed | 0.02 |  |
| backend.tests.unit.test_memory_world_model::test_generic_404_is_not_an_empty_timeline | passed | 0.02 |  |
| backend.tests.unit.test_memory_world_model::test_append_answer_for_another_event_is_a_bad_response | passed | 0.02 |  |
| backend.tests.unit.test_redaction::test_redacts_bearer_token | passed | 0.00 |  |
| backend.tests.unit.test_redaction::test_redacts_authorization_header_forms | passed | 0.00 |  |
| backend.tests.unit.test_redaction::test_redacts_nvapi_keys | passed | 0.00 |  |
| backend.tests.unit.test_redaction::test_redacts_each_setting_secret\[zk-secret-VALUE-1111\] | passed | 0.00 |  |
| backend.tests.unit.test_redaction::test_redacts_each_setting_secret\[zk-buyer-VALUE-5555\] | passed | 0.00 |  |
| backend.tests.unit.test_redaction::test_redacts_each_setting_secret\[zk-admin-VALUE-6666\] | passed | 0.00 |  |
| backend.tests.unit.test_redaction::test_redacts_each_setting_secret\[mt-secret-VALUE-2222\] | passed | 0.00 |  |
| backend.tests.unit.test_redaction::test_redacts_each_setting_secret\[erp-pass-VALUE-3333\] | passed | 0.00 |  |
| backend.tests.unit.test_redaction::test_redacts_each_setting_secret\[app-pass-VALUE-4444\] | passed | 0.00 |  |
| backend.tests.unit.test_redaction::test_redacts_each_setting_secret\[postgresql://erp:erp-pass-VALUE-3333@localhost:5433/erp\] | passed | 0.00 |  |
| backend.tests.unit.test_redaction::test_redacts_sensitive_dict_keys_but_not_token_counts | passed | 0.00 |  |
| backend.tests.unit.test_redaction::test_redacts_nested_structures_and_objects | passed | 0.00 |  |
| backend.tests.unit.test_redaction::test_every_handler_has_the_redaction_filter | passed | 0.00 |  |
| backend.tests.unit.test_redaction::test_secret_in_message_args_and_extra_never_reaches_files\[zk-secret-VALUE-1111\] | passed | 0.01 |  |
| backend.tests.unit.test_redaction::test_secret_in_message_args_and_extra_never_reaches_files\[zk-buyer-VALUE-5555\] | passed | 0.01 |  |
| backend.tests.unit.test_redaction::test_secret_in_message_args_and_extra_never_reaches_files\[zk-admin-VALUE-6666\] | passed | 0.01 |  |
| backend.tests.unit.test_redaction::test_secret_in_message_args_and_extra_never_reaches_files\[mt-secret-VALUE-2222\] | passed | 0.00 |  |
| backend.tests.unit.test_redaction::test_secret_in_message_args_and_extra_never_reaches_files\[erp-pass-VALUE-3333\] | passed | 0.00 |  |
| backend.tests.unit.test_redaction::test_secret_in_message_args_and_extra_never_reaches_files\[app-pass-VALUE-4444\] | passed | 0.00 |  |
| backend.tests.unit.test_redaction::test_secret_in_message_args_and_extra_never_reaches_files\[postgresql://erp:erp-pass-VALUE-3333@localhost:5433/erp\] | passed | 0.01 |  |
| backend.tests.unit.test_redaction::test_secret_in_exception_text_and_traceback_is_redacted | passed | 0.01 |  |
| backend.tests.unit.test_redaction::test_secret_in_stack_info_is_redacted | passed | 0.02 |  |
| backend.tests.unit.test_score::test_weights_and_thresholds_v1_are_pinned | passed | 0.00 |  |
| backend.tests.unit.test_score::test_delivery_collapse_scores_the_maximum | passed | 0.00 |  |
| backend.tests.unit.test_score::test_delivery_at_target_and_improving_scores_zero | passed | 0.00 |  |
| backend.tests.unit.test_score::test_delivery_level_and_trend_add_up | passed | 0.00 |  |
| backend.tests.unit.test_score::test_delivery_without_previous_window_uses_level_only | passed | 0.00 |  |
| backend.tests.unit.test_score::test_delivery_with_too_few_deliveries_is_not_assessed | passed | 0.00 |  |
| backend.tests.unit.test_score::test_concentration_weights_single_source_parts_by_criticality | passed | 0.00 |  |
| backend.tests.unit.test_score::test_concentration_part_points_are_capped_at_70 | passed | 0.00 |  |
| backend.tests.unit.test_score::test_no_single_source_parts_is_zero_concentration_not_unknown | passed | 0.00 |  |
| backend.tests.unit.test_score::test_financial_decline_is_scaled_to_minus_20_percent | passed | 0.00 |  |
| backend.tests.unit.test_score::test_financial_growth_scores_zero | passed | 0.00 |  |
| backend.tests.unit.test_score::test_not_assessed_component_has_no_value_and_says_why | passed | 0.00 |  |
| backend.tests.unit.test_score::test_country_averages_both_indicators | passed | 0.00 |  |
| backend.tests.unit.test_score::test_country_with_one_indicator_uses_it_alone | passed | 0.00 |  |
| backend.tests.unit.test_score::test_country_without_indicators_is_not_assessed | passed | 0.00 |  |
| backend.tests.unit.test_score::test_total_is_the_weighted_average_of_assessed_components | passed | 0.00 |  |
| backend.tests.unit.test_score::test_rating_thresholds\[34.9-green\] | passed | 0.00 |  |
| backend.tests.unit.test_score::test_rating_thresholds\[35.0-amber\] | passed | 0.00 |  |
| backend.tests.unit.test_score::test_rating_thresholds\[59.9-amber\] | passed | 0.00 |  |
| backend.tests.unit.test_score::test_rating_thresholds\[60.0-red\] | passed | 0.00 |  |
| backend.tests.unit.test_score::test_low_coverage_is_insufficient_not_green | passed | 0.00 |  |
| backend.tests.unit.test_score::test_nothing_assessed_has_no_total | passed | 0.00 |  |
| backend.tests.unit.test_score::test_critical_signals_list_components_at_or_above_80_without_changing_the_rating | passed | 0.00 |  |
| backend.tests.unit.test_score::test_component_at_79_is_not_critical | passed | 0.00 |  |
| backend.tests.unit.test_score::test_unassessed_components_are_never_critical | passed | 0.00 |  |
| backend.tests.unit.test_score::test_combine_needs_each_component_once | passed | 0.00 |  |
| backend.tests.unit.test_scripts_help::test_every_script_is_covered | passed | 0.00 |  |
| backend.tests.unit.test_scripts_help::test_help_prints_usage_and_does_no_work\[check_llm.py\] | passed | 0.24 |  |
| backend.tests.unit.test_scripts_help::test_help_prints_usage_and_does_no_work\[erp_up.sh\] | passed | 0.01 |  |
| backend.tests.unit.test_scripts_help::test_help_prints_usage_and_does_no_work\[test_report.py\] | passed | 0.05 |  |
| backend.tests.unit.test_scripts_help::test_help_prints_usage_and_does_no_work\[verify_ciks.py\] | passed | 0.20 |  |
| backend.tests.unit.test_scripts_help::test_help_prints_usage_and_does_no_work\[warm_cache.py\] | passed | 0.17 |  |
| backend.tests.unit.test_scripts_help::test_help_prints_usage_and_does_no_work\[zetaris_verify.py\] | passed | 0.17 |  |
| backend.tests.unit.test_settings::test_loads_values_from_environment | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_defaults_are_safe | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_secret_fields_are_secretstr_and_not_in_repr\[zetaris_admin_api_key\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_secret_fields_are_secretstr_and_not_in_repr\[zetaris_key_buyer\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_secret_fields_are_secretstr_and_not_in_repr\[zetaris_key_risk\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_secret_fields_are_secretstr_and_not_in_repr\[meterless_runtime_token\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_secret_fields_are_secretstr_and_not_in_repr\[context_service_token\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_secret_fields_are_secretstr_and_not_in_repr\[erp_database_url\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_secret_fields_are_secretstr_and_not_in_repr\[app_database_url\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_secret_values_include_every_secret_and_database_passwords | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_missing_required_value_names_the_variable | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_zetaris_keys_are_optional_and_empty_means_unset\[delete-ZETARIS_ADMIN_API_KEY\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_zetaris_keys_are_optional_and_empty_means_unset\[delete-ZETARIS_KEY_BUYER\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_zetaris_keys_are_optional_and_empty_means_unset\[delete-ZETARIS_KEY_RISK\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_zetaris_keys_are_optional_and_empty_means_unset\[empty-ZETARIS_ADMIN_API_KEY\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_zetaris_keys_are_optional_and_empty_means_unset\[empty-ZETARIS_KEY_BUYER\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_zetaris_keys_are_optional_and_empty_means_unset\[empty-ZETARIS_KEY_RISK\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_zetaris_keys_are_optional_and_empty_means_unset\[blank-ZETARIS_ADMIN_API_KEY\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_zetaris_keys_are_optional_and_empty_means_unset\[blank-ZETARIS_KEY_BUYER\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_zetaris_keys_are_optional_and_empty_means_unset\[blank-ZETARIS_KEY_RISK\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_the_retired_app_key_variable_is_not_read | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_reports_all_missing_variables_at_once | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_empty_value_counts_as_missing | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_invalid_value_names_variable_but_not_value\[APP_ENV-staging\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_invalid_value_names_variable_but_not_value\[TOOL_CACHE_TTL_SECONDS-abc\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_invalid_value_names_variable_but_not_value\[LOG_LEVEL-LOUD\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_settings_error_hides_the_original_validation_error | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_log_level_is_case_insensitive | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_meterless_base_url_is_normalised\[http://127.0.0.1:8088/v1\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_meterless_base_url_is_normalised\[http://127.0.0.1:8088\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_meterless_base_url_is_normalised\[http://127.0.0.1:8088/\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_meterless_base_url_is_normalised\[127.0.0.1:8088\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_meterless_base_url_is_normalised\[127.0.0.1:8088/v1/\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_meterless_base_url_is_normalised\[http://127.0.0.1:8088/v1/chat/completions\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_meterless_base_url_is_normalised\[http://127.0.0.1:8088/v1/models\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_meterless_base_url_is_normalised\[http://127.0.0.1:8088/v1/embeddings\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_meterless_base_url_is_normalised\[http://127.0.0.1:8088/healthz\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_meterless_base_url_is_normalised\[http://127.0.0.1:8088/v1/v1\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_meterless_base_url_is_normalised\[ http://127.0.0.1:8088/v1?x=1#frag \] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_meterless_base_url_keeps_https_and_path_prefix | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_meterless_base_url_rejects_bad_urls\[ftp://127.0.0.1:8088/v1\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_meterless_base_url_rejects_bad_urls\[http:///v1\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_meterless_base_url_rejects_bad_urls\[http://user:pw@127.0.0.1:8088/v1\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_routing_modes_are_rejected_as_model_ids\[meterless/auto-meterless_model\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_routing_modes_are_rejected_as_model_ids\[meterless/auto-meterless_deep_model\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_routing_modes_are_rejected_as_model_ids\[meterless/auto-meterless_fast_model\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_routing_modes_are_rejected_as_model_ids\[meterless/auto-meterless_embed_model\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_routing_modes_are_rejected_as_model_ids\[meterless/fusion-meterless_model\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_routing_modes_are_rejected_as_model_ids\[meterless/fusion-meterless_deep_model\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_routing_modes_are_rejected_as_model_ids\[meterless/fusion-meterless_fast_model\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_routing_modes_are_rejected_as_model_ids\[meterless/fusion-meterless_embed_model\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_routing_modes_are_rejected_as_model_ids\[ Meterless/Auto -meterless_model\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_routing_modes_are_rejected_as_model_ids\[ Meterless/Auto -meterless_deep_model\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_routing_modes_are_rejected_as_model_ids\[ Meterless/Auto -meterless_fast_model\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_routing_modes_are_rejected_as_model_ids\[ Meterless/Auto -meterless_embed_model\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_routing_mode_error_names_the_variable | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_placeholder_runtime_token_is_rejected\[rt-local\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_placeholder_runtime_token_is_rejected\[ rt-local \] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_app_id_must_be_a_stable_lowercase_id\[Supplier-Risk\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_app_id_must_be_a_stable_lowercase_id\[supplier risk\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_app_id_must_be_a_stable_lowercase_id\[-x\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_app_id_must_be_a_stable_lowercase_id\[aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_plain_http_is_allowed_only_for_local_hosts\[http://localhost:8088/v1\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_plain_http_is_allowed_only_for_local_hosts\[http://127.0.0.2:8088/v1\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_plain_http_is_allowed_only_for_local_hosts\[http://\[::1\]:8088/v1\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_plain_http_is_allowed_only_for_local_hosts\[http://host.docker.internal:8088/v1\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_plain_http_is_allowed_only_for_local_hosts\[https://nightshift.example.com/v1\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_plain_http_to_a_remote_host_is_rejected\[http://10.0.0.5:8088/v1\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_plain_http_to_a_remote_host_is_rejected\[nightshift.example.com\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_runtime_token_must_be_visible_ascii\[has space\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_runtime_token_must_be_visible_ascii\[tab\\tinside\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_runtime_token_must_be_visible_ascii\[RT-Local\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_runtime_token_must_be_visible_ascii\[xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx... | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_snapshot_cache_defaults | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_snapshot_cache_settings_are_bounded\[overrides0\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_snapshot_cache_settings_are_bounded\[overrides1\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_snapshot_cache_settings_are_bounded\[overrides2\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_snapshot_cache_settings_are_bounded\[overrides3\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_snapshot_cache_settings_are_bounded\[overrides4\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_snapshot_cache_settings_are_bounded\[overrides5\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_context_service_token_is_optional_and_empty_means_unset\[delete\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_context_service_token_is_optional_and_empty_means_unset\[empty\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_context_service_token_is_optional_and_empty_means_unset\[blank\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_context_service_token_must_be_long_visible_ascii\[short\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_context_service_token_must_be_long_visible_ascii\[aaaaaaaaaaaaaaaaaaaa bbbbbbbbbbbbbbbbbbbb\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_context_service_token_must_be_long_visible_ascii\[xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx... | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_context_service_timeout_defaults_and_is_bounded | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_context_service_url_rejects_unsafe_targets\[http://169.254.169.254/latest\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_context_service_url_rejects_unsafe_targets\[http://evil.example.com/prefix\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_context_service_url_rejects_unsafe_targets\[file:///etc/passwd\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_context_service_url_rejects_unsafe_targets\[http://user:pw@localhost:7070\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_context_service_url_rejects_unsafe_targets\[http://localhost:7070/?x=1\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_context_service_url_rejects_unsafe_targets\[http://localhost:7070/#f\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_context_service_url_accepts_local_http_and_https\[http://localhost:7070\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_context_service_url_accepts_local_http_and_https\[http://127.0.0.1:7070/\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_context_service_url_accepts_local_http_and_https\[http://context-service:7070\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_context_service_url_accepts_local_http_and_https\[https://board.example.com\] | passed | 0.00 |  |
| backend.tests.unit.test_smoke::test_app_package_imports | passed | 0.00 |  |
| backend.tests.unit.test_snapshot_warmup::test_app_starts_the_warm_up_on_startup_and_closes_on_shutdown | passed | 0.01 |  |
| backend.tests.unit.test_snapshot_warmup::test_app_without_a_cache_does_not_warm_up | passed | 0.01 |  |
| backend.tests.unit.test_snapshot_warmup::test_uvicorn_factory_wires_a_snapshot_cache | passed | 0.01 |  |
| backend.tests.unit.test_snapshot_warmup::test_warm_cache_script_reports_every_view_and_exits_0 | passed | 0.09 |  |
| backend.tests.unit.test_snapshot_warmup::test_warm_cache_script_exits_1_on_a_failed_view | passed | 0.09 |  |
| backend.tests.unit.test_snapshot_warmup::test_warm_cache_script_skips_without_role_keys | passed | 0.00 |  |
| backend.tests.unit.test_test_report::test_parse_counts_each_outcome | passed | 0.00 |  |
| backend.tests.unit.test_test_report::test_parse_cases_have_id_outcome_and_failure_line | passed | 0.00 |  |
| backend.tests.unit.test_test_report::test_render_markdown_has_title_totals_and_rows | passed | 0.00 |  |
| backend.tests.unit.test_test_report::test_render_escapes_pipes_in_details | passed | 0.00 |  |
| backend.tests.unit.test_test_report::test_main_prints_markdown_and_returns_zero | passed | 0.00 |  |
| backend.tests.unit.test_test_report::test_main_default_title | passed | 0.00 |  |
| backend.tests.unit.test_test_report::test_main_missing_file_exits_zero | passed | 0.00 |  |
| backend.tests.unit.test_test_report::test_main_invalid_xml_exits_zero | passed | 0.00 |  |
| backend.tests.unit.test_test_report::test_report_escapes_markdown_newlines_and_html | passed | 0.00 |  |
| backend.tests.unit.test_test_report::test_report_truncates_long_cells | passed | 0.00 |  |
| backend.tests.unit.test_test_report::test_parse_bad_time_defaults_to_zero\[abc\] | passed | 0.00 |  |
| backend.tests.unit.test_test_report::test_parse_bad_time_defaults_to_zero\[nan\] | passed | 0.00 |  |
| backend.tests.unit.test_test_report::test_parse_bad_time_defaults_to_zero\[inf\] | passed | 0.00 |  |
| backend.tests.unit.test_test_report::test_parse_bad_time_defaults_to_zero\[-1\] | passed | 0.00 |  |
| backend.tests.unit.test_test_report::test_parse_bad_time_defaults_to_zero\[\] | passed | 0.00 |  |
| backend.tests.unit.test_test_report::test_main_bad_time_exits_zero_and_prints_report | passed | 0.00 |  |
| backend.tests.unit.test_tools_cache::test_first_read_loads_the_view_and_later_reads_hit | passed | 0.03 |  |
| backend.tests.unit.test_tools_cache::test_sends_the_registered_constant_sql | passed | 0.02 |  |
| backend.tests.unit.test_tools_cache::test_hundred_concurrent_misses_cause_one_upstream_call | passed | 0.09 |  |
| backend.tests.unit.test_tools_cache::test_buyer_and_risk_snapshots_never_mix | passed | 0.07 |  |
| backend.tests.unit.test_tools_cache::test_buyer_key_never_reads_a_risk_only_view\[v_contracts_full\] | passed | 0.01 |  |
| backend.tests.unit.test_tools_cache::test_buyer_key_never_reads_a_risk_only_view\[v_supplier_revenue\] | passed | 0.01 |  |
| backend.tests.unit.test_tools_cache::test_buyer_key_never_reads_a_risk_only_view\[v_open_orders_full\] | passed | 0.01 |  |
| backend.tests.unit.test_tools_cache::test_unknown_view_is_refused | passed | 0.01 |  |
| backend.tests.unit.test_tools_cache::test_stale_snapshot_is_served_while_one_background_refresh_runs | passed | 0.09 |  |
| backend.tests.unit.test_tools_cache::test_external_views_keep_their_longer_ttl | passed | 0.05 |  |
| backend.tests.unit.test_tools_cache::test_external_snapshot_is_served_up_to_a_day_old | passed | 0.02 |  |
| backend.tests.unit.test_tools_cache::test_failed_refresh_keeps_serving_stale_and_logs_a_warning | passed | 0.10 |  |
| backend.tests.unit.test_tools_cache::test_past_max_staleness_the_source_is_unavailable | passed | 0.04 |  |
| backend.tests.unit.test_tools_cache::test_past_max_staleness_a_working_source_reloads_before_answering | passed | 0.05 |  |
| backend.tests.unit.test_tools_cache::test_a_snapshot_that_fills_the_row_limit_is_refused_as_truncated | passed | 0.02 |  |
| backend.tests.unit.test_tools_cache::test_rows_of_the_wrong_width_are_refused | passed | 0.02 |  |
| backend.tests.unit.test_tools_cache::test_a_role_without_a_key_is_unavailable_not_admin | passed | 0.01 |  |
| backend.tests.unit.test_tools_cache::test_cache_hit_is_faster_than_50_ms | passed | 0.03 |  |
| backend.tests.unit.test_tools_cache::test_warm_all_loads_every_granted_view_erp_first_two_at_a_time | passed | 0.17 |  |
| backend.tests.unit.test_tools_cache::test_warm_all_respects_the_concurrency_setting | passed | 0.25 |  |
| backend.tests.unit.test_tools_cache::test_warm_all_skips_a_missing_key_and_survives_failures | passed | 0.05 |  |
| backend.tests.unit.test_tools_cache::test_start_warmup_does_not_block | passed | 0.54 |  |
| backend.tests.unit.test_tools_cache::test_view_groups_cover_every_view | passed | 0.00 |  |
| backend.tests.unit.test_tools_cache::test_malformed_cells_drop_the_row_and_are_counted\[v_deliveries-5-27/10/2026\] | passed | 0.06 |  |
| backend.tests.unit.test_tools_cache::test_malformed_cells_drop_the_row_and_are_counted\[v_deliveries-7-NaN\] | passed | 0.04 |  |
| backend.tests.unit.test_tools_cache::test_malformed_cells_drop_the_row_and_are_counted\[v_deliveries-1-S3\] | passed | 0.04 |  |
| backend.tests.unit.test_tools_cache::test_malformed_cells_drop_the_row_and_are_counted\[v_country_indicators-3-:\] | passed | 0.02 |  |
| backend.tests.unit.test_tools_cache::test_malformed_cells_drop_the_row_and_are_counted\[v_country_indicators-2-2026-13\] | passed | 0.02 |  |
| backend.tests.unit.test_tools_cache::test_malformed_cells_drop_the_row_and_are_counted\[v_part_sourcing-6-maybe\] | passed | 0.02 |  |
| backend.tests.unit.test_tools_cache::test_malformed_cells_drop_the_row_and_are_counted\[v_supplier_revenue-7-inf\] | passed | 0.02 |  |
| backend.tests.unit.test_tools_cache::test_null_cells_are_kept | passed | 0.02 |  |
| backend.tests.unit.test_tools_cache::test_a_failed_load_is_retried_only_after_the_backoff | passed | 0.04 |  |
| backend.tests.unit.test_tools_cache::test_typed_columns_belong_to_their_view | passed | 0.00 |  |
| backend.tests.unit.test_tools_catalogue::test_catalogue_has_the_architecture_tools_except_propose_action | passed | 0.00 |  |
| backend.tests.unit.test_tools_catalogue::test_buyer_lists_only_suppliers_of_their_category | passed | 0.02 |  |
| backend.tests.unit.test_tools_catalogue::test_risk_manager_lists_all_suppliers | passed | 0.02 |  |
| backend.tests.unit.test_tools_catalogue::test_list_with_a_risk_filter_scores_each_supplier | passed | 0.06 |  |
| backend.tests.unit.test_tools_catalogue::test_supplier_outside_the_buyers_category_looks_like_an_unknown_one | passed | 0.02 |  |
| backend.tests.unit.test_tools_catalogue::test_buyer_profile_has_no_contract_value_or_notes | passed | 0.02 |  |
| backend.tests.unit.test_tools_catalogue::test_risk_profile_quotes_buyer_notes_as_untrusted_data | passed | 0.02 |  |
| backend.tests.unit.test_tools_catalogue::test_open_orders_flag_overdue_orders | passed | 0.02 |  |
| backend.tests.unit.test_tools_catalogue::test_buyer_open_orders_carry_no_prices | passed | 0.02 |  |
| backend.tests.unit.test_tools_catalogue::test_single_source_parts_of_the_story_2_supplier | passed | 0.02 |  |
| backend.tests.unit.test_tools_catalogue::test_delivery_history_shows_the_story_1a_collapse | passed | 0.04 |  |
| backend.tests.unit.test_tools_catalogue::test_invalid_arguments_are_rejected_before_any_query\[arguments0\] | passed | 0.01 |  |
| backend.tests.unit.test_tools_catalogue::test_invalid_arguments_are_rejected_before_any_query\[arguments1\] | passed | 0.01 |  |
| backend.tests.unit.test_tools_catalogue::test_invalid_arguments_are_rejected_before_any_query\[arguments2\] | passed | 0.01 |  |
| backend.tests.unit.test_tools_catalogue::test_invalid_arguments_are_rejected_before_any_query\[arguments3\] | passed | 0.01 |  |
| backend.tests.unit.test_tools_catalogue::test_invalid_arguments_are_rejected_before_any_query\[arguments4\] | passed | 0.01 |  |
| backend.tests.unit.test_tools_catalogue::test_invalid_arguments_are_rejected_before_any_query\[arguments5\] | passed | 0.01 |  |
| backend.tests.unit.test_tools_catalogue::test_invalid_arguments_are_rejected_before_any_query\[arguments6\] | passed | 0.01 |  |
| backend.tests.unit.test_tools_catalogue::test_invalid_arguments_are_rejected_before_any_query\[arguments7\] | passed | 0.01 |  |
| backend.tests.unit.test_tools_catalogue::test_unknown_tool_is_refused | passed | 0.01 |  |
| backend.tests.unit.test_tools_catalogue::test_financial_trend_is_refused_for_a_buyer_without_a_query | passed | 0.01 |  |
| backend.tests.unit.test_tools_catalogue::test_auditor_cannot_use_data_tools | passed | 0.01 |  |
| backend.tests.unit.test_tools_catalogue::test_financial_trend_uses_the_latest_filing | passed | 0.02 |  |
| backend.tests.unit.test_tools_catalogue::test_financial_trend_of_a_private_supplier_is_no_data | passed | 0.02 |  |
| backend.tests.unit.test_tools_catalogue::test_country_indicators_report_the_six_month_change | passed | 0.02 |  |
| backend.tests.unit.test_tools_catalogue::test_country_indicators_reject_unknown_countries | passed | 0.01 |  |
| backend.tests.unit.test_tools_catalogue::test_weather_is_not_assessed | passed | 0.02 |  |
| backend.tests.unit.test_tools_catalogue::test_every_result_has_a_complete_evidence_record | passed | 0.02 |  |
| backend.tests.unit.test_tools_catalogue::test_evidence_keeps_at_most_20_sample_rows | passed | 0.04 |  |
| backend.tests.unit.test_tools_catalogue::test_today_comes_from_the_context_not_the_model | passed | 0.01 |  |
| backend.tests.unit.test_tools_rows_evidence::test_latest_filing_wins_and_fy_beats_quarters_of_the_same_year | passed | 0.00 |  |
| backend.tests.unit.test_tools_rows_evidence::test_full_years_stop_at_a_gap | passed | 0.00 |  |
| backend.tests.unit.test_tools_rows_evidence::test_cell_readers | passed | 0.00 |  |
| backend.tests.unit.test_tools_rows_evidence::test_on_time_rate_of_nothing_is_unknown | passed | 0.00 |  |
| backend.tests.unit.test_tools_rows_evidence::test_financial_with_non_positive_previous_year_is_not_assessed | passed | 0.00 |  |
| backend.tests.unit.test_tools_rows_evidence::test_supplier_with_one_full_year_has_financials_not_assessed | passed | 0.05 |  |
| backend.tests.unit.test_tools_rows_evidence::test_evidence_log_is_bounded | passed | 0.00 |  |
| backend.tests.unit.test_tools_rows_evidence::test_six_month_change_needs_twelve_values | passed | 0.00 |  |
| backend.tests.unit.test_tools_rows_evidence::test_auditor_context_has_no_zetaris_key | passed | 0.01 |  |
| backend.tests.unit.test_tools_score::test_story_1a_s0003_delivery_high_financial_low | passed | 0.04 |  |
| backend.tests.unit.test_tools_score::test_story_1b_s0005_financial_and_country | passed | 0.04 |  |
| backend.tests.unit.test_tools_score::test_private_supplier_has_unknown_financials_and_lower_coverage | passed | 0.04 |  |
| backend.tests.unit.test_tools_score::test_buyer_score_hides_financials_and_never_reads_revenue | passed | 0.04 |  |
| backend.tests.unit.test_tools_score::test_weather_is_never_assessed | passed | 0.04 |  |
| backend.tests.unit.test_tools_score::test_every_component_cites_evidence_in_the_log | passed | 0.04 |  |
| backend.tests.unit.test_tools_score::test_unavailable_external_source_lowers_coverage_instead_of_failing | passed | 0.04 |  |
| backend.tests.unit.test_tools_score::test_score_is_deterministic | passed | 0.04 |  |
| backend.tests.unit.test_tools_score::test_scores_carry_weights_v1_and_score_rules_v2 | passed | 0.05 |  |
| backend.tests.unit.test_tools_score::test_overdue_orders_without_any_completed_delivery_are_assessed | passed | 0.05 |  |
| backend.tests.unit.test_tools_score::test_overdue_orders_count_as_late_in_rate_and_trend | passed | 0.05 |  |
| backend.tests.unit.test_tools_score::test_open_orders_not_yet_due_are_not_counted | passed | 0.04 |  |
| backend.tests.unit.test_tools_score::test_overdue_is_judged_as_of_the_snapshot_date | passed | 0.04 |  |
| backend.tests.unit.test_tools_score::test_delivery_history_applies_the_same_rule | passed | 0.04 |  |
| backend.tests.unit.test_verify_ciks::test_pads_cik_to_ten_digits | passed | 0.00 |  |
| backend.tests.unit.test_verify_ciks::test_rejects_malformed_cik\[abc\] | passed | 0.00 |  |
| backend.tests.unit.test_verify_ciks::test_rejects_malformed_cik\[\] | passed | 0.00 |  |
| backend.tests.unit.test_verify_ciks::test_rejects_malformed_cik\[12345678901\] | passed | 0.00 |  |
| backend.tests.unit.test_verify_ciks::test_rejects_malformed_cik\[-1\] | passed | 0.00 |  |
| backend.tests.unit.test_verify_ciks::test_candidate_tags_match_the_supplier_model | passed | 0.00 |  |
| backend.tests.unit.test_verify_ciks::test_picks_the_tag_with_the_latest_period_end | passed | 0.01 |  |
| backend.tests.unit.test_verify_ciks::test_passes_when_the_yaml_tag_is_the_freshest | passed | 0.02 |  |
| backend.tests.unit.test_verify_ciks::test_fails_when_the_yaml_tag_is_not_the_freshest | passed | 0.02 |  |
| backend.tests.unit.test_verify_ciks::test_fails_when_even_the_freshest_tag_is_stale | passed | 0.02 |  |
| backend.tests.unit.test_verify_ciks::test_fresh_means_within_nine_months | passed | 0.02 |  |
| backend.tests.unit.test_verify_ciks::test_facts_without_a_usable_end_date_are_ignored | passed | 0.02 |  |
| backend.tests.unit.test_verify_ciks::test_fails_when_no_revenue_tag_exists | passed | 0.01 |  |
| backend.tests.unit.test_verify_ciks::test_unknown_cik_fails_without_concept_calls | passed | 0.01 |  |
| backend.tests.unit.test_verify_ciks::test_sends_the_declared_user_agent | passed | 0.01 |  |
| backend.tests.unit.test_verify_ciks::test_limits_request_rate_to_two_per_second | passed | 1.53 |  |
| backend.tests.unit.test_verify_ciks::test_lookup_finds_cik_by_ticker | passed | 0.06 |  |
| backend.tests.unit.test_verify_ciks::test_markdown_row_never_contains_user_agent | passed | 0.00 |  |
| backend.tests.unit.test_verify_ciks::test_markdown_row_shows_the_latest_period_and_the_reason | passed | 0.00 |  |
| backend.tests.unit.test_verify_ciks::test_user_agent_comes_from_settings | passed | 0.01 |  |
| backend.tests.unit.test_verify_ciks::test_user_agent_is_none_when_settings_are_invalid | passed | 0.00 |  |
| backend.tests.unit.test_verify_ciks::test_main_refuses_to_run_without_user_agent | passed | 0.00 |  |
| backend.tests.unit.test_verify_ciks::test_main_lookup_prints_found_and_missing | passed | 0.02 |  |
| backend.tests.unit.test_verify_ciks::test_main_verify_reports_failures_with_exit_code_1 | passed | 0.03 |  |
| backend.tests.unit.test_verify_ciks::test_main_rejects_unknown_command | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_client::test_role_api_key_picks_the_key_of_the_role\[buyer-zk-buyer-VALUE-5555\] | passed | 0.01 |  |
| backend.tests.unit.test_zetaris_client::test_role_api_key_picks_the_key_of_the_role\[risk_manager-zk-secret-VALUE-1111\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_client::test_role_api_key_picks_the_key_of_the_role\[director-zk-secret-VALUE-1111\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_client::test_auditor_has_no_zetaris_access | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_client::test_missing_role_key_names_the_variable | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_client::test_sends_the_documented_request | passed | 0.02 |  |
| backend.tests.unit.test_zetaris_client::test_every_call_gets_a_new_request_and_query_id | passed | 0.03 |  |
| backend.tests.unit.test_zetaris_client::test_rejects_limits_out_of_range\[0\] | passed | 0.02 |  |
| backend.tests.unit.test_zetaris_client::test_rejects_limits_out_of_range\[-1\] | passed | 0.02 |  |
| backend.tests.unit.test_zetaris_client::test_rejects_limits_out_of_range\[5001\] | passed | 0.02 |  |
| backend.tests.unit.test_zetaris_client::test_returns_rows_as_strings_or_none | passed | 0.02 |  |
| backend.tests.unit.test_zetaris_client::test_cuts_rows_to_the_limit | passed | 0.02 |  |
| backend.tests.unit.test_zetaris_client::test_empty_data_list_means_no_rows | passed | 0.02 |  |
| backend.tests.unit.test_zetaris_client::test_null_data_is_a_bad_response_not_zero_rows | passed | 0.02 |  |
| backend.tests.unit.test_zetaris_client::test_unexpected_body_is_a_bad_response\[not json\] | passed | 0.02 |  |
| backend.tests.unit.test_zetaris_client::test_unexpected_body_is_a_bad_response\[\[1, 2\]\] | passed | 0.02 |  |
| backend.tests.unit.test_zetaris_client::test_unexpected_body_is_a_bad_response\[{"data": "rows"}\] | passed | 0.02 |  |
| backend.tests.unit.test_zetaris_client::test_unexpected_body_is_a_bad_response\[{"data": \[\["ok"\], "not a row"\]}\] | passed | 0.02 |  |
| backend.tests.unit.test_zetaris_client::test_unexpected_body_is_a_bad_response\[{"data": \[\[{"nested": 1}\]\]}\] | passed | 0.02 |  |
| backend.tests.unit.test_zetaris_client::test_unauthorized_is_not_retried\[401\] | passed | 0.02 |  |
| backend.tests.unit.test_zetaris_client::test_unauthorized_is_not_retried\[403\] | passed | 0.02 |  |
| backend.tests.unit.test_zetaris_client::test_refused_query_is_a_request_error\[400\] | passed | 0.02 |  |
| backend.tests.unit.test_zetaris_client::test_refused_query_is_a_request_error\[404\] | passed | 0.02 |  |
| backend.tests.unit.test_zetaris_client::test_refused_query_is_a_request_error\[500\] | passed | 0.02 |  |
| backend.tests.unit.test_zetaris_client::test_retries_once_then_succeeds\[429\] | passed | 0.02 |  |
| backend.tests.unit.test_zetaris_client::test_retries_once_then_succeeds\[502\] | passed | 0.02 |  |
| backend.tests.unit.test_zetaris_client::test_retries_once_then_succeeds\[503\] | passed | 0.02 |  |
| backend.tests.unit.test_zetaris_client::test_retries_once_then_succeeds\[504\] | passed | 0.02 |  |
| backend.tests.unit.test_zetaris_client::test_gives_up_after_one_retry | passed | 0.02 |  |
| backend.tests.unit.test_zetaris_client::test_timeout_is_retried_then_unavailable | passed | 0.02 |  |
| backend.tests.unit.test_zetaris_client::test_default_timeout_is_the_app_limit | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_client::test_query_timeout_cuts_a_slow_query | passed | 0.11 |  |
| backend.tests.unit.test_zetaris_client::test_longer_query_timeout_waits_for_a_slow_query | passed | 0.11 |  |
| backend.tests.unit.test_zetaris_client::test_rejects_query_timeouts_out_of_range\[0.0\] | passed | 0.01 |  |
| backend.tests.unit.test_zetaris_client::test_rejects_query_timeouts_out_of_range\[-1.0\] | passed | 0.01 |  |
| backend.tests.unit.test_zetaris_client::test_rejects_query_timeouts_out_of_range\[601.0\] | passed | 0.01 |  |
| backend.tests.unit.test_zetaris_client::test_retry_after_is_honoured_but_capped | passed | 0.03 |  |
| backend.tests.unit.test_zetaris_client::test_logs_query_done_without_sql_or_rows | passed | 0.02 |  |
| backend.tests.unit.test_zetaris_client::test_logs_retry_as_warning | passed | 0.02 |  |
| backend.tests.unit.test_zetaris_sql::test_split_handles_quotes_comments_and_a_last_statement_without_semicolon | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_split_ignores_empty_statements | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_split_keeps_doubled_quotes_inside_strings | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_every_create_file_has_a_select_file | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_file_names_follow_the_convention\[10_erp_datasource_create.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_file_names_follow_the_convention\[10_erp_datasource_select.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_file_names_follow_the_convention\[20_edgar_suppliers_create.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_file_names_follow_the_convention\[20_edgar_suppliers_select.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_file_names_follow_the_convention\[30_eurostat_countries_create.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_file_names_follow_the_convention\[30_eurostat_countries_select.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_file_names_follow_the_convention\[40_risk_views_create.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_file_names_follow_the_convention\[40_risk_views_select.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_file_names_follow_the_convention\[60_roles_grants_create.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_file_names_follow_the_convention\[60_roles_grants_select.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_statements_have_no_trailing_semicolon_and_none_is_empty\[10_erp_datasource_create.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_statements_have_no_trailing_semicolon_and_none_is_empty\[10_erp_datasource_select.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_statements_have_no_trailing_semicolon_and_none_is_empty\[20_edgar_suppliers_create.sql\] | passed | 0.01 |  |
| backend.tests.unit.test_zetaris_sql::test_statements_have_no_trailing_semicolon_and_none_is_empty\[20_edgar_suppliers_select.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_statements_have_no_trailing_semicolon_and_none_is_empty\[30_eurostat_countries_create.sql\] | passed | 0.01 |  |
| backend.tests.unit.test_zetaris_sql::test_statements_have_no_trailing_semicolon_and_none_is_empty\[30_eurostat_countries_select.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_statements_have_no_trailing_semicolon_and_none_is_empty\[40_risk_views_create.sql\] | passed | 0.01 |  |
| backend.tests.unit.test_zetaris_sql::test_statements_have_no_trailing_semicolon_and_none_is_empty\[40_risk_views_select.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_statements_have_no_trailing_semicolon_and_none_is_empty\[60_roles_grants_create.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_statements_have_no_trailing_semicolon_and_none_is_empty\[60_roles_grants_select.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_no_select_star\[10_erp_datasource_create.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_no_select_star\[10_erp_datasource_select.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_no_select_star\[20_edgar_suppliers_create.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_no_select_star\[20_edgar_suppliers_select.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_no_select_star\[30_eurostat_countries_create.sql\] | passed | 0.01 |  |
| backend.tests.unit.test_zetaris_sql::test_no_select_star\[30_eurostat_countries_select.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_no_select_star\[40_risk_views_create.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_no_select_star\[40_risk_views_select.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_no_select_star\[60_roles_grants_create.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_no_select_star\[60_roles_grants_select.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_no_secrets_or_emails\[10_erp_datasource_create.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_no_secrets_or_emails\[10_erp_datasource_select.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_no_secrets_or_emails\[20_edgar_suppliers_create.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_no_secrets_or_emails\[20_edgar_suppliers_select.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_no_secrets_or_emails\[30_eurostat_countries_create.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_no_secrets_or_emails\[30_eurostat_countries_select.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_no_secrets_or_emails\[40_risk_views_create.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_no_secrets_or_emails\[40_risk_views_select.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_no_secrets_or_emails\[60_roles_grants_create.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_no_secrets_or_emails\[60_roles_grants_select.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_user_agent_headers_use_the_placeholder\[10_erp_datasource_create.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_user_agent_headers_use_the_placeholder\[10_erp_datasource_select.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_user_agent_headers_use_the_placeholder\[20_edgar_suppliers_create.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_user_agent_headers_use_the_placeholder\[20_edgar_suppliers_select.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_user_agent_headers_use_the_placeholder\[30_eurostat_countries_create.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_user_agent_headers_use_the_placeholder\[30_eurostat_countries_select.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_user_agent_headers_use_the_placeholder\[40_risk_views_create.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_user_agent_headers_use_the_placeholder\[40_risk_views_select.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_user_agent_headers_use_the_placeholder\[60_roles_grants_create.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_user_agent_headers_use_the_placeholder\[60_roles_grants_select.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_select_files_only_read\[10_erp_datasource_create.sql\] | skipped | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_select_files_only_read\[10_erp_datasource_select.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_select_files_only_read\[20_edgar_suppliers_create.sql\] | skipped | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_select_files_only_read\[20_edgar_suppliers_select.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_select_files_only_read\[30_eurostat_countries_create.sql\] | skipped | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_select_files_only_read\[30_eurostat_countries_select.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_select_files_only_read\[40_risk_views_create.sql\] | skipped | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_select_files_only_read\[40_risk_views_select.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_select_files_only_read\[60_roles_grants_create.sql\] | skipped | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_select_files_only_read\[60_roles_grants_select.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_committed_files_match_the_generator | passed | 0.01 |  |
| backend.tests.unit.test_zetaris_sql::test_check_mode_reports_a_stale_file | passed | 0.03 |  |
| backend.tests.unit.test_zetaris_sql::test_erp_datasource_uses_the_reader_and_a_password_placeholder | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_edgar_has_one_rest_table_and_one_view_per_public_supplier | passed | 0.04 |  |
| backend.tests.unit.test_zetaris_sql::test_edgar_skips_private_suppliers | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_eurostat_has_both_indicators_for_all_six_countries | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_eurostat_uses_the_live_hicp_dataset | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_eurostat_views_decode_with_both_coercions | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_risk_views_are_exactly_the_app_facing_views | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_buyer_views_hide_prices_and_contract_values\[v_country_indicators\] | passed | 0.01 |  |
| backend.tests.unit.test_zetaris_sql::test_buyer_views_hide_prices_and_contract_values\[v_deliveries\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_buyer_views_hide_prices_and_contract_values\[v_open_orders_buyer\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_buyer_views_hide_prices_and_contract_values\[v_part_sourcing\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_buyer_views_hide_prices_and_contract_values\[v_suppliers_buyer\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_full_views_carry_the_hidden_columns | passed | 0.01 |  |
| backend.tests.unit.test_zetaris_sql::test_supplier_revenue_maps_each_cik_to_its_supplier | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_country_indicators_cover_every_country_and_indicator | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_risk_views_read_only_registered_sources | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_roles_are_created_for_buyer_and_risk_manager_only | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_role_descriptions_use_only_the_characters_zetaris_accepts | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_role_descriptions_are_the_ones_that_ran_in_zetaris | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_buyer_gets_only_the_buyer_views | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_risk_manager_gets_every_risk_view | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_grants_file_holds_only_roles_and_grants | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_setup_doc_lists_every_manual_statement\[10_erp_datasource_create.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_setup_doc_lists_every_manual_statement\[60_roles_grants_create.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_no_file_drops_anything\[10_erp_datasource_create.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_no_file_drops_anything\[10_erp_datasource_select.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_no_file_drops_anything\[20_edgar_suppliers_create.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_no_file_drops_anything\[20_edgar_suppliers_select.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_no_file_drops_anything\[30_eurostat_countries_create.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_no_file_drops_anything\[30_eurostat_countries_select.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_no_file_drops_anything\[40_risk_views_create.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_no_file_drops_anything\[40_risk_views_select.sql\] | passed | 0.06 |  |
| backend.tests.unit.test_zetaris_sql::test_no_file_drops_anything\[60_roles_grants_create.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_no_file_drops_anything\[60_roles_grants_select.sql\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_sql::test_every_candidate_tag_has_a_distinct_short_table_slug | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_verify::test_checks_every_risk_view_once | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_verify::test_sql_counts_rows_and_reads_the_date_range | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_verify::test_sql_without_a_date_column_only_counts | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_verify::test_sql_refuses_names_that_are_not_identifiers\[v_x; DROP-None\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_verify::test_sql_refuses_names_that_are_not_identifiers\[v_x-a b\] | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_verify::test_format_table_is_markdown | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_verify::test_main_prints_the_table_and_exits_zero | passed | 0.02 |  |
| backend.tests.unit.test_zetaris_verify::test_main_waits_long_enough_for_live_rest_views | passed | 0.03 |  |
| backend.tests.unit.test_zetaris_verify::test_main_skips_when_the_risk_key_is_missing | passed | 0.00 |  |
| backend.tests.unit.test_zetaris_verify::test_main_skips_when_the_key_has_no_access | passed | 0.02 |  |
| backend.tests.unit.test_zetaris_verify::test_main_fails_on_a_broken_view | passed | 0.02 |  |
| backend.tests.unit.test_zetaris_verify::test_main_names_the_view_that_failed_and_keeps_the_others | passed | 0.02 |  |
