# End-to-end demo and screenshot guide

Use three distinct inboxes you control: HR, manager and employee. In the SES sandbox, verify all three in the deployment region. Use fake names/reasons in the demonstration. Do not record passwords, signed approval URLs, authentication tokens or Step Functions task-token details.

## Five-minute recording

1. **0:00–0:30** Show the employee balance cards and explain the editable leave types.
2. **0:30–1:10** Submit a one-day future request; show PENDING MANAGER and unchanged balance.
3. **1:10–1:40** Show the submitted SES email and manager SNS notification (hide the signed URL).
4. **1:40–2:20** Open the manager link and authenticate in another browser profile. Approve explicitly.
5. **2:20–2:50** Refresh the employee view. Show APPROVED and the one-day deduction, then the SES confirmation.
6. **2:50–3:30** Open Step Functions → smart-leave-workflow → corresponding request UUID. Capture the successful graph and Events entry ExecutionSucceeded.
7. **3:30–4:15** Show the manager absence calendar and download the CSV.
8. **4:15–5:00** Demonstrate a >5-day request waiting for HR, or explain this branch using the state machine definition. If time permits, approve it as HR and show the second successful execution.

## Additional evidence

- A rejected request with insufficient quota and unchanged balance.
- An approved overlap rejection.
- Timeout reminder and re-entry in the 60-second demonstration configuration.
- HR quota editor and a visible rule change.
- Balance table values before/after approval (omit private tokens).

Live screenshots/video are not included in this source archive because it has not been deployed into your account. Populate `docs/evidence/` after completing these checks.
