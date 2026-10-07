# Azure Databricks + Neo4j Aura PrivateLink

Production-grade reference for establishing private, end-to-end connectivity between **Azure Databricks** and **Neo4j Aura (Azure)** using **Azure Private Link**. Serverless compute connects through Databricks **Network Connectivity Configurations (NCC)**. Classic Databricks, AKS, ADF, and jump VMs connect through a private endpoint in your own VNet.

This repository provides validated step-by-step guides, Terraform, helper scripts, and notebooks.

---

## Problem Statement

Enterprise teams need to exchange data between Delta tables in Azure Databricks and Neo4j Aura graph databases without traversing the public internet. Public TLS endpoints with IP allowlists are operationally fragile (egress IPs change, allowlists drift) and not aligned with Zero Trust principles for regulated workloads.

This repository documents a private-only architecture that:

- Routes all Bolt traffic between Databricks and Neo4j Aura over the Azure backbone
- Eliminates customer-managed VNets on the consumer side for Serverless, via Databricks NCC
- Supports disabling public ingress on Aura entirely after validation

---

## Architecture

![Databricks Serverless reaches Neo4j Aura through an NCC private endpoint and the Aura Private Link Service](docs/images/architecture-goal.svg)

See [docs/architecture.md](docs/architecture.md) for a detailed walkthrough including the data-plane vs control-plane distinction and DNS routing behavior.

---

## Prerequisites

| Item | Value |
|------|-------|
| Aura tier | **AuraDB Virtual Dedicated Cloud (VDC)** or **AuraDS Enterprise**, on Azure |
| Aura role | Org admin on the Aura tenant |
| Databricks (NCC paths) | **Premium** workspace and account, with an **Azure Databricks account admin** (workspace admin is not sufficient) |
| Azure | `az login` as the account admin. For CI/CD, use a service principal with account admin. |

> Private Link is **not available** on Aura Professional or Business Critical on Azure. Verify your tier before starting.

---

## Choose your setup

Pick the row that matches **what reaches Aura**, then the column that matches **how you want to run it**.

| Connecting from | Manual | Terraform |
|-----------------|--------|-----------|
| **Databricks Serverless** (NCC) | [Manual NCC setup](docs/setup-ncc-manual.md) | [Terraform and automated NCC setup](docs/setup-ncc-terraform.md) |
| **Your VNet**: classic Databricks, AKS, ADF, jump VMs (Private Link) | [Manual Private Link setup](docs/setup-private-link-manual.md) | [Private Link Terraform setup](docs/setup-private-link-terraform.md) |

- **NCC, manual.** The Databricks CLI or REST steps, with a console alternative where one exists. Start here to understand each step.
- **NCC, Terraform.** `scripts/automate.py` runs Terraform, polls the endpoint rule to `ESTABLISHED`, restarts warehouses, loads the `neo4j` secret scope, and runs the validation notebook. Recommended for the NCC demo.

  ```bash
  uv run scripts/automate.py run --account-profile <name>
  ```

- **Private Link, manual.** The Azure CLI creates the private endpoint and the private DNS. [`scripts/private_link.py`](scripts/private_link.py) runs the same commands, and `--dry-run` shows what it would change.
- **Private Link, Terraform.** The [`azure-private-endpoint`](infra/terraform/azure-private-endpoint/) stack creates the private endpoint and DNS. See [infra/terraform/README.md](infra/terraform/README.md) for which stack to pick.

The Aura console has no API, so its steps are manual in every path. They are collected in [Aura console steps](docs/shared/aura-console-steps.md): provision Aura, enable Private Link, allow-list the consumer subscription, approve the endpoint, and disable public access. Every path ends with [Validate connectivity](docs/shared/validate-connectivity.md).

---

## What's next

| Topic | Guide |
|-------|-------|
| Developer laptop needs Neo4j Desktop or a browser after public access is off | [Developer desktop access](docs/operations/developer-desktop-access.md) |
| Batch jobs, pipelines, or services in other VNets or subscriptions | [Batch jobs in other VNets](docs/operations/batch-jobs-other-vnets.md) |
| Something does not resolve or connect | [Troubleshooting](docs/operations/troubleshooting.md) |
| Remove the setup | [Teardown](docs/operations/teardown.md) |
| Run it in production | [Production notes](docs/operations/production-notes.md) |

---

## Repository Layout

```
.
├── README.md                                       # This file
├── LICENSE                                         # Apache 2.0
├── docs/
│   ├── README.md                                   # Docs index
│   ├── architecture.md                             # Detailed architecture and rationale
│   ├── setup-ncc-manual.md                         # NCC setup by hand with the Databricks CLI or REST
│   ├── setup-ncc-terraform.md                      # NCC setup with Terraform and scripts/automate.py
│   ├── setup-private-link-manual.md                # Private Endpoint and DNS setup by hand with the Azure CLI
│   ├── setup-private-link-terraform.md             # Private Endpoint and DNS setup with Terraform
│   ├── shared/                                     # Steps common to every setup path
│   │   ├── aura-console-steps.md                   # Aura provisioning, allow-list, approval, public access
│   │   └── validate-connectivity.md                # Secret scope, notebooks, validation
│   ├── operations/                                 # Running and removing the setup
│   │   ├── troubleshooting.md
│   │   ├── teardown.md
│   │   ├── developer-desktop-access.md
│   │   ├── batch-jobs-other-vnets.md
│   │   └── production-notes.md
│   ├── reference/                                  # Reports and notes about the repo itself
│   │   ├── validation-report.md                    # Setup steps checked against official docs
│   │   ├── security-review.md                      # Security review findings
│   │   └── suggest-improvements.md                 # Suggested improvements
│   └── images/                                     # SVG diagrams and screenshots used in the docs
├── notebooks/
│   ├── 01_validate_connectivity.py                 # Generic DNS + Bolt sanity check
│   ├── 02_delta_to_neo4j.py                        # Round-trip: Delta -> Neo4j -> Delta
│   ├── 03_serverless_push_pull_demo.py             # Small push/pull demo over PrivateLink (synthetic customers)
│   └── 04_smoke_test.py                            # End-to-end PrivateLink smoke test with write and read-back
├── infra/
│   └── terraform/
│       ├── README.md                               # Index: which stack to pick
│       ├── databricks-ncc/                         # NCC stack: Databricks NCC + PE rule + workspace binding
│       ├── azure-private-endpoint/                 # Private Endpoint stack: PE in your VNet + private DNS
│       └── jumpbox/                                # Azure Bastion + jump box VM for developer desktop access
├── scripts/
│   ├── automate.py                                 # Orchestrator for the NCC Terraform setup
│   ├── private_link.py                             # Private Endpoint and DNS setup with the Azure CLI, with --dry-run and verify --bolt
│   ├── private_link_testbed.py                     # Throwaway stand-in Aura PLS for testing private_link.py
│   ├── create-secret-scope.sh                      # Databricks secret scope setup
│   ├── create-private-endpoint-rule.sh             # REST API fallback for the NCC PE rule
│   └── validate-dns.py                             # Standalone DNS check
└── screenshots/                                    # Console screenshots of the Private Link Terraform setup
```

---

## Validation Report

This repo's setup steps are reconciled against the latest official documentation (May 2026). See [docs/reference/validation-report.md](docs/reference/validation-report.md) for the corrections made to the original draft of this guide, with source links.

---

## Contributing

Pull requests welcome. Please:

1. Test changes against a real Aura VDC + Databricks Serverless setup
2. Update the validation report if Microsoft or Neo4j docs change

---

## Author

**Guhan Sivaji**, Principal Architect, Neo4j Field Engineering. Reach out via [Neo4j Field](https://github.com/neo4j-field).
