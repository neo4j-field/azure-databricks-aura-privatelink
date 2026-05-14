# Validation Session

**Date:** 2026-05-13
**Tool:** Claude Code (Opus 4.7, 1M context)
**Inputs:** the PDF guide + current Aura, Databricks, and Microsoft Learn documentation

## Prompt shape

The validation prompt was:

> Validate the PDF and check if the instructions are right and valid by referring to the latest official docs page from neo4j, azure and databricks.

## Method

Four parallel WebFetch calls were issued against the canonical doc pages:

1. `https://neo4j.com/docs/aura/platform/security/secure-connections/`
2. `https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/serverless-private-link`
3. `https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/`
4. `https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/manage-private-endpoint-rules`

Each fetch was scoped to extract specific procedural detail (subscription registration, exact UI labels, supported resource categories, propagation delays, etc.) rather than free-form summarization.

## Key findings

The complete findings live in [`docs/validation-report.md`](../docs/validation-report.md). The most material finding:

- **PDF Step 4 (private endpoint rule) is incorrect** as written. The flow described uses the account-console UI fields (Resource ID + Domain), but the UI does not support third-party Private Link Services like Aura. Aura must be configured via the REST API path that takes `resource_id` + `group_id` + `domain_names` — the same path that Microsoft explicitly documents for Azure App Gateway v2.

Secondary findings:

- Missing prerequisites: Premium plan on account and workspace, account-admin role
- Missing limits: 10 NCCs / 100 PEs / 50 workspaces per region/NCC
- Missing target-subscription registration on the Aura side
- Missing 14-day expiry for unapproved PE rules
- Missing 10-minute propagation delay and restart-serverless step after attaching NCC
- Notebook example should include `%pip install neo4j`, secret scope creation, DNS sanity check, retry pattern

## Decision

Rather than amend the PDF in place, produce a new repository under github.com/neo4j-field that contains:

- A corrected README mirroring the PDF structure with the validated steps
- An explicit validation report (the deltas, with source links)
- Production-grade notebooks
- Terraform for the NCC + PE rule
- Helper scripts including the REST API path for the third-party PLS case
