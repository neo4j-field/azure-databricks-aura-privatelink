# Initial Design Prompt

**Date:** 2026-05-13
**Author:** Guhan Sivaji
**Goal:** Produce a production-grade reference for connecting Azure Databricks Serverless to Neo4j Aura VDC on Azure via Private Link.

## Original artifact

The starting point was a written architecture guide PDF ("Azure Databricks Serverless + Neo4j PrivateLink.pdf") drafted as a customer-facing reference. The PDF described:

- The end-to-end architecture (Databricks NCC → Azure Private Link → Aura PLS)
- Step-by-step setup
- A minimal Python notebook example
- Production best-practices guidance

## Constraints

- Target audience: enterprise customers evaluating private connectivity for Aura on Azure
- Aura tier required: VDC (Private Link is not on Professional)
- Databricks tier required: Premium with Serverless enabled
- Output must be production-grade, not POC
- All assets under github.com/neo4j-field, Apache 2.0

## Open questions at the start

1. Are the steps in the PDF accurate against the latest official docs?
2. Does the PDF correctly handle the third-party PLS case (Aura is not an Azure-native resource)?
3. Should the example notebook show retry/idempotency patterns?
4. Should we ship Terraform alongside the manual steps?

These questions drove the validation session captured in `02_validation_session.md`.
