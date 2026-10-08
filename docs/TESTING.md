# What the tests prove

Run everything with `make test` (backend tests and lint) and `make web-test` (frontend). `make coverage` adds a coverage report for `backend/`. No test calls a real service: a guard in `backend/tests/conftest.py` makes any attempt to reach another machine fail, the LLM provider is `none` unless a test sets it, email is off unless a test turns it on with a fake SNS client, and secrets in the tests are made-up values.

| Requirement | Where it is checked (backend/tests/) |
|---|---|
| Data generator is deterministic (seed 42), same counts and ring members twice | `test_generator.py`: `test_generation_is_deterministic`, `test_row_counts_match_targets`, `test_ring_members_shared_by_a01_b01_c01` |
| Each planted scenario exists, including the impossible-timing provider | `test_generator.py`: `test_ground_truth_file` and one test per scenario |
| Each of the 7 rules fires on its planted scenario | `test_hardening.py`: `test_each_of_the_seven_rules_fires_on_its_planted_scenario`; per-rule tests in `test_rules.py` |
| No rule fires on the honest cases | `test_rules.py`: `test_duplicate_does_not_fire_on_honest_same_day_followups`, `test_upcoding_flags_the_upcoder_only`; `test_hardening.py`: `test_the_honest_busy_specialist_is_flagged_by_no_rule` |
| A dummy rule file is picked up with no engine change | `test_rules.py`: `test_dummy_rule_file_is_auto_loaded_with_no_engine_change` |
| A rule that raises is skipped and logged; the pipeline completes | `test_rules.py`: `test_a_rule_that_raises_is_skipped_and_logged`; `test_api.py`: `test_a_failing_stage_is_skipped_and_reported_as_degraded` |
| Anomaly: planted providers in the top 10, honest specialist not in the top 5 | `test_anomaly.py`: `test_planted_providers_are_in_the_top_10`, `test_busy_honest_specialist_is_not_in_the_top_5` |
| Graph: ring entities share one community with the highest score | `test_graph.py`: `test_ring_entities_share_one_community`, `test_ring_community_has_the_highest_score` |
| Ranking: same weights, same order; capacity schedules within the hours | `test_cases.py`: `test_same_weights_always_give_the_same_order`, `test_schedule_follows_priority_until_hours_run_out`, `test_real_capacity_split` |
| Prediction is called investigation risk; the banned name appears nowhere | `test_predict.py`: `test_the_banned_name_appears_nowhere_in_the_codebase` (scans backend and frontend) |
| Repeat offender gets a High band with its source shown | `test_predict.py`: `test_the_repeat_offender_gets_a_high_30_day_band` |
| metrics.json holds model-only metrics | `test_predict.py`: `test_metrics_json_has_all_four_metrics`, `test_metrics_are_reproducible` |
| 30, 60 and 90-day risk: cut-offs per window, censoring, one model each, `Insufficient data` when a window has no positives, the 30-day result unchanged, all three windows in the API and report | `test_horizons.py` |
| Brief: unknown `[E99]`, banned words, or an LLM error give the template | `test_brief.py`: `test_validator_rejects_bad_briefs`, `test_banned_words_are_matched_case_insensitively`, `test_invalid_llm_text_falls_back_to_the_template`, `test_llm_problems_fall_back_to_the_template` |
| Masking: the payload has no real identifiers | `test_llm_masked.py`: `test_the_payload_sent_contains_no_real_names_or_identifiers`, `test_every_case_can_be_masked_with_nothing_left_and_nothing_dropped` |
| A leak error means the LLM is never called | `test_llm_masked.py`: `test_a_leak_error_means_the_llm_is_never_called` |
| A cache hit makes no LLM call | `test_llm_masked.py`: `test_a_stored_brief_means_no_llm_call_and_the_same_text`, `test_reloading_the_prewarmed_cases_makes_no_llm_calls` |
| 429 then success makes exactly one retry | `test_llm_masked.py`: `test_a_429_then_success_makes_exactly_one_retry_after_three_seconds` |
| New case gives an in-app notification | `test_notify.py`: `test_a_new_case_creates_an_in_app_notification_for_each_case` |
| High priority: one SNS publish; a rerun within 24 h is skipped | `test_notify.py`: `test_each_urgent_case_publishes_exactly_one_email_and_a_rerun_within_24h_sends_none` |
| The email holds no member IDs, claim IDs, amounts or names | `test_notify.py`: `test_the_email_holds_only_the_case_id_rank_detector_count_and_link`, `test_the_email_validator_blocks_personal_or_forbidden_content` |
| An SNS failure keeps the in-app notification; the pipeline completes | `test_notify.py`: `test_an_sns_error_keeps_the_in_app_notification_and_marks_the_email_failed`, `test_an_sns_outage_does_not_stop_the_api_or_the_in_app_alerts` |
| A decision without a reason is rejected | `test_api.py`: `test_a_decision_needs_an_action_and_a_reason` |
| Every decision writes an append-only audit row | `test_api.py`: `test_the_audit_log_is_append_only`, `test_audit_is_latest_first_filterable_and_limited` |
| Drafts are never sent without approval; "fraud" or "investigation" is rejected | `test_notify.py`: `test_a_draft_is_created_from_the_template_and_never_sent`, `test_a_draft_with_a_banned_word_is_rejected`, `test_approval_needs_a_reason_and_only_marks_the_message_sent_simulated` |
| /health, /overview, /queue, /cases/{id}, graph, brief, /audit return 200 | `test_hardening.py`: `test_the_main_endpoints_return_200`; `test_api.py` covers each in depth |
| Roles: investigators, team leads and admins see and do only what the matrix allows | `test_rbac.py` (about 60 tests, including every cross-unit refusal and its audit entry) |
| Reports: PDF has the case ID and the confidentiality footer on every page; downloads are audited | `test_rbac.py`: `test_the_case_report_is_a_pdf_with_the_case_id_the_footer_and_the_sections`, `test_the_unit_report_pdf_and_csv` |
| `make reset` leaves a clean, demo-ready state | `test_demo_reset.py` |
| No network access, no real provider | `test_hardening.py`: `test_a_test_cannot_reach_the_internet`, `test_tests_run_with_no_llm_provider_and_no_email` |
| No key or `.env` is tracked; settings are documented | `test_hardening.py`: `test_no_real_looking_key_is_tracked_in_git`, `test_no_env_file_or_database_is_tracked`, `test_gitignore_covers_the_files_that_must_never_be_committed`, `test_every_setting_the_code_reads_is_in_env_example_blank_with_a_comment` |
| README has no secrets and the required sections | `test_hardening.py`: `test_the_readme_has_no_keys_and_the_expected_sections` |

Frontend (`frontend/src/**/*.test.*`): wording rules, the queue, case detail, brief rendering and badges, decision and review panels, notifications, outbound drafts, login, role navigation and assignment. `frontend/e2e/flow.mjs` signs in as admin, team lead and investigator in a real Chrome and checks the whole story, including 401 and 403 refusals and the audit log.
