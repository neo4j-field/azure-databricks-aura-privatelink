# Azure Databricks Serverless + Neo4j Aura PrivateLink

Production-grade reference for establishing private, end-to-end connectivity between **Azure Databricks Serverless** and **Neo4j Aura (Azure)** using **Azure Private Link** and Databricks **Network Connectivity Configurations (NCC)**.

This repository accompanies the architecture guide "Neo4j Aura PrivateLink + Azure Databricks Serverless" and provides the corrected, validated step-by-step setup along with notebooks, Terraform, and helper scripts.

A worked reference deployment is included for:

- Aura instance `b7253d3b` (`b7253d3b.databases.neo4j.io`), UK South
- PLS alias `production-orch-0477-service.10388da2-e87f-4402-9140-b5ab816fc8d6.uksouth.azure.privatelinkservice`
- Databricks workspace `dbxuk-svrless-drose` (`adb-7405607696817769.9.azuredatabricks.net`), serverless, UK South

The Terraform under [`infra/terraform/`](infra/terraform/) covers the serverless NCC path; [`infra/terraform/azure-native-pe/`](infra/terraform/azure-native-pe/) covers the customer-managed PE path shown in the Aura console wizard. End-to-end validation runs via [`notebooks/03_dbxuk_svrless_drose_smoke_test.py`](notebooks/03_dbxuk_svrless_drose_smoke_test.py).

---

## Problem Statement

Enterprise teams need to exchange data between Delta tables in Azure Databricks and Neo4j Aura graph databases without traversing the public internet. Public TLS endpoints with IP allowlists are operationally fragile (egress IPs change, allowlists drift) and not aligned with Zero Trust principles for regulated workloads.

This repository documents a private-only architecture that:

- Routes all Bolt traffic between Databricks Serverless and Neo4j Aura over the Azure backbone
- Eliminates customer-managed VNets on the consumer side via Databricks NCC
- Supports disabling public ingress on Aura entirely after validation

---

## Architecture

```
+--------------------------------------------------+
| Azure Databricks Serverless (region X)           |
|                                                  |
|  [Notebook / Job / SQL Warehouse]                |
|              |                                   |
|              v                                   |
|  +-----------------------------+                 |
|  | Network Connectivity Config |                 |
|  | (NCC, account-level)        |                 |
|  +-------------+---------------+                 |
+----------------|---------------------------------+
                 | (private endpoint rule)
                 v
        +-----------------------+
        | Azure Private Link    |
        | (managed by Databricks|
        |  on customer behalf)  |
        +-----------+-----------+
                    |
                    v  (over Azure backbone)
        +-----------------------+
        | Neo4j Aura            |
        | Private Link Service  |
        | (Aura-managed         |
        |  subscription)        |
        +-----------+-----------+
                    |
                    v
        +-----------------------+
        | Neo4j Aura VDC        |
        | (Azure, region Y)     |
        +-----------------------+
```

See [docs/architecture.md](docs/architecture.md) for a detailed walkthrough including the data-plane vs control-plane distinction and DNS routing behavior.

---

## Prerequisites

### Aura side

| Item | Value |
|------|-------|
| Aura tier | **AuraDB Virtual Dedicated Cloud (VDC)** or **AuraDS Enterprise** |
| Cloud | Azure |
| Console role | Org admin on the Aura tenant |

> Private Link is **not available** on Aura Professional or Business Critical on Azure. Verify your tier before starting.

### Databricks side

| Item | Value |
|------|-------|
| Workspace plan | **Premium** |
| Account plan | **Premium** |
| Role | **Azure Databricks account admin** (workspace admin is not sufficient) |
| Region | Must match the NCC region |

### Azure side

| Item | Value |
|------|-------|
| Subscription role | Permission to create Private Endpoints and approve PLS connections |
| Region | Region that supports Private Link for your target resources |

---

## Choose your Terraform stack

| Consumer | Stack | Why |
|---|---|---|
| Azure Databricks Serverless (this repo's primary target) | [`infra/terraform/`](infra/terraform/) | Serverless compute lives in Databricks-managed subscriptions; the only supported private-network path is NCC + private endpoint rule. |
| Classic Azure Databricks (VNet-injected), AKS, ADF self-hosted IR, jump VMs, Functions on VNet integration | [`infra/terraform/azure-native-pe/`](infra/terraform/azure-native-pe/) | Customer-managed Private Endpoint into Aura's PLS plus a `databases.neo4j.io` private DNS zone linked to your VNet. |

The two stacks are independent. You can run only the NCC stack, only the Azure-native stack, or both side-by-side if different teams in the same subscription consume Aura over both surfaces.

## Setup Steps (Validated)

### Step 1: Provision Neo4j Aura VDC on Azure

1. In the [Aura console](https://console.neo4j.io), provision an **AuraDB Virtual Dedicated Cloud** instance in your target Azure region.
2. Wait for the instance to reach **Running** state.
3. Note the **Connection URI** (public form: `neo4j+s://<dbid>.databases.neo4j.io`). This will be replaced by a **Private URI** after Step 2.

### Step 2: Enable Private Link in Aura (Network Access Configuration)

In the Aura console:

1. Navigate to **Security → Network Access → Network Access**
2. Click **New network access configuration**
3. Configure:
   - **Product**: AuraDB VDC (matches your tier)
   - **Region**: same Azure region as your Aura instance
   - **Target Azure Subscription IDs**: paste the subscription ID(s) where your Databricks workspace lives (e.g., `70bb2c8c-6c76-47c0-b6c9-82f0204f30ac`). **This is mandatory** — without it, the Aura console will not see incoming private endpoint requests.
   - Toggle **Enable Private Link**
4. Click **Save**
5. Copy the **Private Link service name** (also called the PLS alias) — it looks like `pls-<id>.<guid>.<region>.azure.privatelinkservice`.
6. Open your Aura instance details — you will now see a **Private URI** in addition to the Connection URI. The Private URI is what your applications will use.

> Private Link in Aura is **region-scoped, not instance-scoped**. Enabling it applies to all instances in the selected region under your tenant.

### Step 3: Create a Databricks Workspace (or use existing)

If you don't already have one:

1. Deploy an **Azure Databricks Workspace** on the **Premium** plan in your chosen region.
2. Confirm **Serverless compute** is enabled (`Settings → Compute → Serverless`).

### Step 4: Create a Network Connectivity Configuration (NCC)

In the **Account Console** (`https://accounts.azuredatabricks.net/`), as **account admin**:

1. Sidebar → **Security** → **Network connectivity configurations**
2. Click **Add network configuration**
3. Provide:
   - **Name**: e.g., `ncc-eastus-prod`
   - **Region**: must match the workspace region exactly
4. Click **Add**

**Limits to know:**

- Maximum **10 NCCs per region per account**
- Maximum **100 private endpoints per region** (distributed across your NCCs)
- An NCC can attach to up to **50 workspaces**

### Step 5: Attach the NCC to your Workspace

1. Account Console → **Workspaces** → select your workspace
2. Click **Update workspace**
3. In **Network connectivity configurations**, select your NCC
4. Click **Update**
5. **Wait 10 minutes** for propagation
6. **Restart any running serverless services** in the workspace

### Step 6: Add a Private Endpoint Rule for Neo4j Aura PLS

> **Important — must use REST API, not the account console UI.**
>
> The account console UI requires an Azure-native resource ID + subresource ID. Neo4j Aura is a **third-party Private Link Service** (similar to Azure App Gateway v2 in this regard), so the UI flow does not apply. You must use the Network Connectivity Configurations REST API with `resource_id` + `group_id` + `domain_names`.

See [scripts/create-private-endpoint-rule.sh](scripts/create-private-endpoint-rule.sh) for a ready-to-run script.

Minimal example:

```bash
curl --location --request POST \
  "https://accounts.azuredatabricks.net/api/2.0/accounts/${ACCOUNT_ID}/network-connectivity-configs/${NCC_ID}/private-endpoint-rules" \
  --header "Authorization: Bearer ${DATABRICKS_TOKEN}" \
  --header "Content-Type: application/json" \
  --data @- <<EOF
{
  "resource_id":  "${AURA_PLS_ALIAS}",
  "group_id":     "${AURA_GROUP_ID}",
  "domain_names": ["${AURA_PRIVATE_HOSTNAME}"]
}
EOF
```

Where:

- `AURA_PLS_ALIAS` = the Private Link service name returned by the Aura console in Step 2
- `AURA_GROUP_ID` = the group identifier provided by Aura alongside the PLS alias (typically the service name component)
- `AURA_PRIVATE_HOSTNAME` = the Private URI hostname from Aura (e.g., `d48d6199.databases.neo4j.io`)

After submission the rule will appear in the NCC with status `PENDING`.

### Step 7: Approve the Private Endpoint in the Aura Console

1. Return to Aura → **Security → Network Access**
2. Locate the incoming endpoint request from your Azure subscription
3. Click **Accept**
4. Wait until status reads **Approved**
5. In Databricks, refresh the NCC view — the rule status should transition to `ESTABLISHED`

> A rule that stays in `PENDING`, `REJECTED`, or `DISCONNECTED` for **14 days will expire** and must be recreated. Don't leave half-finished setups.

### Step 8: Verify DNS and Connectivity

DNS resolution for the Aura Private URI is handled by Databricks NCC because you supplied `domain_names` in Step 6. From a serverless notebook:

```python
import socket
host = "b7253d3b.databases.neo4j.io"   # use your own Aura instance id
print(socket.gethostbyname(host))      # should return a 10.x or similar private IP
```

Then run the end-to-end validation notebook for the reference deployment: [notebooks/03_dbxuk_svrless_drose_smoke_test.py](notebooks/03_dbxuk_svrless_drose_smoke_test.py). For a generic version, use [notebooks/01_validate_connectivity.py](notebooks/01_validate_connectivity.py).

### Step 9: Disable Public Access on Aura (Recommended)

Once validation succeeds:

1. Aura → **Security → Network Access**
2. Toggle **Disable public access**
3. Wait for status to update — propagation is not instant; monitor the console
4. Re-run the validation notebook to confirm private-only access still works

---

## Repository Layout

```
.
├── README.md                                       # This file
├── LICENSE                                         # Apache 2.0
├── docs/
│   ├── architecture.md                             # Detailed architecture and rationale
│   ├── validation-report.md                        # PDF-vs-docs validation findings
│   └── troubleshooting.md                          # Common issues and fixes
├── notebooks/
│   ├── 01_validate_connectivity.py                 # Generic DNS + Bolt sanity check
│   ├── 02_delta_to_neo4j.py                        # Round-trip: Delta -> Neo4j -> Delta
│   ├── 03_dbxuk_svrless_drose_smoke_test.py        # End-to-end PrivateLink smoke test for the reference deployment
│   └── 04_serverless_push_pull_demo.py             # Small push/pull demo over PrivateLink (synthetic customers)
├── infra/
│   └── terraform/
│       ├── main.tf, variables.tf, ...              # Databricks NCC + PE rule + workspace binding (serverless path)
│       └── azure-native-pe/                        # Customer-managed PE + private DNS (non-serverless path)
├── scripts/
│   ├── create-secret-scope.sh                      # Databricks secret scope setup
│   ├── create-private-endpoint-rule.sh             # REST API fallback for the NCC PE rule
│   └── validate-dns.py                             # Standalone DNS check
├── prompts/                                        # Prompts used to build this repo
└── screenshots/                                    # Console screenshots from the reference deployment
```

---

## Production Best Practices

- **Run as Databricks Jobs**, not interactive notebooks. Notebooks are for development and validation.
- **Store credentials in Azure Key Vault**, exposed via a Key-Vault-backed Databricks secret scope. Never put credentials in notebooks or repo files.
- **Idempotent writes**: use Cypher `MERGE` (not `CREATE`) on `:Label {id: $id}` keys.
- **Batch writes**: use `UNWIND` with batch sizes of 1k–10k rows depending on payload.
- **Retry transient failures**: the `neo4j` driver raises `TransientError`; wrap writes with bounded retries (see [notebooks/02_delta_to_neo4j.py](notebooks/02_delta_to_neo4j.py)).
- **Monitor**: NCC private endpoint status, Databricks job runs, Aura performance metrics.
- **Cost awareness**: Azure Databricks bills for networking costs when serverless workloads connect to customer resources. Plan for this in your TCO.

---

## Limitations and Gotchas

| Gotcha | Mitigation |
|--------|-----------|
| Aura tier must be VDC; Professional/Business Critical do not support Azure Private Link | Verify tier before any work |
| NCC private endpoint rules for third-party PLS need REST API (UI is Azure-native-only) | Use [scripts/create-private-endpoint-rule.sh](scripts/create-private-endpoint-rule.sh) |
| `domain_names` must be supplied or DNS will resolve to public IP | Always include the Aura Private URI hostname in the API call |
| 14-day expiry on unapproved rules | Approve promptly in Aura console |
| 10-minute NCC propagation after attach | Wait, then restart serverless services |
| Aura Private Link is region-scoped, not instance-scoped | Plan multi-region setups accordingly |

---

## Validation Report

This repo's setup steps are reconciled against the latest official documentation (May 2026). See [docs/validation-report.md](docs/validation-report.md) for the specific deltas between this guide and the original PDF, with source links.

---

## Contributing

Pull requests welcome. Please:

1. Test changes against a real Aura VDC + Databricks Serverless setup
2. Update the validation report if Microsoft or Neo4j docs change
3. Capture any new prompts used in `prompts/`

---

## Author

**Guhan Sivaji** — Principal Architect, Neo4j Field Engineering. Reach out via [Neo4j Field](https://github.com/neo4j-field).
