# Edge-case report and verification plan

Automated evidence: `tests/test_system.py` runs against Moto AWS mocks. A passing unit/integration simulation is not a live AWS execution or concurrency load test.

| Scenario | Expected outcome | Automated coverage |
|---|---|---|
| Insufficient balance at submission | Persist REJECTED with reason; no workflow or debit | test_insufficient_auto_reject |
| Approved overlap at submission | Persist REJECTED; balance unchanged | test_overlap_submission |
| Two overlapping pending requests | First approval claims dates; second rejected atomically | test_overlap_final_transaction |
| Pending requests compete for quota | Final approval rechecks quota; no overspending | test_pending_requests_cannot_overspend |
| Duplicate submission UUID | Return original request | test_submit_does_not_debit_and_is_idempotent |
| Repeated approval | Decision condition rejects replay; only one debit | test_duplicate_decision_rejected, test_final_debit_exactly_once |
| More than five chargeable days | Manager plus HR needed | test_long_request_requires_hr |
| Unpaid/unlimited | Track days but bypass quota | test_unlimited_still_tracks_days |
| Wrong manager or self approval | Permission denied | test_wrong_manager_and_self_approval |
| Tampered/expired signed URL | Reject link; dashboard still available | test_signed_link_tamper_expiry |
| Weekend/holiday-only request | Reject empty chargeable range | test_no_weekday_rejected, test_working_days_and_holidays |
| Request crosses year | Ask employee to split it | test_cross_year_rejected |
| Repeated weekly carry job | One annual credit only | test_weekly_carry_once |
| Manager inactive for 48 hours | Notify HR and remind manager; remain pending | test_inaction_reminder_preserves_balance (reminder function); live timer test pending |

## Inaction behavior

Standard ApprovalSeconds is 172800 (48 hours). On a callback timeout, Step Functions runs the reminder Lambda, increments a counter, then registers a fresh callback token and signed link. Any already-recorded decision is replayed to the fresh task rather than requested again. Both manager and HR stages use this mechanism. Fifteen unanswered timeout windows across the request cause EXPIRED (about 30 days with standard settings). Expiry does not debit quota. Notification delays and retries mean elapsed wall-clock time can exceed the nominal duration.

For a short demo, deploy ApprovalSeconds=60. This changes the actual workflow waiting period and link expiration to one minute; it is explicitly a test configuration, not evidence of a real 48-hour wait. Restore 172800 for normal use. Existing running executions retain their definition/input.

## Required live checks

- Confirm manager SNS subscription and wait for its filter policy to become effective.
- Verify delivery of submitted, pending-HR and final SES messages.
- Confirm GET approval links only open the portal; an authenticated POST makes the decision.
- Approve a short request and a request longer than five charged days.
- Submit two competing requests and verify the balance and approved-date locks after decisions.
- Let a demo timeout occur and capture the reminder/loop in Step Functions.
- Check the failed-stream SQS queue remains empty; inspect the CloudWatch alarm if not.
- Capture successful execution details and portal results. Do not label mocked tests as live evidence.

## Known boundaries

No approved-request cancellation/refund, part-day leave, statutory entitlement engine, multi-region concurrency, employee editing screen, or manager delegation. Users are bootstrapped with the administrative CLI. Manager/HR staff need an independent assigned manager to submit their own leave; self-approval is blocked. Weekly processing and team scans should be redesigned for a large workforce.

Stream and email actions are at-least-once. Delivery can duplicate after a crash. Stream failures are retried, then sent to SQS with an HR alarm; an operator must inspect and recover failed events. SNS delivery depends on confirmed subscriptions. A successful workflow does not alone prove the recipient received every email.
