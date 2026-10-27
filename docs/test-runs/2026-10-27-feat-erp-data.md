# Test run 2026-10-27, branch feat/erp-data

Synthetic ERP: data_pipeline/suppliers.yaml and loader, synthetic_erp/generate.py (seed 42, `today` argument),
docker-compose erp-db with the read-only role zetaris_reader, scripts/erp_up.sh, scripts/verify_ciks.py.
Tests were written first and run red (modules missing at collection), then green. Coverage 98 % (backend/app,
scripts, data_pipeline). ruff, ruff format and mypy strict clean.

Live checks: scripts/verify_ciks.py against SEC EDGAR (8/8 CIKs and revenue tags PASS, see docs/data-contract.md);
`scripts/erp_up.sh up` started erp-db healthy and loaded the seed; integration tests ran against it as
zetaris_reader. Zetaris and Nightshift were not needed for this area and were not run.

Security pass (security-breaker): no Critical or High; SEC-031 to SEC-043 (6 Medium, 7 Low) recorded in
docs/security-review.md, SEC-031 to SEC-041 as strict xfail tests (backend/tests/security/test_erp_redteam.py).
SEC-031 and SEC-033 were then fixed test first (markers removed: red, then green; erp-db rebuilt from an empty
volume so the live reader test ran against the fixed role script). SEC-032 stays xfail by design.
No logs/errors.jsonl exists after the runs, so no unexpected ERROR/CRITICAL lines.

## Unit tests

| Tests | Passed | Failed | Errors | Skipped | XFailed | Duration |
|---|---|---|---|---|---|---|
| 494 | 473 | 0 | 0 | 0 | 21 | 8.44s |

| Test | Outcome | Seconds | Failure |
|---|---|---|---|
| backend.tests.security.test_core_api_security::test_unlisted_secret_shapes_are_redacted\[login failed password=hunter2-hunter2\] | passed | 0.05 |  |
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
| backend.tests.security.test_erp_redteam::test_reader_role_has_resource_limits | passed | 0.00 |  |
| backend.tests.security.test_erp_redteam::test_erp_up_copies_the_files_that_the_generator_just_wrote | xfailed | 0.63 |  |
| backend.tests.security.test_erp_redteam::test_reader_password_is_not_in_the_container_environment | xfailed | 0.00 |  |
| backend.tests.security.test_erp_redteam::test_env_example_app_url_uses_the_read_only_role | xfailed | 0.00 |  |
| backend.tests.security.test_erp_redteam::test_sql_literal_handles_numpy_scalars_and_non_finite_floats | xfailed | 0.00 |  |
| backend.tests.security.test_erp_redteam::test_sql_literal_escapes_quotes_backslashes_and_newlines | passed | 0.00 |  |
| backend.tests.security.test_erp_redteam::test_rate_limit_holds_across_consecutive_ciks | xfailed | 0.00 |  |
| backend.tests.security.test_erp_redteam::test_verify_fails_when_yaml_tag_differs_from_the_tag_found | xfailed | 1.03 |  |
| backend.tests.security.test_erp_redteam::test_markdown_row_neutralises_pipes_and_newlines_from_sec_names | xfailed | 0.01 |  |
| backend.tests.security.test_erp_redteam::test_non_json_200_is_reported_as_a_failed_check_not_a_traceback | xfailed | 0.00 |  |
| backend.tests.security.test_erp_redteam::test_rate_limited_response_is_not_reported_as_an_invalid_cik | xfailed | 0.01 |  |
| backend.tests.security.test_llm_redteam::test_non_ascii_digit_retry_after_does_not_crash | passed | 0.06 |  |
| backend.tests.security.test_llm_redteam::test_deeply_nested_tool_arguments_raise_llm_error | passed | 0.03 |  |
| backend.tests.security.test_llm_redteam::test_deeply_nested_body_raises_llm_error | passed | 0.05 |  |
| backend.tests.security.test_llm_redteam::test_status_with_deeply_nested_body_is_offline | passed | 0.02 |  |
| backend.tests.security.test_llm_redteam::test_unclosed_think_block_is_not_returned | passed | 0.00 |  |
| backend.tests.security.test_llm_redteam::test_think_end_tag_case_variants_are_stripped | passed | 0.00 |  |
| backend.tests.security.test_llm_redteam::test_oversized_chat_content_is_rejected | passed | 0.04 |  |
| backend.tests.security.test_llm_redteam::test_tool_call_count_is_bounded | passed | 0.03 |  |
| backend.tests.security.test_llm_redteam::test_embedding_non_finite_values_are_rejected | passed | 0.02 |  |
| backend.tests.security.test_llm_redteam::test_embedding_dimensions_must_match | passed | 0.03 |  |
| backend.tests.security.test_llm_redteam::test_chat_has_a_total_deadline | passed | 0.68 |  |
| backend.tests.security.test_llm_redteam::test_shared_limiter_works_across_event_loops | xfailed | 0.01 |  |
| backend.tests.security.test_llm_redteam::test_chat_rejects_oversized_message_content | passed | 0.03 |  |
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
| backend.tests.security.test_llm_security::test_token_is_not_in_client_repr | passed | 0.01 |  |
| backend.tests.security.test_llm_security::test_errors_do_not_echo_upstream_body_or_token\[400-LLMRequestError\] | passed | 0.02 |  |
| backend.tests.security.test_llm_security::test_errors_do_not_echo_upstream_body_or_token\[401-LLMUnauthorized\] | passed | 0.02 |  |
| backend.tests.security.test_llm_security::test_errors_do_not_echo_upstream_body_or_token\[503-LLMUnavailable\] | passed | 0.02 |  |
| backend.tests.security.test_llm_security::test_does_not_follow_redirects_with_the_token | passed | 0.02 |  |
| backend.tests.security.test_llm_security::test_receipt_fields_from_upstream_are_bounded | passed | 0.02 |  |
| backend.tests.security.test_llm_security::test_tool_arguments_are_never_evaluated | passed | 0.02 |  |
| backend.tests.security.test_llm_security::test_settings_reject_credentials_in_the_base_url | passed | 0.00 |  |
| backend.tests.security.test_llm_security::test_wrong_token_is_unauthorized_not_offline | passed | 0.04 |  |
| backend.tests.unit.test_api_app::test_accepts_and_returns_a_valid_request_id | passed | 0.02 |  |
| backend.tests.unit.test_api_app::test_creates_a_request_id_when_missing | passed | 0.01 |  |
| backend.tests.unit.test_api_app::test_replaces_invalid_request_ids\[xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\] | passed | 0.01 |  |
| backend.tests.unit.test_api_app::test_replaces_invalid_request_ids\[has space\] | passed | 0.01 |  |
| backend.tests.unit.test_api_app::test_replaces_invalid_request_ids\[line\\\\nbreak\] | passed | 0.01 |  |
| backend.tests.unit.test_api_app::test_replaces_invalid_request_ids\[semi;colon\] | passed | 0.01 |  |
| backend.tests.unit.test_api_app::test_replaces_invalid_request_ids\[\<script\>\] | passed | 0.01 |  |
| backend.tests.unit.test_api_app::test_request_ids_differ_between_requests | passed | 0.01 |  |
| backend.tests.unit.test_api_app::test_logs_one_info_line_per_request | passed | 0.01 |  |
| backend.tests.unit.test_api_app::test_access_log_has_request_id_in_json | passed | 0.01 |  |
| backend.tests.unit.test_api_app::test_logs_status_500_for_unhandled_errors | passed | 0.02 |  |
| backend.tests.unit.test_api_app::test_health_is_always_ok | passed | 0.01 |  |
| backend.tests.unit.test_api_app::test_health_stays_ok_when_a_readiness_check_fails | passed | 0.01 |  |
| backend.tests.unit.test_api_app::test_ready_is_ok_with_no_checks | passed | 0.01 |  |
| backend.tests.unit.test_api_app::test_ready_runs_registered_checks | passed | 0.01 |  |
| backend.tests.unit.test_api_app::test_ready_returns_503_naming_only_failed_checks | passed | 0.01 |  |
| backend.tests.unit.test_api_app::test_ready_treats_exception_as_failed_without_leaking_it | passed | 0.01 |  |
| backend.tests.unit.test_api_app::test_ready_treats_a_slow_check_as_failed | passed | 0.06 |  |
| backend.tests.unit.test_api_app::test_docs_available_in_dev | passed | 0.01 |  |
| backend.tests.unit.test_api_app::test_docs_hidden_in_prod | passed | 0.01 |  |
| backend.tests.unit.test_api_app::test_small_body_is_accepted | passed | 0.01 |  |
| backend.tests.unit.test_api_app::test_body_over_16kb_is_rejected_by_content_length | passed | 0.01 |  |
| backend.tests.unit.test_api_app::test_body_exactly_16kb_is_accepted | passed | 0.01 |  |
| backend.tests.unit.test_api_app::test_chunked_body_over_limit_is_rejected | passed | 0.01 |  |
| backend.tests.unit.test_api_app::test_invalid_content_length_is_a_bad_request | passed | 0.01 |  |
| backend.tests.unit.test_architecture::test_real_code_follows_the_layer_rule | passed | 0.03 |  |
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
| backend.tests.unit.test_check_llm::test_routed_model_mismatch_fails_the_plain_text_check | passed | 0.03 |  |
| backend.tests.unit.test_check_llm::test_format_result | passed | 0.00 |  |
| backend.tests.unit.test_check_llm::test_main_prints_results_without_secrets_and_exits_zero | passed | 0.03 |  |
| backend.tests.unit.test_check_llm::test_main_exits_one_on_failure | passed | 0.03 |  |
| backend.tests.unit.test_check_llm::test_every_chat_prompt_carries_a_fresh_run_id | passed | 0.03 |  |
| backend.tests.unit.test_erp_compose::test_uses_postgres_16 | passed | 0.00 |  |
| backend.tests.unit.test_erp_compose::test_port_is_bound_to_localhost_only | passed | 0.00 |  |
| backend.tests.unit.test_erp_compose::test_data_lives_in_a_named_volume | passed | 0.00 |  |
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
| backend.tests.unit.test_erp_generate::test_same_seed_gives_identical_rows | passed | 0.13 |  |
| backend.tests.unit.test_erp_generate::test_different_seed_gives_different_rows | passed | 0.04 |  |
| backend.tests.unit.test_erp_generate::test_default_seed_is_42 | passed | 0.04 |  |
| backend.tests.unit.test_erp_generate::test_written_files_are_byte_identical_across_runs | passed | 0.16 |  |
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
| backend.tests.unit.test_erp_generate::test_story_1_one_supplier_has_rising_late_deliveries | passed | 0.00 |  |
| backend.tests.unit.test_erp_generate::test_story_1_supplier_is_public | passed | 0.00 |  |
| backend.tests.unit.test_erp_generate::test_story_2_exactly_one_single_source_a_part_with_a_large_open_order | passed | 0.00 |  |
| backend.tests.unit.test_erp_generate::test_story_3_one_supplier_sits_in_the_worsening_country | passed | 0.00 |  |
| backend.tests.unit.test_erp_generate::test_story_3_country_is_a_single_parameter | passed | 0.04 |  |
| backend.tests.unit.test_erp_generate::test_story_3_rejects_non_eu_site_countries\[US\] | passed | 0.00 |  |
| backend.tests.unit.test_erp_generate::test_story_3_rejects_non_eu_site_countries\[cz\] | passed | 0.00 |  |
| backend.tests.unit.test_erp_generate::test_story_3_rejects_non_eu_site_countries\[\] | passed | 0.00 |  |
| backend.tests.unit.test_erp_generate::test_story_3_rejects_non_eu_site_countries\[XX\] | passed | 0.00 |  |
| backend.tests.unit.test_erp_generate::test_story_4_exactly_one_buyer_note_has_the_prompt_injection | passed | 0.00 |  |
| backend.tests.unit.test_erp_generate::test_write_outputs_creates_schema_seed_and_csvs | passed | 0.04 |  |
| backend.tests.unit.test_erp_generate::test_seed_escapes_quotes_in_text | passed | 0.04 |  |
| backend.tests.unit.test_erp_generate::test_csv_round_trips_row_counts | passed | 0.05 |  |
| backend.tests.unit.test_erp_generate::test_data_card_documents_each_story_and_the_synthetic_labels | passed | 0.00 |  |
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
| backend.tests.unit.test_erp_suppliers_yaml::test_rejects_duplicate_ids | passed | 0.00 |  |
| backend.tests.unit.test_erp_suppliers_yaml::test_rejects_unknown_fields | passed | 0.00 |  |
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
| backend.tests.unit.test_llm_client_chat::test_role_selects_the_configured_model\[fast-meterless_fast_model\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_rejects_unknown_role_without_a_request | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_rejects_empty_or_oversized_message_lists\[messages0\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_rejects_empty_or_oversized_message_lists\[messages1\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_passes_tools_tool_choice_and_response_format | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_strips_reasoning_up_to_think_end\[\<think\>secret plan\</think\>Final\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_strips_reasoning_up_to_think_end\[no opener, still reasoning\</think\>\\nFinal\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_parses_tool_calls_and_tolerates_null_content | passed | 0.02 |  |
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
| backend.tests.unit.test_llm_client_chat::test_honours_retry_after_seconds_with_a_cap\[7-7.0\] | passed | 0.03 |  |
| backend.tests.unit.test_llm_client_chat::test_honours_retry_after_seconds_with_a_cap\[0-0.0\] | passed | 0.04 |  |
| backend.tests.unit.test_llm_client_chat::test_honours_retry_after_seconds_with_a_cap\[3600-60.0\] | passed | 0.03 |  |
| backend.tests.unit.test_llm_client_chat::test_honours_retry_after_http_date | passed | 0.03 |  |
| backend.tests.unit.test_llm_client_chat::test_unusable_retry_after_falls_back_to_backoff\[soon\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_unusable_retry_after_falls_back_to_backoff\[-5\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_unusable_retry_after_falls_back_to_backoff\[\] | passed | 0.03 |  |
| backend.tests.unit.test_llm_client_chat::test_gives_up_after_four_attempts_on_5xx | passed | 0.03 |  |
| backend.tests.unit.test_llm_client_chat::test_gives_up_after_four_attempts_on_timeouts | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_gives_up_after_four_attempts_on_429 | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_unauthorized_is_not_retried\[401\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_unauthorized_is_not_retried\[403\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_other_non_success_codes_are_not_retried\[302\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_other_non_success_codes_are_not_retried\[400\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_other_non_success_codes_are_not_retried\[404\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_other_non_success_codes_are_not_retried\[422\] | passed | 0.03 |  |
| backend.tests.unit.test_llm_client_chat::test_deep_falls_back_to_default_once_on_persistent_429 | passed | 0.03 |  |
| backend.tests.unit.test_llm_client_chat::test_deep_does_not_fall_back_on_5xx | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_fast_does_not_fall_back_on_429 | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_fallback_happens_only_once | passed | 0.03 |  |
| backend.tests.unit.test_llm_client_chat::test_budget_counts_one_per_call_even_with_retries_and_fallback | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_exhausted_budget_blocks_the_request | passed | 0.03 |  |
| backend.tests.unit.test_llm_client_chat::test_every_attempt_goes_through_the_rate_limiter | passed | 0.03 |  |
| backend.tests.unit.test_llm_client_chat::test_truncated_answer_raises_bad_response | passed | 0.03 |  |
| backend.tests.unit.test_llm_client_chat::test_oversized_response_body_raises_bad_response | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_rejects_too_many_tools_without_a_request | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_stops_retrying_when_the_wait_would_pass_the_deadline | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_attempt_timeout_is_retried | passed | 0.10 |  |
| backend.tests.unit.test_llm_client_chat::test_records_cache_status_from_the_receipt\[cache0-exact-True\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_records_cache_status_from_the_receipt\[cache1-miss-False\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_records_cache_status_from_the_receipt\[None-None-False\] | passed | 0.09 |  |
| backend.tests.unit.test_llm_client_chat::test_records_cache_status_from_the_receipt\[exact-None-False\] | passed | 0.04 |  |
| backend.tests.unit.test_llm_client_chat::test_records_cache_status_from_the_receipt\[cache4-None-False\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_routed_model_falls_back_to_the_winning_route | passed | 0.03 |  |
| backend.tests.unit.test_llm_client_chat::test_empty_reply_is_retried_once_with_a_fresh_request_id\[empty0-tools0\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_empty_reply_is_retried_once_with_a_fresh_request_id\[empty1-tools1\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_empty_reply_is_retried_once_with_a_fresh_request_id\[empty2-None\] | passed | 0.03 |  |
| backend.tests.unit.test_llm_client_chat::test_each_empty_retry_uses_a_new_request_id | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_empty_reply_twice_raises_bad_response | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_text_reply_without_tools_is_not_treated_as_empty\[body0\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_chat::test_text_reply_without_tools_is_not_treated_as_empty\[body1\] | passed | 0.03 |  |
| backend.tests.unit.test_llm_client_embed::test_default_batch_size_is_32 | passed | 0.00 |  |
| backend.tests.unit.test_llm_client_embed::test_returns_vectors_in_input_order | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_embed::test_sends_batches_with_the_embed_model_and_no_scope | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_embed::test_empty_input_makes_no_request | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_embed::test_malformed_embeddings_raise_bad_response\[body0\] | passed | 0.03 |  |
| backend.tests.unit.test_llm_client_embed::test_malformed_embeddings_raise_bad_response\[body1\] | passed | 0.03 |  |
| backend.tests.unit.test_llm_client_embed::test_malformed_embeddings_raise_bad_response\[body2\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_embed::test_malformed_embeddings_raise_bad_response\[body3\] | passed | 0.03 |  |
| backend.tests.unit.test_llm_client_embed::test_malformed_embeddings_raise_bad_response\[body4\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_embed::test_rejects_oversized_text_without_a_request | passed | 0.03 |  |
| backend.tests.unit.test_llm_client_embed::test_retries_on_429 | passed | 0.03 |  |
| backend.tests.unit.test_llm_client_embed::test_dimensions_must_match_across_batches | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_embed::test_oversized_vectors_are_rejected | passed | 0.04 |  |
| backend.tests.unit.test_llm_client_logging::test_one_info_line_per_call_with_receipt_fields | passed | 0.03 |  |
| backend.tests.unit.test_llm_client_logging::test_retry_is_logged_as_warning | passed | 0.04 |  |
| backend.tests.unit.test_llm_client_logging::test_fallback_is_logged_as_warning | passed | 0.03 |  |
| backend.tests.unit.test_llm_client_logging::test_final_failure_is_logged_as_error | passed | 0.03 |  |
| backend.tests.unit.test_llm_client_logging::test_status_is_logged | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_logging::test_embed_call_is_logged | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_logging::test_never_logs_prompts_contents_tool_arguments_or_token | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_logging::test_model_call_logs_cache_status | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_logging::test_empty_reply_retry_is_logged_as_warning | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_status::test_online_lists_all_model_ids_including_unknown_ones | passed | 0.12 |  |
| backend.tests.unit.test_llm_client_status::test_returns_unauthorized_on_401_and_403\[401\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_status::test_returns_unauthorized_on_401_and_403\[403\] | passed | 0.03 |  |
| backend.tests.unit.test_llm_client_status::test_returns_offline_on_other_status_codes\[404\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_status::test_returns_offline_on_other_status_codes\[429\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_status::test_returns_offline_on_other_status_codes\[500\] | passed | 0.02 |  |
| backend.tests.unit.test_llm_client_status::test_returns_offline_on_other_status_codes\[502\] | passed | 0.03 |  |
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
| backend.tests.unit.test_llm_rate_limiter::test_limits_concurrent_calls_to_two | passed | 0.04 |  |
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
| backend.tests.unit.test_logging::test_creates_log_dir_and_both_files | passed | 0.00 |  |
| backend.tests.unit.test_logging::test_record_has_required_fields | passed | 0.00 |  |
| backend.tests.unit.test_logging::test_event_defaults_when_not_given | passed | 0.00 |  |
| backend.tests.unit.test_logging::test_request_and_investigation_ids_come_from_contextvars | passed | 0.00 |  |
| backend.tests.unit.test_logging::test_ids_are_null_outside_a_request | passed | 0.00 |  |
| backend.tests.unit.test_logging::test_extra_fields_are_included | passed | 0.00 |  |
| backend.tests.unit.test_logging::test_errors_file_only_has_warning_and_above | passed | 0.00 |  |
| backend.tests.unit.test_logging::test_log_level_setting_filters_app_file | passed | 0.00 |  |
| backend.tests.unit.test_logging::test_exception_is_logged_with_stack_trace | passed | 0.00 |  |
| backend.tests.unit.test_logging::test_rotation_is_10mb_times_5 | passed | 0.00 |  |
| backend.tests.unit.test_logging::test_console_handler_writes_json | passed | 0.00 |  |
| backend.tests.unit.test_logging::test_setup_is_idempotent | passed | 0.00 |  |
| backend.tests.unit.test_logging::test_teardown_removes_only_our_handlers | passed | 0.00 |  |
| backend.tests.unit.test_main::test_create_builds_app_and_configures_logging | passed | 0.00 |  |
| backend.tests.unit.test_main::test_create_logs_critical_and_reraises_on_bad_settings | passed | 0.00 |  |
| backend.tests.unit.test_redaction::test_redacts_bearer_token | passed | 0.00 |  |
| backend.tests.unit.test_redaction::test_redacts_authorization_header_forms | passed | 0.00 |  |
| backend.tests.unit.test_redaction::test_redacts_nvapi_keys | passed | 0.00 |  |
| backend.tests.unit.test_redaction::test_redacts_each_setting_secret\[zk-secret-VALUE-1111\] | passed | 0.00 |  |
| backend.tests.unit.test_redaction::test_redacts_each_setting_secret\[mt-secret-VALUE-2222\] | passed | 0.00 |  |
| backend.tests.unit.test_redaction::test_redacts_each_setting_secret\[erp-pass-VALUE-3333\] | passed | 0.00 |  |
| backend.tests.unit.test_redaction::test_redacts_each_setting_secret\[app-pass-VALUE-4444\] | passed | 0.00 |  |
| backend.tests.unit.test_redaction::test_redacts_each_setting_secret\[postgresql://erp:erp-pass-VALUE-3333@localhost:5433/erp\] | passed | 0.00 |  |
| backend.tests.unit.test_redaction::test_redacts_sensitive_dict_keys_but_not_token_counts | passed | 0.00 |  |
| backend.tests.unit.test_redaction::test_redacts_nested_structures_and_objects | passed | 0.00 |  |
| backend.tests.unit.test_redaction::test_every_handler_has_the_redaction_filter | passed | 0.01 |  |
| backend.tests.unit.test_redaction::test_secret_in_message_args_and_extra_never_reaches_files\[zk-secret-VALUE-1111\] | passed | 0.01 |  |
| backend.tests.unit.test_redaction::test_secret_in_message_args_and_extra_never_reaches_files\[mt-secret-VALUE-2222\] | passed | 0.00 |  |
| backend.tests.unit.test_redaction::test_secret_in_message_args_and_extra_never_reaches_files\[erp-pass-VALUE-3333\] | passed | 0.01 |  |
| backend.tests.unit.test_redaction::test_secret_in_message_args_and_extra_never_reaches_files\[app-pass-VALUE-4444\] | passed | 0.01 |  |
| backend.tests.unit.test_redaction::test_secret_in_message_args_and_extra_never_reaches_files\[postgresql://erp:erp-pass-VALUE-3333@localhost:5433/erp\] | passed | 0.01 |  |
| backend.tests.unit.test_redaction::test_secret_in_exception_text_and_traceback_is_redacted | passed | 0.01 |  |
| backend.tests.unit.test_redaction::test_secret_in_stack_info_is_redacted | passed | 0.04 |  |
| backend.tests.unit.test_settings::test_loads_values_from_environment | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_defaults_are_safe | passed | 0.01 |  |
| backend.tests.unit.test_settings::test_secret_fields_are_secretstr_and_not_in_repr\[zetaris_app_api_key\] | passed | 0.01 |  |
| backend.tests.unit.test_settings::test_secret_fields_are_secretstr_and_not_in_repr\[meterless_runtime_token\] | passed | 0.01 |  |
| backend.tests.unit.test_settings::test_secret_fields_are_secretstr_and_not_in_repr\[erp_database_url\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_secret_fields_are_secretstr_and_not_in_repr\[app_database_url\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_secret_values_include_every_secret_and_database_passwords | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_missing_required_value_names_the_variable | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_reports_all_missing_variables_at_once | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_empty_value_counts_as_missing | passed | 0.01 |  |
| backend.tests.unit.test_settings::test_invalid_value_names_variable_but_not_value\[APP_ENV-staging\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_invalid_value_names_variable_but_not_value\[TOOL_CACHE_TTL_SECONDS-abc\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_invalid_value_names_variable_but_not_value\[LOG_LEVEL-LOUD\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_settings_error_hides_the_original_validation_error | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_log_level_is_case_insensitive | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_meterless_base_url_is_normalised\[http://127.0.0.1:8088/v1\] | passed | 0.01 |  |
| backend.tests.unit.test_settings::test_meterless_base_url_is_normalised\[http://127.0.0.1:8088\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_meterless_base_url_is_normalised\[http://127.0.0.1:8088/\] | passed | 0.01 |  |
| backend.tests.unit.test_settings::test_meterless_base_url_is_normalised\[127.0.0.1:8088\] | passed | 0.01 |  |
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
| backend.tests.unit.test_settings::test_routing_modes_are_rejected_as_model_ids\[meterless/fusion-meterless_embed_model\] | passed | 0.01 |  |
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
| backend.tests.unit.test_settings::test_plain_http_is_allowed_only_for_local_hosts\[http://127.0.0.2:8088/v1\] | passed | 0.01 |  |
| backend.tests.unit.test_settings::test_plain_http_is_allowed_only_for_local_hosts\[http://\[::1\]:8088/v1\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_plain_http_is_allowed_only_for_local_hosts\[http://host.docker.internal:8088/v1\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_plain_http_is_allowed_only_for_local_hosts\[https://nightshift.example.com/v1\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_plain_http_to_a_remote_host_is_rejected\[http://10.0.0.5:8088/v1\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_plain_http_to_a_remote_host_is_rejected\[nightshift.example.com\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_runtime_token_must_be_visible_ascii\[has space\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_runtime_token_must_be_visible_ascii\[tab\\tinside\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_runtime_token_must_be_visible_ascii\[RT-Local\] | passed | 0.00 |  |
| backend.tests.unit.test_settings::test_runtime_token_must_be_visible_ascii\[xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx... | passed | 0.00 |  |
| backend.tests.unit.test_smoke::test_app_package_imports | passed | 0.00 |  |
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
| backend.tests.unit.test_verify_ciks::test_pads_cik_to_ten_digits | passed | 0.00 |  |
| backend.tests.unit.test_verify_ciks::test_rejects_malformed_cik\[abc\] | passed | 0.00 |  |
| backend.tests.unit.test_verify_ciks::test_rejects_malformed_cik\[\] | passed | 0.00 |  |
| backend.tests.unit.test_verify_ciks::test_rejects_malformed_cik\[12345678901\] | passed | 0.00 |  |
| backend.tests.unit.test_verify_ciks::test_rejects_malformed_cik\[-1\] | passed | 0.00 |  |
| backend.tests.unit.test_verify_ciks::test_uses_revenues_tag_when_present | passed | 0.02 |  |
| backend.tests.unit.test_verify_ciks::test_falls_back_to_contract_revenue_tag | passed | 0.02 |  |
| backend.tests.unit.test_verify_ciks::test_fails_when_no_revenue_tag_exists | passed | 0.02 |  |
| backend.tests.unit.test_verify_ciks::test_unknown_cik_fails_without_concept_calls | passed | 0.01 |  |
| backend.tests.unit.test_verify_ciks::test_sends_the_declared_user_agent | passed | 0.01 |  |
| backend.tests.unit.test_verify_ciks::test_limits_request_rate_to_two_per_second | passed | 0.53 |  |
| backend.tests.unit.test_verify_ciks::test_lookup_finds_cik_by_ticker | passed | 0.06 |  |
| backend.tests.unit.test_verify_ciks::test_markdown_row_never_contains_user_agent | passed | 0.00 |  |
| backend.tests.unit.test_verify_ciks::test_main_refuses_to_run_without_user_agent | passed | 0.00 |  |
| backend.tests.unit.test_verify_ciks::test_main_lookup_prints_found_and_missing | passed | 0.01 |  |
| backend.tests.unit.test_verify_ciks::test_main_verify_reports_failures_with_exit_code_1 | passed | 0.03 |  |
| backend.tests.unit.test_verify_ciks::test_main_rejects_unknown_command | passed | 0.00 |  |

## Integration tests

| Tests | Passed | Failed | Errors | Skipped | XFailed | Duration |
|---|---|---|---|---|---|---|
| 11 | 10 | 0 | 0 | 0 | 1 | 0.21s |

| Test | Outcome | Seconds | Failure |
|---|---|---|---|
| backend.tests.integration.test_erp_db::test_reader_counts_rows_in_every_table | passed | 0.15 |  |
| backend.tests.integration.test_erp_db::test_reader_sees_the_planted_prompt_injection_note | passed | 0.00 |  |
| backend.tests.integration.test_erp_db::test_reader_cannot_write_or_change_the_schema\[INSERT INTO suppliers (supplier_id, name, country_iso, category, site_lat, site_lon) VALUES ('S9999', 'x', 'DE',... | passed | 0.00 |  |
| backend.tests.integration.test_erp_db::test_reader_cannot_write_or_change_the_schema\[UPDATE suppliers SET name = 'x'\] | passed | 0.00 |  |
| backend.tests.integration.test_erp_db::test_reader_cannot_write_or_change_the_schema\[DELETE FROM suppliers\] | passed | 0.00 |  |
| backend.tests.integration.test_erp_db::test_reader_cannot_write_or_change_the_schema\[TRUNCATE suppliers\] | passed | 0.00 |  |
| backend.tests.integration.test_erp_db::test_reader_cannot_write_or_change_the_schema\[DROP TABLE suppliers\] | passed | 0.00 |  |
| backend.tests.integration.test_erp_db::test_reader_cannot_write_or_change_the_schema\[CREATE TABLE evil (id int)\] | passed | 0.00 |  |
| backend.tests.integration.test_erp_db::test_reader_cannot_write_or_change_the_schema\[CREATE ROLE evil\] | passed | 0.00 |  |
| backend.tests.security.test_erp_redteam::test_reader_cannot_write_after_turning_read_only_off | passed | 0.02 |  |
| backend.tests.security.test_erp_redteam::test_reader_cannot_change_its_own_role_settings_or_password | xfailed | 0.01 |  |
