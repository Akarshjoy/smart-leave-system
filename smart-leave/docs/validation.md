# Local validation

Checks performed while preparing this archive:

- `python -m pytest tests -q`: **22 passed**, using Moto AWS mocks.
- `cfn-lint template.json`: passed with exit code 0.
- `node --check frontend/app.js`: passed.
- Template generator: executed successfully and produced both JSON definitions.

The tests cover whole-day rules, cross-year rejection, holidays, quota rejection, submission idempotency, final debit idempotency, overlap at submission/final approval, competing requests, stale-version transaction rollback, multi-level decision requirements, wrong approver and self-approval, duplicate decisions, signed token tampering/expiry, unlimited leave, once-only annual carry, reminders, secret-field filtering and disabled-rule final rejection.

AWS mocks do not verify IAM permissions, real Step Functions execution, SNS subscription delivery, SES sandbox settings, Cognito/browser integration, or real concurrent load. A Chromium browser was not available here; the frontend has syntax validation but no completed browser-rendering or end-to-end UI test.

Complete `docs/demo-guide.md` in your AWS account before describing the application as end-to-end verified. A workflow execution can succeed with a REJECTED business result, so capture the final business status and balance as well as the execution status.

The generated state-machine JSON in `docs/workflow.asl.json` includes CloudFormation intrinsic references. Deploy it through `template.json`; it is not a standalone definition to paste unchanged into the Step Functions console.
