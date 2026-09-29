# DynamoDB schema

Physical table names are created by CloudFormation under the `smart-leave` stack. The conceptual leave_requests and leave_balances tables are the RequestsTable and BalancesTable outputs. Relationships are enforced by application logic; DynamoDB does not enforce relational foreign keys.

```mermaid
erDiagram
    PEOPLE ||--o{ LEAVE_REQUESTS : submits
    PEOPLE ||--o{ LEAVE_BALANCES : owns
    LEAVE_CONFIG ||--o{ LEAVE_REQUESTS : configures
    LEAVE_CONFIG ||--o{ LEAVE_BALANCES : defines
    LEAVE_REQUESTS ||--o{ APPROVED_DATES : claims
    PEOPLE {
        string employee_id PK
        string email
        string name
        string role
        string manager_id FK
        boolean active
        string summary_week
    }
    LEAVE_REQUESTS {
        string employee_id PK
        string request_id SK
        string leave_type FK
        string manager_id FK
        string start_date
        string end_date
        number days
        number year
        string status
        boolean needs_hr
        map rule_snapshot
        string manager_decision
        string hr_decision
        string manager_actor
        string hr_actor
        string outcome_reason
        string nonce
        string manager_token
        string hr_token
    }
    LEAVE_BALANCES {
        string employee_id PK
        string balance_key SK
        number used
        number carry
        number quota_basis
        boolean carry_applied
        number version
    }
    LEAVE_CONFIG {
        string leave_type PK
        number annual_quota
        number carry_cap
        boolean unlimited
        boolean working_days_only
        number max_calendar_days
        list holidays
        boolean enabled
        string version
    }
    APPROVED_DATES {
        string employee_id PK
        string leave_date SK
        string request_id FK
    }
```

| Table | Access pattern |
|---|---|
| People | Get current user and approver; scan active employees for weekly summaries |
| Leave requests | Query employee history; scan and filter manager/HR team view; stream stage changes |
| Leave balances | Get employee/year/type; conditionally update usage or carry |
| Leave config | Read type rules; list types; HR conditional version update |
| Approved dates | Conditional daily lock creation prevents concurrent overlap |

Scan calls paginate through every DynamoDB page. HTTP responses do not yet expose pagination: this is suitable for a small demonstration dataset, not an unbounded company directory. Add manager/status indexes, calendar-month indexes and API pagination before scaling.

Task tokens and request nonces are excluded from public API responses. Leave reasons are visible only to the employee, their assigned manager and HR. CSV reports omit reasons.
