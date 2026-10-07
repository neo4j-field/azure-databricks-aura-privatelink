# Aura console steps

The Aura console has no API, so these steps stay manual in all four setup guides. The guides cover two paths, NCC and Private Link, and each path has a manual guide and a Terraform guide. Every setup guide links here for the Aura side and picks up again on the Databricks or Azure side.

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

1. In the [Aura console](https://console.neo4j.io), provision an **AuraDB Virtual Dedicated Cloud** or **AuraDS Enterprise** instance in your target Azure region.
2. Wait for the instance to reach **Running** state.
3. Note the **Connection URI**. Its public form is `neo4j+s://<aura-instance-id>.databases.neo4j.io`. Step 2 adds a **Private URI** next to it.

## Step 2: Enable Private Link in Aura (Network Access Configuration)

In the Aura console:

1. In the left sidebar, navigate to **Project settings → Security & Networking → Private endpoints**
2. Click **New network access configuration**
3. Configure:
   - **Product:** Choose the product that matches your tier, such as AuraDB VDC.
   - **Region:** Choose the same Azure region as your Aura instance.
   - **Target Azure Subscription IDs:** This field is mandatory. Enter your own Azure subscription ID for now. See [Step 3](#step-3-allow-list-the-consumer-subscription) for the subscription each path needs.
   - In **Edit network access configuration**, toggle **Enable Private Link**
4. Click **Save**
5. Copy the **Private Link service name**. The guides call it the PLS alias. It looks like `production-orch-<id>-service.<guid>.<region>.azure.privatelinkservice`.
6. Open your Aura instance details. You will now see a **Private URI** in addition to the Connection URI. The Private URI is what your applications will use.

> Private Link in Aura is **region-scoped, not instance-scoped**. Enabling it applies to all instances in the selected region under your tenant.

You now have the two values every setup path needs. Put them in the repo-root `.env`, created with `cp env.sample .env`, and load it:

```bash
AURA_PLS_ALIAS="production-orch-<id>-service.<guid>.<region>.azure.privatelinkservice"
AURA_PRIVATE_HOSTNAME="<aura-instance-id>.databases.neo4j.io"
```

```bash
source scripts/load-env.sh
```

`AURA_PRIVATE_HOSTNAME` is the host of `NEO4J_URI`, so the loader fills it in when you leave it out. [Environment setup](../env-setup.md) covers every other value.

## Step 3: Allow-list the consumer subscription

Aura accepts a private endpoint request only from a subscription listed in **Target Azure Subscription IDs**. The subscription you need depends on who creates the endpoint.

| Path | Subscription to add |
|------|---------------------|
| NCC (Databricks Serverless) | The **Databricks-managed** subscription that sends the request for your NCC. You learn its ID from the first failed create call. Your own subscription does not send this request, so listing only your own subscription is not enough. |
| Private Link (your VNet) | **Your own** subscription, the one that holds the VNet and the private endpoint. |
| Private Link, batch jobs in another subscription | The **batch workload's** subscription. See [Batch jobs in other VNets](../operations/batch-jobs-other-vnets.md). |

Keep every existing entry when you add a new one. The subscription you entered in Step 2 can stay, and an entry for one path does not affect the other path. Add only subscription IDs that come from your own failed create call or from your own `az account show` output. Do not copy subscription IDs from docs, screenshots, or other teams.

### NCC: if the first create call fails

When the NCC creates the private endpoint, the request comes from a Databricks-managed subscription for the workspace region. Aura rejects the request until that subscription is on the list. Expect this on your first NCC setup in a region, whether you use the CLI, REST, Terraform, or `scripts/automate.py`. The create call or `terraform apply` fails with this error:

```
ThirdPartyPrivateLinkServiceProvidedDuringPrivateEndpointCreationDoesNotExistOrIsNotVisible
```

1. Find the subscription ID in the error. It appears in a path like `/subscriptions/<guid>/resourceGroups/prod-<region>-snp-...`. `scripts/automate.py` prints this GUID for you.
2. In the Aura console, open **Project settings → Security & Networking → Private endpoints**. Edit the network access configuration from [Step 2](#step-2-enable-private-link-in-aura-network-access-configuration) for the Aura instance's region.
3. Add the GUID from the error to **Target Azure Subscription IDs**. Keep the existing entries.
4. Save and wait about a minute.
5. Retry the create call, `terraform apply`, or `scripts/automate.py run`.

Databricks can retry from more than one managed subscription in a region. Repeat the steps for each new GUID the error shows.

A Databricks-managed subscription is not yours, and it may serve other Databricks customers. The allow-list entry lets that subscription request a connection to your Aura service. Your approval in [Step 4](#step-4-approve-the-private-endpoint-in-the-aura-console) decides whether the connection is made, so approve only the request that matches your NCC rule.

Neo4j and Microsoft public docs do not describe this step. It is a known operational gotcha for NCC with a third-party Private Link service.

### Private Link: add your own subscription

The private endpoint lives in your VNet, so the request comes from your own subscription. Add that subscription ID before you create the endpoint. Print it with this command:

```bash
az account show --query id -o tsv
```

If the subscription is missing, `az network private-endpoint create` fails with the same `...DoesNotExistOrIsNotVisible` error. The endpoint can also stay `Pending` with no request in Aura. Add the subscription, wait about a minute, and create the endpoint again.

## Step 4: Approve the private endpoint in the Aura console

1. Return to Aura → **Project settings → Security & Networking → Private endpoints**
2. Open **Edit network access configuration** and go to **Step 3 of 4: Endpoint Connection Requests**
3. Locate the incoming endpoint request from the subscription you allow-listed in Step 3
4. Click **Accept**
5. Wait until status reads **Approved**

Then confirm the connection state on the consumer side:

- **NCC:** In the [Account Console](https://accounts.azuredatabricks.net/), go to **Security → Network connectivity configurations**, open your NCC, and select the **Private endpoint rules** tab. Refresh the page. The **Connection status** column for your rule should move from `PENDING` to `ESTABLISHED`.
- **Private Link:** The private endpoint connection state in Azure reads `Approved`.

> **NCC:** An NCC private endpoint rule that stays `PENDING`, `REJECTED`, or `DISCONNECTED` for 14 days expires. You must then recreate it, as in [Recreate an expired or failed rule](../setup-ncc-manual.md#recreate-an-expired-or-failed-rule). Do not leave the request waiting. Approve it as soon as it appears.
>
> **Private Link:** The 14-day expiry does not apply to a private endpoint in your VNet. A rejected or disconnected endpoint still needs to be recreated, as in [Recreate a rejected or disconnected endpoint](../setup-private-link-manual.md#recreate-a-rejected-or-disconnected-endpoint).

## Step 5: Disable public access on Aura

Private Link adds a private path. It does not close the public one. Until you disable public access, the instance stays reachable from the internet with only its password. Once validation succeeds:

1. Aura → **Project settings → Security & Networking → Private endpoints**
2. Toggle **Disable public access**
3. Watch the console until the status shows that public access is disabled. The change takes time to propagate.
4. Re-run the validation to confirm private-only access still works. See [Validate connectivity for NCC](../setup-ncc-manual.md#validate-connectivity) or [for Private Link](../setup-private-link-manual.md#run-the-validation-notebook)
5. From a machine outside Azure and outside any network linked to your private DNS, confirm the public endpoint is closed. The connection must fail or time out. A success means public access is still on.

   ```bash
   nc -vz -w 5 <aura-instance-id>.databases.neo4j.io 7687
   ```

After public access is off, laptops and workloads outside the private path lose access. See [Developer desktop access](../operations/developer-desktop-access.md) and [Batch jobs in other VNets](../operations/batch-jobs-other-vnets.md).
