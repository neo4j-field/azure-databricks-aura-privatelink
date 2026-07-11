# Azure Databricks Serverless + Neo4j Aura PrivateLink

Production-grade reference for establishing private, end-to-end connectivity between **Azure Databricks Serverless** and **Neo4j Aura (Azure)** using **Azure Private Link** and Databricks **Network Connectivity Configurations (NCC)**.

This repository accompanies the architecture guide "Neo4j Aura PrivateLink + Azure Databricks Serverless" and provides the corrected, validated step-by-step setup along with notebooks, Terraform, and helper scripts.

A worked reference deployment is included for:

- Aura instance `b7253d3b` (`b7253d3b.databases.neo4j.io`), UK South
- PLS alias `production-orch-0477-service.<guid>.uksouth.azure.privatelinkservice` (the exact alias lives in `infra/terraform/databricks-ncc/terraform.tfvars`; confirm it against the Aura console at deploy time)
- Databricks workspace `partner-demo-workspace-v2` (`adb-1098933906466604.4.azuredatabricks.net`), serverless, East US

The workspace is in East US and the Aura instance is in UK South, so the NCC and its private endpoint sit in East US and reach the UK South Aura PLS cross-region.

The Terraform under [`infra/terraform/`](infra/terraform/) is split into two sibling stacks:

- [`infra/terraform/databricks-ncc/`](infra/terraform/databricks-ncc/) — Databricks NCC + private endpoint rule + workspace binding (use this for serverless).
- [`infra/terraform/azure-private-endpoint/`](infra/terraform/azure-private-endpoint/) — Customer-managed Azure Private Endpoint + private DNS zone (use this for classic Databricks, AKS, ADF, jump VMs).

End-to-end validation for the serverless path runs via [`notebooks/01_validate_connectivity.py`](notebooks/01_validate_connectivity.py), the deployment-agnostic check that `scripts/automate.py` runs by default. [`notebooks/03_dbxuk_svrless_drose_smoke_test.py`](notebooks/03_dbxuk_svrless_drose_smoke_test.py) is the original branded worked example, kept for reference. See [`screenshots/`](screenshots/) for both the customer-managed Azure Private Endpoint walkthrough and the Databricks NCC setup screens.

The customer-managed PE path has been validated end-to-end against this deployment from a Windows VM in East US connecting to UK South Aura. See [`screenshots/`](screenshots/) for the captured walkthrough — including the Aura-side approval, the `Disable public traffic` lockdown, the VM's `nslookup` resolving to the PE NIC, and a working Neo4j Browser session over the private path.

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
| Azure Databricks Serverless (this repo's primary target) | [`infra/terraform/databricks-ncc/`](infra/terraform/databricks-ncc/) | Serverless compute lives in Databricks-managed subscriptions; the only supported private-network path is NCC + private endpoint rule. |
| Classic Azure Databricks (VNet-injected), AKS, ADF self-hosted IR, jump VMs, Functions on VNet integration | [`infra/terraform/azure-private-endpoint/`](infra/terraform/azure-private-endpoint/) | Customer-managed Private Endpoint into Aura's PLS plus a `databases.neo4j.io` private DNS zone linked to your VNet. |

The two stacks are independent. You can run only the NCC stack, only the Azure-native stack, or both side-by-side if different teams in the same subscription consume Aura over both surfaces.

### Decide who owns DNS (customer-managed stack only)

On the customer-managed (`azure-private-endpoint/`) stack, decide **who owns DNS for `databases.neo4j.io`** before you apply. Serverless (NCC) has no such choice — NCC owns DNS entirely — so this applies only to the customer-managed path:

- **Self-managed (single VNet):** the default (`manage_private_dns = true`). The stack creates the `databases.neo4j.io` private DNS zone, links it to your VNet, and writes the Aura A record. Nothing else to wire.
- **Central / hub-and-spoke:** set `manage_private_dns = false` when your organization manages private DNS centrally — a hub VNet holds the zones (often behind Azure DNS Private Resolver) and spokes consume them over peering and zone links. The stack then provisions the Private Endpoint only; you add the A record plus the `p-*` routing-host records in your hub zone.

See the [private-DNS section of the azure-private-endpoint README](infra/terraform/azure-private-endpoint/README.md#private-dns-self-managed-vs-central-hub-and-spoke) for the exact steps and [docs/architecture.md](docs/architecture.md#dns-ownership-who-answers-for-the-aura-hostname) for the rationale.

Once you have picked a stack, see [Setup: automated or manual](#setup-automated-or-manual) to choose how to run it.

## Setup: automated or manual

Two Aura-console actions have no API and stay manual either way: adding the Databricks-managed subscription to Aura's allow-list (Step 2) and approving the private endpoint (Step 7). Everything else can be scripted.

**Automated (recommended for the Serverless / NCC demo).** `scripts/automate.py` drives the Databricks side end to end: it runs Terraform, polls the endpoint rule to ESTABLISHED, restarts warehouses, loads the `neo4j` secret scope, and runs the validation notebook. It is re-entrant and pauses only for the two Aura actions above.

```bash
uv run scripts/automate.py run --account-profile <name>
```

See [AUTOMATE-README.md](AUTOMATE-README.md) for the full flow. To remove the Databricks side afterward, see [Teardown](#teardown).

**Manual / step-by-step.** Follow Steps 1-9 below. Use this to understand each step, or when you are on the Azure-native stack (`azure-private-endpoint/`), which the orchestrator does not cover.

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
   - **Target Azure Subscription IDs**: for customer-managed Azure Private Endpoints, paste the subscription ID(s) where the private endpoint will be created. For Databricks Serverless NCC, the request comes from a **Databricks-managed Azure subscription**, not necessarily your workspace subscription; if the first NCC apply fails with a `ThirdPartyPrivateLinkService...DoesNotExistOrIsNotVisible` error, copy the subscription ID from that error into the Aura allow-list and retry. **This is mandatory** — without the right subscription, Aura will not see incoming private endpoint requests.
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
> The account console UI requires an Azure-native resource ID + subresource ID. Neo4j Aura is a **third-party/customer-managed Private Link Service**, so the UI flow does not apply. Use Terraform in `infra/terraform/databricks-ncc/` or the Network Connectivity Configurations REST API with the Aura PLS alias as `resource_id` and the Aura hostname in `domain_names`.

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
  "domain_names": ["${AURA_PRIVATE_HOSTNAME}"]
}
EOF
```

Where:

- `AURA_PLS_ALIAS` = the Private Link service name returned by the Aura console in Step 2
- `AURA_PRIVATE_HOSTNAME` = the Private URI hostname from Aura (e.g., `d48d6199.databases.neo4j.io`)

For Aura VDC routing, the Neo4j driver can also receive `p-*.neo4j.io` Bolt addresses from the routing table. If a validation notebook fails with `Cannot resolve address p-...neo4j.io:7687`, add that hostname to the same private endpoint rule `domain_names` list. The Terraform stack exposes this as `aura_extra_domain_names`.

After submission the rule will appear in the NCC with status `PENDING`.

### Step 7: Approve the Private Endpoint in the Aura Console

1. Return to Aura → **Security → Network Access**
2. Locate the incoming endpoint request from the Databricks-managed Azure subscription
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

If the Bolt validation reveals additional routing hostnames, verify them too:

```python
for host in [
    "b7253d3b.databases.neo4j.io",
    "p-b7253d3b-944d-0005.production-orch-0477.neo4j.io",
]:
    print(host, socket.gethostbyname(host))
```

Then run the end-to-end validation notebook for the reference deployment: [notebooks/03_dbxuk_svrless_drose_smoke_test.py](notebooks/03_dbxuk_svrless_drose_smoke_test.py). For a generic version, use [notebooks/01_validate_connectivity.py](notebooks/01_validate_connectivity.py).

### Step 9: Disable Public Access on Aura (Recommended)

Once validation succeeds:

1. Aura → **Security → Network Access**
2. Toggle **Disable public access**
3. Wait for status to update — propagation is not instant; monitor the console
4. Re-run the validation notebook to confirm private-only access still works

---

## What's next: access after public traffic is disabled

Once public access is off, two categories of access break that the initial setup
does not cover. Both have dedicated guides:

| Scenario | Guide |
|----------|-------|
| **Developer on a laptop** needs Neo4j Desktop or a browser to reach the private instance | [`docs/developer-desktop-access.md`](docs/developer-desktop-access.md) — Azure Bastion (Option A) or Azure P2S VPN with OpenVPN (Option B, recommended for regulated industries) |
| **Batch jobs, pipelines, or services** running in a different VNet or subscription fail to reach Aura | [`docs/batch-jobs-other-vnets.md`](docs/batch-jobs-other-vnets.md) — Private DNS Zone VNet links and VNet peering (same subscription) or a new Private Endpoint (cross-subscription) |

---

## Teardown

Removing the Databricks side is mostly `terraform destroy` on the `databricks-ncc` stack, plus one manual step that has no clean automation and two Aura-console actions that have no API.

**The NCC gotcha.** `terraform destroy` deletes the private endpoint rule and the workspace binding, but it **cannot delete the NCC itself**. The NCC is attached to the workspace through the workspace's own `network_connectivity_config_id`, and the Databricks account API has no way to *unset* an NCC on a workspace — it can only be **swapped for a different one**. So destroy fails on the final resource with:

```
cannot delete mws network connectivity config: ... unable to be deleted because
it is attached to one or more workspaces: <workspace-id>
```

That error is expected. To finish teardown, reassign the workspace to a throwaway placeholder NCC, then delete the original.

### Step 1: Destroy the stack

```bash
terraform -chdir=infra/terraform/databricks-ncc destroy
```

This removes the private endpoint rule and the workspace binding, then fails on the NCC with the message above. Note the NCC id (also available via `terraform -chdir=infra/terraform/databricks-ncc output ncc_id`).

### Step 2: Detach the NCC by swapping in a placeholder

The workspace must always point at *some* NCC, so free the original by pointing the workspace at an empty placeholder instead. The placeholder's region **must match the workspace region** — an NCC only binds to a workspace in its own region.

**Account console:** Workspaces → your workspace → **Update workspace** → under **Network connectivity configurations** pick a different NCC (create an empty one first if you have none) → **Update**.

**REST API** (mirrors Step 6; `ACCOUNT_ID`, `WORKSPACE_ID`, and a `DATABRICKS_TOKEN` bearer for `accounts.azuredatabricks.net`):

```bash
BASE="https://accounts.azuredatabricks.net/api/2.0/accounts/${ACCOUNT_ID}"

# a. Create an empty placeholder NCC in the workspace's region.
PLACEHOLDER=$(curl -s -X POST "${BASE}/network-connectivity-configs" \
  -H "Authorization: Bearer ${DATABRICKS_TOKEN}" -H "Content-Type: application/json" \
  --data '{"name":"ncc-placeholder","region":"eastus"}' \
  | python3 -c 'import sys, json; print(json.load(sys.stdin)["network_connectivity_config_id"])')

# b. Swap the workspace onto the placeholder — this frees the original NCC.
curl -s -X PATCH "${BASE}/workspaces/${WORKSPACE_ID}" \
  -H "Authorization: Bearer ${DATABRICKS_TOKEN}" -H "Content-Type: application/json" \
  --data "{\"network_connectivity_config_id\": \"${PLACEHOLDER}\"}"
```

### Step 3: Delete the original NCC and reconcile Terraform

```bash
# ORIGINAL_NCC_ID is the NCC id from Step 1.
curl -s -X DELETE "${BASE}/network-connectivity-configs/${ORIGINAL_NCC_ID}" \
  -H "Authorization: Bearer ${DATABRICKS_TOKEN}"

terraform -chdir=infra/terraform/databricks-ncc state rm \
  databricks_mws_network_connectivity_config.ncc
```

The `state rm` drops the already-deleted NCC from Terraform state so the stack reads clean. The placeholder NCC stays attached to the workspace; it is empty and harmless — leave it or delete it later from the console.

### Step 4: Aura-side cleanup (manual, no API)

1. Aura console → **Security → Network Access**: remove the now-orphaned private endpoint approval.
2. Optionally remove the Databricks-managed subscription from **Target Azure Subscription IDs** if nothing else uses it.

The `neo4j` secret scope and the imported validation notebook remain in the workspace; delete them by hand for a full reset.

---

## Repository Layout

```
.
├── README.md                                       # This file
├── LICENSE                                         # Apache 2.0
├── docs/
│   ├── architecture.md                             # Detailed architecture and rationale
│   ├── validation-report.md                        # PDF-vs-docs validation findings
│   ├── troubleshooting.md                          # Common issues and fixes
│   ├── developer-desktop-access.md                 # Neo4j Desktop / browser access after public traffic disabled
│   └── batch-jobs-other-vnets.md                   # Private Link connectivity for workloads in other VNets
├── notebooks/
│   ├── 01_validate_connectivity.py                 # Generic DNS + Bolt sanity check
│   ├── 02_delta_to_neo4j.py                        # Round-trip: Delta -> Neo4j -> Delta
│   ├── 03_dbxuk_svrless_drose_smoke_test.py        # End-to-end PrivateLink smoke test for the reference deployment
│   └── 04_serverless_push_pull_demo.py             # Small push/pull demo over PrivateLink (synthetic customers)
├── infra/
│   └── terraform/
│       ├── README.md                               # Index: which stack to pick
│       ├── databricks-ncc/                         # Databricks NCC + PE rule + workspace binding (serverless path)
│       ├── azure-private-endpoint/                 # Customer-managed PE + private DNS (non-serverless path)
│       └── jumpbox/                                # Azure Bastion + jump box VM for developer desktop access
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
| Customer-managed PE in a central-DNS (hub-and-spoke) org: a self-created zone collides with the hub or is blocked by Azure Policy | Set `manage_private_dns = false`; add the A record + routing-host records in the hub zone |

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
