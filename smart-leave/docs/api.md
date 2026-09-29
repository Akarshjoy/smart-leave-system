# API reference

All routes except GET /approval require a Cognito ID token in the Authorization header. The employee identity comes from the verified Cognito claims, never from a client-selected role. Roles are looked up in the standalone People table.

| Method | Route | Purpose |
|---|---|---|
| GET | /me | Current directory record, rules, current/next-year balances, own requests |
| POST | /requests | Create or return an idempotent request; input request_id UUID, leave_type, start_date, end_date, reason |
| GET | /team | Assigned requests for managers; all requests for HR; denied to employees |
| POST | /decisions | Explicit decision; input employee_id, request_id, stage (manager/hr), decision (approve/reject), note; optional signed token |
| POST | /config | HR rule create/update with expected version for updates |
| GET | /approval?token=... | Verify signed review link and redirect to portal fragment; no decision or database change |

The review token is placed in a URL fragment after redirect, removed from the address bar by the frontend, and kept in memory until used/closed. The API's public response never returns task tokens or the signing nonce. DynamoDB streams asynchronously start workflows, send employee status notifications and deliver decision callbacks. Brief UI lag after a decision is normal; refresh status.

API error codes: 400 invalid input, 403 role/ownership denial, 409 concurrent edit or decision already recorded, 500 AWS operation failure. Cognito/API Gateway can also return 401 when the token expires.

CSV export is generated in-browser from authorized /team data. Formula-prefixed values are escaped; employee reasons are omitted.
