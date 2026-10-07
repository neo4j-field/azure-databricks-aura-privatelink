# Aura console steps

The Aura console has no API, so these steps stay manual in all four setup paths. Every setup guide links here for the Aura side and picks up again on the Databricks or Azure side.

| Step | When you do it |
|------|----------------|
| [Step 1: Provision Aura](#step-1-provision-neo4j-aura-vdc-on-azure) | Before any setup path |
| [Step 2: Enable Private Link](#step-2-enable-private-link-in-aura-network-access-configuration) | Before any setup path |
| [Step 3: Allow-list the consumer subscription](#step-3-allow-list-the-consumer-subscription) | Before creating the endpoint, or after the first create call fails |
| [Step 4: Approve the endpoint](#step-4-approve-the-private-endpoint-in-the-aura-console) | After the endpoint request exists |
| [Step 5: Disable public access](#step-5-disable-public-access-on-aura) | After validation succeeds |

## Aura prerequisites

| Item | Value |
|------|-------|
| Aura tier | **AuraDB Virtual Dedicated Cloud (VDC)** or **AuraDS Enterprise** |
| Cloud | Azure |
| Console role | Org admin on the Aura tenant |

> Private Link is **not available** on Aura Professional or Business Critical on Azure. Verify your tier before starting.

## Step 1: Provision Neo4j Aura VDC on Azure

1. In the [Aura console](https://console.neo4j.io), provision an **AuraDB Virtual Dedicated Cloud** instance in your target Azure region.
2. Wait for the instance to reach **Running** state.
3. Note the **Connection URI** (public form: `neo4j+s://<dbid>.databases.neo4j.io`). This will be replaced by a **Private URI** after Step 2.

## Step 2: Enable Private Link in Aura (Network Access Configuration)

In the Aura console:

1. In the left sidebar, navigate to **Project settings → Security & Networking → Private endpoints**
2. Click **New network access configuration**
3. Configure:
   - **Product**: AuraDB VDC (matches your tier)
   - **Region**: same Azure region as your Aura instance
   - **Target Azure Subscription IDs**: This field is mandatory. Enter your own Azure subscription ID for now. See [Step 3](#step-3-allow-list-the-consumer-subscription) for the subscription each path needs.
   - In **Edit network access configuration**, toggle **Enable Private Link**
4. Click **Save**
5. Copy the **Private Link service name** (also called the PLS alias). It looks like `pls-<id>.<guid>.<region>.azure.privatelinkservice`.
6. Open your Aura instance details. You will now see a **Private URI** in addition to the Connection URI. The Private URI is what your applications will use.

> Private Link in Aura is **region-scoped, not instance-scoped**. Enabling it applies to all instances in the selected region under your tenant.

You now have the two values every setup path needs:

```bash
export AURA_PLS_ALIAS="pls-<id>.<guid>.<region>.azure.privatelinkservice"
export AURA_PRIVATE_HOSTNAME="<aura-id>.databases.neo4j.io"
```

## Step 3: Allow-list the consumer subscription

Aura accepts a private endpoint request only from a subscription listed in **Target Azure Subscription IDs**. The subscription you need depends on who creates the endpoint.

| Path | Subscription to add |
|------|---------------------|
| NCC (Databricks Serverless) | The **Databricks-managed** subscription that sends the request. You learn its ID from the first failed create call. |
| Private Link (your VNet) | **Your own** subscription, the one that holds the VNet and the private endpoint. |
| Private Link, batch jobs in another subscription | The **batch workload's** subscription. See [Batch jobs in other VNets](../operations/batch-jobs-other-vnets.md). |

### NCC: if the first create call fails

The error `ThirdPartyPrivateLinkService...DoesNotExistOrIsNotVisible` means the request came from a Databricks-managed subscription that Aura does not trust yet. Copy the subscription ID from that error and add it:

1. In the Aura console, open **Project settings → Security & Networking → Private endpoints** and edit the network access configuration from [Step 2](#step-2-enable-private-link-in-aura-network-access-configuration).
2. Add the subscription ID from the error to **Target Azure Subscription IDs**. Do not add subscription IDs from other sources.
3. Save, wait about a minute, then retry the create call or the apply.

Databricks can retry from more than one managed subscription in a region. Repeat the steps for each new ID the error shows.

## Step 4: Approve the private endpoint in the Aura console

1. Return to Aura → **Project settings → Security & Networking → Private endpoints**
2. Open **Edit network access configuration** and go to **Step 3 of 4: Endpoint Connection Requests**
3. Locate the incoming endpoint request from your consumer subscription
4. Click **Accept**
5. Wait until status reads **Approved**

Then confirm the connection state on the consumer side:

- **NCC:** In the [Account Console](https://accounts.azuredatabricks.net/), go to **Security → Network connectivity configurations**, open your NCC, and select the **Private endpoint rules** tab. Refresh the page. The **Connection status** column for your rule should move from `PENDING` to `ESTABLISHED`.
- **Private Link:** The private endpoint connection state in Azure reads `Approved`.

> A rule that stays in `PENDING`, `REJECTED`, or `DISCONNECTED` for **14 days will expire** and must be recreated. Don't leave half-finished setups. Approve within a day.

## Step 5: Disable public access on Aura

Private Link adds a private path. It does not close the public one. Until you disable public access, the instance stays reachable from the internet with only its password. Once validation succeeds:

1. Aura → **Project settings → Security & Networking → Private endpoints**
2. Toggle **Disable public access**
3. Wait for status to update. Propagation is not instant; monitor the console
4. Re-run the validation notebook to confirm private-only access still works
5. From a machine outside Azure and outside any network linked to your private DNS, confirm the public endpoint is closed. The connection must fail or time out. A success means public access is still on.

   ```bash
   nc -vz -w 5 <aura-id>.databases.neo4j.io 7687
   ```

After public access is off, laptops and workloads outside the private path lose access. See [Developer desktop access](../operations/developer-desktop-access.md) and [Batch jobs in other VNets](../operations/batch-jobs-other-vnets.md).
