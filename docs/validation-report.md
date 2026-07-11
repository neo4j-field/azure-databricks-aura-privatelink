# Validation Report

Reconciliation of the original PDF guide "Neo4j Aura PrivateLink + Azure Databricks Serverless" against the latest official documentation (as of May 2026).

## Sources

- Neo4j Aura — [Secure connections / Network access](https://neo4j.com/docs/aura/platform/security/secure-connections/)
- Microsoft Learn — [Configure private connectivity to Azure resources (Databricks NCC)](https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/serverless-private-link)
- Microsoft Learn — [Serverless compute plane networking](https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/)
- Microsoft Learn — [Manage private endpoint rules](https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/manage-private-endpoint-rules)
- Microsoft Learn — [Private Link and DNS integration at scale (hub-and-spoke)](https://learn.microsoft.com/en-us/azure/cloud-adoption-framework/ready/azure-best-practices/private-link-and-dns-integration-at-scale)
- Microsoft Learn — [Azure DNS Private Resolver](https://learn.microsoft.com/en-us/azure/dns/dns-private-resolver-overview)

## Summary

| PDF Section | Status | Issue |
|-------------|--------|-------|
| Architecture overview | OK | Accurate |
| NCC concept | OK | Accurate |
| Step 1 (Aura provision) | Needs additions | Missing target-subscription registration, Private URI distinction, region-scope nuance |
| Step 2 (Workspace) | Needs additions | Missing Premium plan + account-admin prereqs |
| Step 3 (NCC create) | Needs additions | Missing limits (10/100/50), 10-min propagation, exact UI labels |
| Step 4 (PE rule) | **Needs rework** | Third-party PLS requires REST API with `domain_names`; UI flow does not apply |
| Step 5 (Approve) | OK | Add 14-day expiry note |
| Step 6 (DNS) | Needs additions | Clarify third-party PLS DNS via `domain_names` |
| Step 7 (Validate) | Needs additions | Add `%pip install neo4j`, secret scope creation, DNS check |
| Step 8 (Disable public) | OK | Add propagation-delay note |
| Production best practices | Needs additions | Example notebook should show retry/idempotency |

## Step-by-Step Deltas

### Step 1 — Aura Provisioning

**Missing in PDF:**

1. **Target Azure Subscription ID registration** in Aura Network Access config. For Databricks Serverless NCC, the private endpoint request comes from a Databricks-managed Azure subscription; without that subscription on Aura's allow-list, the PE request never surfaces for approval.
2. **Region-scoped, not instance-scoped**: enabling Private Link applies to all instances in the region under the tenant.
3. **Private URI vs Connection URI**: each instance gets a separate Private URI after Private Link is enabled.
4. **Exact console path**: Security → Network Access → Network Access → New network access configuration.

### Step 2 — Databricks Workspace

**Missing in PDF:**

1. **Premium plan required** on both the account and the workspace.
2. **Azure Databricks account admin** role required (not workspace admin).

### Step 3 — NCC Creation

**Missing in PDF:**

1. **Limits**: 10 NCCs/region/account, 100 PEs/region, 50 workspaces/NCC.
2. **Exact location**: Account Console (`accounts.azuredatabricks.net`) → Security → Network connectivity configurations → Add network configuration.
3. **Post-attach**: wait 10 minutes, then restart serverless compute.

### Step 4 — Private Endpoint Rule (the biggest fix)

**Issue:** PDF describes:
> Resource ID → Neo4j Private Link Service Name
> Domain → Neo4j private hostname

The official Databricks NCC UI for private endpoint rules expects an **Azure-native ARM resource ID + sub-resource ID** — not a PLS alias and not a domain field. The UI flow does not work for third-party Private Link Services.

**Correct path:** Use the Network Connectivity Configurations REST API (same path used for Azure App Gateway v2), passing:

- `resource_id` — the PLS alias from Aura
- `domain_names` — array containing the Aura Private URI hostname

Without `domain_names`, NCC's managed DNS will not route the Aura hostname to the private endpoint. The UI does not expose `domain_names`.

Aura falls under the Databricks "Resources behind a Standard Load Balancer" supported-resource category.

### Step 5 — Approval

**Add:** NCC rule states are `PENDING` → `ESTABLISHED` → `REJECTED`/`DISCONNECTED`/`EXPIRED`. A rule held in non-`ESTABLISHED` states for **14 days expires** and must be recreated.

### Step 6 — DNS

**Clarification needed:**

- For Azure-native services, NCC creates managed Private DNS zones automatically.
- For third-party PLS (Aura), DNS routing depends on the `domain_names` field in the PE rule. Without it, DNS resolves to public IP and traffic does not use the private endpoint.
- The Aura docs mention "configure Azure Private DNS within your VNet" — this guidance applies to classic Databricks compute with a customer VNet, not Serverless. Serverless has no customer VNet, so the DNS responsibility shifts entirely to NCC.
- **Customer-managed PE path has two valid DNS topologies**, and the PDF assumes only the first:
  - *Self-managed (single VNet):* the consumer creates a `databases.neo4j.io` private DNS zone, links it to the VNet, and adds the Aura A record. This is the `azure-private-endpoint` stack's default (`manage_private_dns = true`).
  - *Central / hub-and-spoke:* a shared hub VNet owns the private DNS zones (frequently behind Azure DNS Private Resolver) and spokes consume them via peering and zone links. This is Microsoft's recommended enterprise pattern (see [Private Link and DNS integration at scale](https://learn.microsoft.com/en-us/azure/cloud-adoption-framework/ready/azure-best-practices/private-link-and-dns-integration-at-scale)). Here the stack must **not** create its own zone — a duplicate zone in a spoke causes split-brain resolution, and many tenants block spoke-level zone creation via Azure Policy. Set `manage_private_dns = false` so only the Private Endpoint is created, then add the A record (pointing at the PE NIC IP) and the `p-*` routing-host records in the hub zone.
  - The `p-<dbid>-...neo4j.io` routing hostnames (see item 4 below) must also be present in whichever zone owns the hostname, in both topologies.

### Step 7 — Validation

**Missing in PDF example code:**

1. `%pip install neo4j` (driver is not pre-installed on serverless)
2. Secret scope creation prerequisite
3. DNS sanity check to verify private IP resolution

### Step 8 — Disable Public Access

**Add:** Propagation is not instant. Monitor the Aura console for status update before considering the change complete.

## Items entirely missing from the PDF

1. **Azure Databricks networking costs** — serverless egress to customer resources is billed.
2. **June 9, 2026 deadline** for Azure storage allowlists to migrate to Network Security Perimeter with the `AzureDatabricksServerless` service tag.
3. **Production retry/idempotency example** — the PDF recommends these but the code sample does not show them.
4. **Bolt routing on VDC clusters** — routing URIs returned in cluster topology can include `p-*.neo4j.io` hostnames. These must be added to NCC `domain_names` alongside `<dbid>.databases.neo4j.io`; otherwise the first DNS/TCP checks pass but the driver fails with `Cannot resolve address p-...neo4j.io:7687`.
