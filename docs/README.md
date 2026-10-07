# Documentation

## Setup guides

Pick the guide that matches what reaches Aura and how you want to run it.

| Connecting from | Manual | Terraform |
|-----------------|--------|-----------|
| **Databricks Serverless** (NCC) | [NCC manual setup](setup-ncc-manual.md) | [NCC Terraform setup](setup-ncc-terraform.md) |
| **Your VNet** (Private Link) | [Private Link manual setup](setup-private-link-manual.md) | [Private Link Terraform setup](setup-private-link-terraform.md) |

Every guide ends with the same follow-up steps: validate connectivity, close the public endpoint, teardown, and what's next. The NCC guides and the Private Link Terraform guide give each step its own section. The Private Link manual guide groups them under [Databricks setup and validation](setup-private-link-manual.md#databricks-setup-and-validation), with an extra step to add routing-host records.

The guides call the two paths NCC and Private Link. The Terraform folders for them are the **NCC stack** (`databricks-ncc`) and the **Private Endpoint stack** (`azure-private-endpoint`).

Every guide uses the same variables. [Environment setup](env-setup.md) says where to find each value and how to load them with `source scripts/load-env.sh`. Read it before you start a guide.

Read [architecture.md](architecture.md) first if you want the data-plane, control-plane, and DNS background.

## Shared steps

| Doc | Covers |
|-----|--------|
| [env-setup.md](env-setup.md) | Where to find each environment variable, and how `scripts/load-env.sh` loads `.env` into your shell |
| [shared/aura-console-steps.md](shared/aura-console-steps.md) | Provision Aura, enable Private Link, allow-list the consumer subscription, approve the endpoint, disable public access |
| [shared/private-dns-central.md](shared/private-dns-central.md) | Why `databases.neo4j.io` lives in your private DNS zone, self-managed vs. central DNS, and central hub DNS setup for Private Link |

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
