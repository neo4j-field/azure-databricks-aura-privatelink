# Azure Databricks + Neo4j Aura PrivateLink

Production-grade reference for establishing private, end-to-end connectivity between **Azure Databricks** and **Neo4j Aura (Azure)** using **Azure Private Link**. Serverless compute connects through Databricks **Network Connectivity Configurations (NCC)**. Classic Databricks, AKS, ADF, and jump VMs connect through a private endpoint in your own VNet.

This repository provides validated step-by-step guides, Terraform, helper scripts, and notebooks.

---

## Problem Statement

Enterprise teams need to exchange data between Delta tables in Azure Databricks and Neo4j Aura graph databases without traversing the public internet. Public TLS endpoints with IP allowlists are fragile to operate. Egress IPs change, and allowlists drift. They also fall short of Zero Trust principles for regulated workloads.

This repository documents a private-only architecture that:

- Routes all Bolt traffic between Databricks and Neo4j Aura over the Azure backbone
- Eliminates customer-managed VNets on the consumer side for Serverless, via Databricks NCC
- Supports disabling public ingress on Aura entirely after validation

---

## Architecture

![Databricks Serverless reaches Neo4j Aura through an NCC private endpoint and the Aura Private Link Service](docs/images/architecture-goal.svg)

The diagram shows the NCC path from Databricks Serverless. [docs/architecture.md](docs/architecture.md) walks through both paths, including the Private Link path from your own VNet. It also covers the data-plane vs control-plane distinction and DNS routing behavior. Console screenshots of both validated paths are in [screenshots/](screenshots/).

---

## Prerequisites

| Item | Value |
|------|-------|
| Aura tier | **AuraDB Virtual Dedicated Cloud (VDC)** or **AuraDS Enterprise**, on Azure |
| Aura role | Org admin on the Aura tenant |
| Databricks (NCC paths) | **Premium** workspace and account, with an **Azure Databricks account admin** (workspace admin is not sufficient) |
| Azure (NCC paths) | `az login` as the Azure Databricks account admin. For CI/CD, use a service principal with account admin. |
| Azure (Private Link paths) | `az login` as a user who can create private endpoints and private DNS zones in the target resource group. |

> Private Link is **not available** on Aura Professional or Business Critical on Azure. Verify your tier before starting.

Every guide uses the same variables, kept in a repo-root `.env`. Copy `env.sample` to `.env`, fill it in, and load it with `source scripts/load-env.sh`. [Environment setup](docs/env-setup.md) says where to find each value.

---

## Choose your setup

Pick the row that matches **what reaches Aura**, then the column that matches **how you want to run it**.

| Connecting from | Manual | Terraform |
|-----------------|--------|-----------|
| **Databricks Serverless** (NCC) | [NCC manual setup](docs/setup-ncc-manual.md) | [NCC Terraform setup](docs/setup-ncc-terraform.md) |
| **Your VNet**: classic Databricks, AKS, ADF, jump VMs (Private Link) | [Private Link manual setup](docs/setup-private-link-manual.md) | [Private Link Terraform setup](docs/setup-private-link-terraform.md) |

- **NCC, manual.** The Databricks CLI or REST steps, with a console alternative where one exists. Start here to understand each step.
- **NCC, Terraform.** `scripts/automate.py` runs Terraform, polls the endpoint rule to `ESTABLISHED`, restarts warehouses, loads the `neo4j` secret scope, and runs the validation notebook. Recommended for the NCC demo.

  ```bash
  uv run scripts/automate.py run --account-profile <name>
  ```

- **Private Link, manual.** The Azure CLI creates the private endpoint and the private DNS. [`scripts/private_link.py`](scripts/private_link.py) runs the same commands, and `--dry-run` shows what it would change.
- **Private Link, Terraform.** The [`azure-private-endpoint`](infra/terraform/azure-private-endpoint/) stack creates the private endpoint and DNS. See [infra/terraform/README.md](infra/terraform/README.md) for which stack to pick.

The guides call the two paths NCC and Private Link. The Terraform folders for them are the **NCC stack** (`databricks-ncc`) and the **Private Endpoint stack** (`azure-private-endpoint`).

The Aura console has no API, so its steps are manual in every path. They are collected in [Aura console steps](docs/shared/aura-console-steps.md): provision Aura, enable Private Link, allow-list the consumer subscription, approve the endpoint, and disable public access. Every guide ends with the same follow-up steps: validate connectivity, close the public endpoint, teardown, and what's next. The NCC guides and the Private Link Terraform guide give each step its own section. The Private Link manual guide groups them under [Databricks setup and validation](docs/setup-private-link-manual.md#databricks-setup-and-validation), with an extra step to add routing-host records. Each path validates in its own guide: [NCC](docs/setup-ncc-manual.md#validate-connectivity) and [Private Link](docs/setup-private-link-manual.md#run-the-validation-notebook).

---

## How NCC works

An NCC gives Databricks Serverless a private path to Aura. Serverless compute runs in Databricks-owned subscriptions, so it cannot join your VNet. Databricks builds the private endpoint for you and answers DNS for the hostnames you list.

The Bolt driver adds one more step. After the first connection, the driver asks Aura for a routing table. It then connects to the hosts in that table. Those hosts need a private path too.

![Sequence diagram. The driver looks up the Aura instance host through NCC DNS and connects over the NCC private endpoint. Aura returns a routing table with p- routing hosts. A driver resolver rewrites them to the instance host, so later connections use the same private endpoint. Without the resolver, the lookup of a routing host fails.](docs/images/ncc-routing-flow.svg)

### Key terms

- **NCC:** An NCC is a Databricks account setting. You attach it to a workspace, and serverless compute in that workspace follows its rules.
- **Private endpoint rule:** A private endpoint rule is an entry in the NCC. It names the Aura Private Link service and a list of hostnames. Databricks creates a private endpoint from it, and you approve that endpoint in Aura.
- **domain_names:** `domain_names` is the hostname list in the rule. Serverless DNS returns the private endpoint address only for names on this list. Each name is exact. Databricks says to enter the specific instance name, not a wildcard domain.
- **Private ingress:** The private ingress is the Neo4j side of the path. It reads the instance ID from the hostname in the TLS handshake and sends the connection to that instance.
- **Routing table:** The routing table is a list of servers that Aura sends to the driver. It names the servers for reads, writes, and routing. The driver refreshes the table when its time-to-live ends or when the table looks out of date. The hosts in it can therefore change.
- **Routing host:** A routing host is a server name from the routing table. On Aura VDC it looks like `p-<aura-instance-id>-<suffix>.<orch>.neo4j.io`. It differs from the instance host, so the rule does not cover it until you add it.

### Two ways to cover routing hosts

- **Add the host to domain_names:** Use this fix for your own jobs and apps. The error `Cannot resolve address p-...` names the missing host. The update call replaces the whole list, so send every host. See [Add a routing hostname later](docs/setup-ncc-manual.md#add-a-routing-hostname-later).
- **Use a driver resolver:** The notebooks in `ncc-notebooks/` use this fix. A resolver is a driver setting that rewrites an address before DNS runs. The notebooks rewrite each `p-<aura-instance-id>-*` host to the instance host. The driver then sends the instance host in the TLS handshake, and the ingress routes it to the instance.

### Good to know

- **Why the resolver works:** The Neo4j Python driver runs the resolver on every address it opens, including the addresses from the routing table. The TLS server name comes from the resolver output, so it matches the instance host. This was checked in the source of driver 5.28.7.
- **Trade-off:** With the resolver, every connection goes to the instance host. The driver no longer picks a specific reader or writer, and Aura decides which server answers. Neo4j does not document that choice for this case, so test your write workload.
- **Delay:** Rule changes reach serverless compute within about 10 minutes. They can take up to 24 hours to apply fully. Restart serverless compute after each change.

More detail is in [Architecture](docs/architecture.md#routing-hosts-on-both-paths). Sources: [Neo4j routing](https://neo4j.com/docs/operations-manual/current/clustering/setup/routing/), [Aura private link flow](https://neo4j.com/docs/aura/security/secure-connections/), [Databricks domain names](https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/pl-to-internal-network), and [Databricks rule updates](https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/manage-private-endpoint-rules).

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
│   ├── env-setup.md                                # Where to find each environment variable, and how to load .env
│   ├── setup-ncc-manual.md                         # NCC setup by hand with the Databricks CLI or REST
│   ├── setup-ncc-terraform.md                      # NCC setup with Terraform and scripts/automate.py
│   ├── setup-private-link-manual.md                # Private Endpoint and DNS setup by hand with the Azure CLI
│   ├── setup-private-link-terraform.md             # Private Endpoint and DNS setup with Terraform
│   ├── shared/                                     # Steps common to every setup path
│   │   ├── aura-console-steps.md                   # Aura provisioning, allow-list, approval, public access
│   │   └── private-dns-central.md                  # Private DNS ownership and central hub DNS for Private Link
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
├── ncc-notebooks/                                  # Notebooks for the NCC path (serverless compute)
│   ├── 01_validate_connectivity.py                 # Generic DNS + Bolt sanity check
│   ├── 02_delta_to_neo4j.py                        # Round-trip: Delta -> Neo4j -> Delta
│   ├── 03_serverless_push_pull_demo.py             # Small push/pull demo over PrivateLink (synthetic customers)
│   └── 04_smoke_test.py                            # End-to-end PrivateLink smoke test with write and read-back
├── pl-notebooks/                                   # Notebooks for the Private Link path (classic clusters)
│   ├── 01_validate_connectivity.py                 # DNS, routing-host, and Bolt checks, no resolver by default
│   ├── 02_delta_to_neo4j.py                        # Round-trip: Delta -> Neo4j -> Delta
│   ├── 03_push_pull_demo.py                        # Small push/pull demo over Private Link (synthetic customers)
│   └── 04_smoke_test.py                            # End-to-end smoke test with routing-host check, write, and read-back
├── infra/
│   └── terraform/
│       ├── README.md                               # Index: which stack to pick
│       ├── databricks-ncc/                         # NCC stack: Databricks NCC + PE rule + workspace binding
│       ├── azure-private-endpoint/                 # Private Endpoint stack: PE in your VNet + private DNS
│       └── jumpbox/                                # Azure Bastion + jump box VM for developer desktop access
├── scripts/
│   ├── automate.py                                 # Orchestrator for the NCC Terraform setup
│   ├── private_link.py                             # Private Endpoint and DNS setup with the Azure CLI, with --dry-run and verify --bolt
│   ├── databricks_vnet_workspace.py                # Create or delete a VNet-injected workspace for the Private Link path
│   ├── private_link_testbed.py                     # Throwaway stand-in Aura PLS for testing private_link.py
│   ├── load-env.sh                                 # Source it to load .env into your shell (bash or zsh)
│   ├── create-secret-scope.sh                      # Databricks secret scope setup
│   ├── create-private-endpoint-rule.sh             # REST API fallback for the NCC PE rule
│   └── validate-dns.py                             # Standalone DNS check
└── screenshots/                                    # Console screenshots of both setup paths
```

---

## Validation Report

This repo's setup steps are checked against the official Neo4j and Microsoft documentation as of May 2026. [docs/reference/validation-report.md](docs/reference/validation-report.md) lists each correction with its source link.

---

## Contributing

Pull requests welcome. Please:

1. Test changes against a real AuraDB Virtual Dedicated Cloud (VDC) or AuraDS Enterprise instance on Azure. Test each path your change touches: NCC from Databricks Serverless, and Private Link from a VNet.
2. Update the validation report if Microsoft or Neo4j docs change

---

## Author

**Guhan Sivaji**, Principal Architect, Neo4j Field Engineering. Reach out via [Neo4j Field](https://github.com/neo4j-field).
