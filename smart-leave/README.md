# LeaveFlow — Smart Leave & Absence Management

A standalone AWS serverless application for employee leave requests, manager/HR approvals, configurable quotas, and a team absence calendar.

This project has its own Cognito pool, employee directory, DynamoDB tables and deployment. It does not connect to DayOne.

## Features

- Employee portal: leave application, annual balances and history.
- Manager portal: assigned-team approvals, absence calendar and CSV reports.
- HR portal: >5-day approvals, company calendar and editable leave rules.
- Sick, casual, earned and unpaid rules seeded as editable demonstration data.
- Signed, expiring email review links plus authenticated approval confirmation.
- Conditional decisions and transactional final quota deduction.
- Submission and final-approval overlap checks.
- 48-hour approval reminders and escalation notice to HR.
- Once-per-year carry-forward credits checked by a weekly job.
- SES employee notifications and SNS manager notifications.
- CloudFront HTTPS delivery from a private S3 bucket.

## Validation status

Local automated tests and template checks are described in `docs/validation.md`. AWS deployment, real email delivery, live callback timing and browser behavior must be verified in your account. No real execution screenshot or video is claimed in this archive.

## Stack and source

| Layer | Implementation |
|---|---|
| UI | HTML, CSS, JavaScript; no frontend build step |
| Authentication | Cognito admin-created accounts |
| API | API Gateway REST API with Cognito authorizer |
| Backend | Python 3.14 Lambda functions |
| Storage | DynamoDB on-demand, encrypted at rest |
| Workflow | Step Functions Standard callback tasks |
| Messages | SES and SNS |
| Scheduling | EventBridge weekly rule |
| Deployment | AWS SAM / CloudFormation |

- `backend/app.py`: API, workflow tasks, stream processing, weekly job.
- `backend/domain.py`: day-count and rule validation.
- `frontend/`: all website source.
- `scripts/build_template.py`: generates `template.json` and `docs/workflow.asl.json`.
- `scripts/setup.py`: frontend configuration, rule seeding and account creation.
- `scripts/deployment_policy.py`: produces a scoped deployment-policy starting point for this stack.
- `tests/test_system.py`: mocked AWS and domain tests.
- [Schema and ER diagram](docs/schema.md)
- [Balance logic walkthrough](docs/balance-rules.md)
- [Edge-case report](docs/edge-cases.md)
- [Demo guide](docs/demo-guide.md)

## 1. Prepare your Windows environment

Open the extracted `smart-leave` folder in VS Code. Open Terminal → New Terminal (PowerShell). Run commands one at a time and stop on any error.

```powershell
python --version
aws --version
sam --version
aws sts get-caller-identity --profile my-account
python -m pip install -r requirements-dev.txt
```

The template uses Python 3.14 to match the existing environment used in this project. The Lambda code uses runtime-provided boto3; the development dependencies are for local setup and tests.

Choose `us-east-1` for the first deployment. Verify your sender identity and all demo recipient addresses in SES while in sandbox. Copy the sender's SES identity ARN. Confirm SNS subscription emails after deployment.

The existing DayOne-only IAM policy will not automatically authorize this stack. An account administrator can generate and review the managed policy described under Permissions below. Do not use root credentials for routine deployment.

## 2. Build and deploy

```powershell
python scripts/build_template.py
python -m pytest tests -q
sam validate --lint --template-file template.json --profile my-account --region us-east-1
sam build --template-file template.json
sam deploy --guided --profile my-account --region us-east-1
```

| Prompt | Value |
|---|---|
| Stack name | `smart-leave` |
| Region | `us-east-1` |
| SenderEmail | Your SES-verified sending email |
| HREmail | Your HR test inbox |
| SESIdentityArn | Sender identity ARN in us-east-1 |
| ApprovalSeconds | `172800`; use `60` only for short timeout demonstrations |
| Confirm changes | Yes |
| Allow IAM role creation | Yes, after reviewing the template |
| Disable rollback | No |
| Save configuration | Yes |

This creates billable AWS resources. It does not upload the frontend or create demo people. Keep stack outputs for the next steps.

## 3. Seed rules and upload the frontend

```powershell
python scripts/setup.py --profile my-account seed
python scripts/setup.py --profile my-account configure
```

The configure command prints the web bucket and portal URL. Replace `YOUR_WEB_BUCKET`:

```powershell
aws s3 sync frontend s3://YOUR_WEB_BUCKET --cache-control "max-age=60" --profile my-account --region us-east-1
```

Seeding preserves existing rules. The default quotas (12 sick, 12 casual, 24 earned, unlimited unpaid) are examples, not statutory rules. HR can edit them after login.

## 4. Create three users

Replace placeholder emails with three distinct verified inboxes. Use separate browser profiles for each role.

```powershell
python scripts/setup.py --profile my-account user --name "HR Admin" --email YOUR_HR_EMAIL --role HRAdmin
python scripts/setup.py --profile my-account user --name "Demo Manager" --email YOUR_MANAGER_EMAIL --role Manager
```

Copy the manager's printed Employee ID, then:

```powershell
python scripts/setup.py --profile my-account user --name "Demo Employee" --email YOUR_EMPLOYEE_EMAIL --role Employee --manager-id MANAGER_UUID
```

Each user receives a Cognito temporary password. Sign in with the email or printed UUID and choose a new password (12+ characters, upper/lowercase, number and symbol). The manager must also confirm the SNS subscription email. Allow time for the subscription filter policy to propagate before submitting requests.

Directory roles are the API authorization source. Cognito groups mirror the bootstrap role; merely editing a Cognito group does not change application permissions. Account creation is an administrative CLI operation; it is intentionally not an unauthenticated registration form. The setup script can be rerun with identical details after a partial setup failure. It does not overwrite an existing person's role/details.

## 5. Test the journey

1. Employee submits a short future leave request.
2. Balance remains unchanged; status becomes PENDING_MANAGER.
3. Manager opens the email review link, signs in, and explicitly approves.
4. The workflow finalizes; employee sees APPROVED and a reduced balance.
5. Manager/HR calendar includes the approved absence. Download the CSV.
6. Test a request with more than five chargeable days: manager approval moves it to PENDING_HR; HR approval triggers the only debit.
7. Test insufficient balance, overlap, rejection and manager inaction. See the edge-case report.

The calendar polls for approved absences every 30 seconds while a manager/HR session is open. Refresh status reloads all lists and balances. It does not use WebSockets.

## Weekly carry-forward and summaries

EventBridge runs the job every Monday at 04:00 UTC. For a demo, get `WeeklyFunction` from the stack outputs and invoke it once:

```powershell
aws lambda invoke --function-name YOUR_WEEKLY_FUNCTION --payload "{}" --cli-binary-format raw-in-base64-out --profile my-account --region us-east-1 weekly-result.json
```

Inspect the command metadata for FunctionError and read the output file before claiming success. Carry is applied once per year, not every week. Missing prior-year balance earns zero carry. See the balance walkthrough for the exact formula and quota snapshot policy.

## Permissions

Generate a reviewable policy for the actual account (no keys printed):

```powershell
python scripts/deployment_policy.py --profile my-account
```

An AWS account administrator should review `deployment-policy.json`, create a **customer-managed IAM policy** using that JSON, and attach it to the deployment user. Do not paste it into a user inline policy with the smaller character limit. This is a project-level deployment policy, not a general organization least-privilege policy. It includes role creation and pass-role for the stack; restrict who can edit and deploy its templates. Organization SCPs, permission boundaries, and service restrictions may still block deployment. The script assumes the stack name `smart-leave` and region `us-east-1` unless changed through its options.

## Security and operational limits

- Approval GET links verify a signed token and redirect to a confirmation UI; they never change a request. The final POST requires a valid Cognito token and correct manager/HR identity. Dashboard approvals use identity authorization without an email token.
- Accepted decisions are immutable per stage and conditionally written, so replay cannot debit twice. Self-approval is blocked.
- Signed review tokens expire; a refreshed workflow registration sends a new link. The live task callback token is never exposed in the frontend API.
- Email/SNS delivery is at-least-once. Duplicate delivery after a crash is possible; accounting is idempotent.
- Request stream failures go to a managed-encrypted SQS dead-letter queue after retries. A CloudWatch alarm notifies HR; operator investigation/replay is required.
- Requests and team lists use bounded application scope but scan/query all pages. Add indexes and response pagination before growing beyond a small demonstration workforce.
- No cancellation/refund workflow, half-days, delegation, payroll integration, or employee editing screen is included.
- One request cannot span two years. Late approvals after the start date are rejected. UTC defines server dates; adapt this for an organization's time zone.
- Manager reminders and HR inaction use 48-hour callback windows. After 15 unanswered windows across the request, it expires. This is configurable as a short demo timer, not a statutory policy.
- Tokens remain in browser memory, not persistent storage. Reloading requires signing in again.
- Production use requires administrative MFA, retention rules, audit review, integration tests and monitoring. Do not upload real employee information for the demonstration.

## Troubleshooting

- **AccessDenied**: identify the failed action and resource; check the reviewed smart-leave policy. Do not attach AdministratorAccess as a blanket fix.
- **No manager email**: confirm the filtered SNS subscription and wait for filter activation. Manager SNS notifications differ from employee SES messages.
- **No employee email**: check SES identity/recipient verification, stream Lambda logs, and the dead-letter queue.
- **Decision recorded but still pending**: refresh after a few seconds; inspect Step Functions and Changes Lambda logs. The stream delivers callbacks asynchronously.
- **Expired link**: sign in directly to the portal and review the pending approvals list.
- **Workflow failed**: inspect Error/Cause in Step Functions and fix the cause before redriving where eligible. Failed AWS status is not automatically converted into business approval.
- **Website not configured**: run setup.py configure and sync the complete frontend directory again.

## GitHub and cleanup

Commit code, generated templates, tests and docs. Do not commit credentials, samconfig.toml, generated frontend/config.js, or employee data. Configuration can be regenerated from stack outputs. The placeholder config.js in this download allows a local design preview; it contains no credentials.

Deleting the stack retains DynamoDB tables, the website bucket and Cognito pool. Inventory retained resources from failed and successful attempts, then delete only what is no longer needed. Retained data/storage can continue to incur charges.
