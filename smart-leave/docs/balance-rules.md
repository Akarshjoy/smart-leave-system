# Balance logic walkthrough

## Defined policy

LeaveFlow is a standalone demonstration system. Calendar years run January 1–December 31 using UTC dates on the server. Whole-day leave only; no half days. Dates are inclusive. A request must be in the current or next year and within a single year. Split a December/January absence into two requests. Past start dates are rejected, including a start date that has passed while approval was pending.

Each type has editable `annual_quota`, `carry_cap`, `unlimited`, `working_days_only`, `max_calendar_days`, `holidays`, and `enabled` attributes. Example quotas are seed data, not statutory entitlements. HR edits these from the portal without deploying code. Maximum span is capped at 60 calendar days so a final-approval transaction fits below DynamoDB's 100-action limit.

If working_days_only is true, Saturdays, Sundays and configured holidays are not charged. Otherwise every day except configured holidays is charged. More than 5 **charged days** requires HR approval after manager approval. Date locks cover the full inclusive absence range, including excluded weekends, to prevent overlapping approved date ranges.

## Data and formula

Each balance item uses `employee_id` + `year#leave_type` and stores used, carry, carry_applied, quota_basis and version.

**Available = current configured annual quota + credited carry − approved used days.**

Unlimited types bypass the quota limit but still track used days and date conflicts. A quota reduction below already-used days produces a negative displayed balance; existing approvals remain valid, and further requests are rejected until sufficient allowance exists. Manager approval alone never reserves or deducts quota.

## Submission

1. Resolve the signed-in employee and their assigned manager from the directory.
2. Read the active leave rule, calculate days, and record a rule snapshot.
3. Initialize the annual balance if missing, using a conditional put.
4. Check available balance and existing approved requests for overlaps.
5. Persist PENDING_MANAGER or REJECTED with a reason. No debit occurs.
6. A DynamoDB stream starts the named Step Functions execution for a pending request and sends the employee a status email.

Each browser submission has a UUID idempotency key. Retrying the same key returns the original result. New UUIDs can create overlapping pending requests; only one can ultimately claim overlapping approved dates.

## Final approval and concurrency

After every required approver has approved, the final Lambda re-reads the current rule and annual balance. It then submits one DynamoDB transaction containing:

- Request update to APPROVED, conditional on its previous status.
- Rule-version condition check.
- Balance used-days increment, conditional on the version just read.
- Conditional creation of one date-lock item for every day in the range.

The operation commits entirely or does nothing. A concurrent balance update causes the transaction to fail and the workflow retries, re-reading the balance. A competing date lock leads to rejection without a debit. A repeated finalization finds the terminal status and does not debit again. An insufficient balance at final approval is a business rejection, even when there was enough balance at submission.

The version condition protects the quota calculation: if two requests both read one remaining day, only one can successfully update that balance version. The other retries and is rejected after finding zero days.

Rejections and expiry never debit. Cancellation of approved leave and refunding quota are not implemented in this version.

## Rule changes

The day count, dates and >5-day HR requirement are fixed when the request is submitted. The current enabled flag, unlimited setting and quota are checked again at final approval. HR rule writes use optimistic version checks to prevent silently overwriting another HR edit.

`quota_basis` snapshots the quota at balance creation and refreshes it at each final approval. The rollover calculation uses this historical basis, so changing the current year's quota does not retrospectively reinterpret the stored prior-year balance. Changing rules alone does not rewrite prior quota_basis records. If an organization needs exact historical entitlement amendments, add an explicit audited adjustment operation.

## Weekly carry-forward and summaries

The EventBridge job runs Mondays at 04:00 UTC. It initializes current-year balances and checks carry_applied:

**Credit = min(current type carry cap, max(0, previous quota_basis + previous carry − previous used)).**

Missing previous-year balance or unlimited leave yields zero carry. Carry is credited once per employee/type/year using a conditional update, and never repeatedly added each week. Subsequent runs send summaries but preserve the credited amount. Carry does not expire within the year. Current-year carry becomes available only once the weekly job runs; manually invoke the job after seeding users for a demo.

Summary delivery has a week marker. A crash after email delivery but before updating the marker may produce a duplicate email. Exactly-once email delivery is not claimed. Carry arithmetic remains idempotent.

## Worked example

Casual quota 12, credited carry 3, previously approved usage 4: available 11. A 4-day pending request leaves available at 11. Final approval atomically increases usage to 8 and available becomes 7. A duplicate click or workflow retry cannot turn usage into 12. A later 8-day submission is auto-rejected, leaving available at 7.

## Sources

- https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/transaction-apis.html
- https://docs.aws.amazon.com/step-functions/latest/dg/connect-to-resource.html
