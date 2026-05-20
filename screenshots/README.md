# Screenshots

This folder captures two validated connectivity paths:

- Customer-managed Azure Private Endpoint from a VM/VNet to Neo4j Aura
- Databricks-managed NCC private endpoint from Azure Databricks Serverless to Neo4j Aura

## Customer-managed Azure Private Endpoint path

End-to-end visual walkthrough validated against:

- Aura instance `b7253d3b` (UK South, `b7253d3b.databases.neo4j.io`)
- Azure subscription `FieldEng-SE-US-East` (`70bb2c8c-6c76-47c0-b6c9-82f0204f30ac`)
- Test VM `pl-test-win930_z1` in resource group `guhan-rg`, VNet `guhan-neo4j-vnet`, subnet `default` (East US)
- Private Endpoint `pe-neo4j-aura-uksouth` with NIC IP `172.16.0.6`

The screenshots are numbered in the chronological order an operator would see them when following [`infra/terraform/azure-private-endpoint/`](../infra/terraform/azure-private-endpoint/).

| # | File | Stage | What it shows |
|---|------|-------|---------------|
| 01 | `01-aura-network-access-overview.png` | Aura prep | Aura *Network Access* page — confirms `uksouth` region has `Private traffic: Enabled` and `Public traffic: Disabled` |
| 02 | `02-aura-network-access-config-subscriptions.png` | Aura prep | *Edit network access configuration* wizard, Step 1 of 4 — the Target Azure Subscription IDs registered for inbound PrivateLink (must include the consumer subscription) |
| 03 | `03-aura-pls-service-name-and-instructions.png` | Aura prep | Wizard Step 2 of 4 — the PLS service name (`production-orch-0477-service.<guid>.uksouth.azure.privatelinkservice`) and the Aura-provided Azure Portal instructions |
| 04 | `04-terraform-apply-success.png` | Apply | Output of `terraform apply` in `infra/terraform/azure-private-endpoint/` — `Apply complete! Resources: 4 added`, with the `private_endpoint_nic_ip = 172.16.0.6` output |
| 05 | `05-aura-pe-approval-step3.png` | Aura approval | Wizard Step 3 of 4 — *Endpoint Connection Requests* with the Terraform-created PE from RG `guhan-rg` shown as `Approved` |
| 06 | `06-aura-disable-public-traffic-step4.png` | Lockdown | Wizard Step 4 of 4 — `Disable public traffic` toggled on for instance `dbxuk-svrless-rosenblum / b7253d3b`; Aura now rejects any non-PrivateLink connections |
| 07 | `07-vm-powershell-private-dns-resolution.png` | Validation | PowerShell on the test VM: `nslookup b7253d3b.databases.neo4j.io` returns `172.16.0.6` (the PE NIC IP), and `Test-NetConnection ... -Port 7687` reports `TcpTestSucceeded: True` |
| 08 | `08-neo4j-browser-connected-via-privatelink.png` | Validation | Neo4j Browser opened against `https://b7253d3b.databases.neo4j.io:7687/browser/` from inside the VNet — query returns nodes against a real dataset (6,193 nodes / 11,540 relationships). End-to-end private path proven. |

## Databricks NCC path

Visual checkpoints from the Azure Databricks Serverless NCC setup validated against the same Aura instance.

| # | File | Stage | What it shows |
|---|------|-------|---------------|
| 09 | `09-azure-databricks-serverless-workspace-overview.png` | Workspace | Azure Portal overview for `guhan-dbx-ws`, showing a Premium Azure Databricks workspace with workspace type `Serverless`, URL `adb-7405618964107075.15.azuredatabricks.net`, and East US location |
| 10 | `10-aura-ncc-managed-subscription-ids.png` | Aura allow-list | Aura *Edit network access configuration* Step 1 with Databricks-managed Azure subscription IDs added as NCC retries exposed them in `ThirdPartyPrivateLinkService...DoesNotExistOrIsNotVisible` errors |
| 11 | `11-aura-pls-service-name-accepted.png` | Aura PLS | Aura Step 2 showing the accepted PrivateLink configuration and the Aura PLS alias `production-orch-0477-service.<guid>.uksouth.azure.privatelinkservice` |
| 12 | `12-databricks-ncc-private-endpoint-rule-established.png` | NCC rule | Databricks Account Console → NCC `ncc-aura-eastus-new` → *Private endpoint rules*, showing connection status `ESTABLISHED` and the two configured domain names |
| 13 | `13-databricks-ncc-private-endpoint-resource-tooltip.png` | NCC rule details | Same Databricks NCC rule with the Azure resource tooltip expanded, confirming the rule points at the Aura PLS alias |

The Databricks NCC screenshots correspond to the Terraform stack in [`infra/terraform/databricks-ncc/`](../infra/terraform/databricks-ncc/). The tested rule used:

- NCC name: `ncc-aura-eastus-new`
- Workspace ID: `7405618964107075`
- Primary domain: `b7253d3b.databases.neo4j.io`
- Routing domain: `p-b7253d3b-944d-0005.production-orch-0477.neo4j.io`

## Add your own for new deployments

For a fresh end-to-end walk-through, capture additionally:

| Suggested file | What to capture |
|----------------|-----------------|
| `azure-pe-overview.png` | Azure Portal → Private endpoints → `pe-neo4j-aura-uksouth` → Overview, showing `Approved` connection state and NIC IP |
| `azure-private-dns-zone-records.png` | Azure Portal → Private DNS zones → `databases.neo4j.io` → record set list, showing the A record for `<dbid>` pointing to the PE NIC IP |
| `databricks-ncc-rule-established.png` | Databricks Account Console → NCC → rule status `ESTABLISHED` and configured domain names |
| `databricks-notebook-private-dns.png` | Serverless notebook DNS check showing `b7253d3b.databases.neo4j.io` resolving to a private IP |
| `databricks-notebook-neo4j-query.png` | Serverless notebook query success over `bolt+s://` or `neo4j+s://` with the resolver workaround |

PNG only. Keep file sizes under ~500 KB each. Redact any account / subscription IDs you do not want public.
