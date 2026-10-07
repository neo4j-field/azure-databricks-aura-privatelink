# Documentation

## Setup guides

Pick the guide that matches what reaches Aura and how you want to run it.

| Connecting from | Manual | Terraform |
|-----------------|--------|-----------|
| **Databricks Serverless** (NCC) | [setup-ncc-manual.md](setup-ncc-manual.md) | [setup-ncc-terraform.md](setup-ncc-terraform.md) |
| **Your VNet** (Private Link) | [setup-private-link-manual.md](setup-private-link-manual.md) | [setup-private-link-terraform.md](setup-private-link-terraform.md) |

Read [architecture.md](architecture.md) first if you want the data-plane, control-plane, and DNS background.

## Shared steps

| Doc | Covers |
|-----|--------|
| [shared/aura-console-steps.md](shared/aura-console-steps.md) | Provision Aura, enable Private Link, allow-list the consumer subscription, approve the endpoint, disable public access |
| [shared/validate-connectivity.md](shared/validate-connectivity.md) | Secret scope, notebook upload, validation and smoke test notebooks |

## Operations

| Doc | Covers |
|-----|--------|
| [operations/troubleshooting.md](operations/troubleshooting.md) | Symptoms and fixes |
| [operations/teardown.md](operations/teardown.md) | Removing each setup path and the Aura side |
| [operations/developer-desktop-access.md](operations/developer-desktop-access.md) | Neo4j Desktop and browser access after public access is off |
| [operations/batch-jobs-other-vnets.md](operations/batch-jobs-other-vnets.md) | Workloads in other VNets or subscriptions |
| [operations/production-notes.md](operations/production-notes.md) | Best practices, limits, and gotchas |

## Reference

| Doc | Covers |
|-----|--------|
| [reference/validation-report.md](reference/validation-report.md) | Setup steps checked against official docs |
| [reference/security-review.md](reference/security-review.md) | Security review findings |
| [reference/suggest-improvements.md](reference/suggest-improvements.md) | Unbuilt ideas |
