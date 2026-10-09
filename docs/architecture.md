# Architecture

## Goal

This repo connects Azure workloads to Neo4j Aura over two private paths: the NCC path for Azure Databricks Serverless compute, and the Private Link path for workloads in your own VNet. Both paths carry Bolt+TLS traffic to an AuraDB Virtual Dedicated Cloud (VDC) or AuraDS Enterprise instance on Azure, without that traffic leaving the Azure backbone. The NCC path uses a Databricks Network Connectivity Configuration. The Private Link path uses a private endpoint in your VNet and serves classic Databricks, AKS, ADF, and jump VMs.

![Databricks Serverless reaches Neo4j Aura through an NCC private endpoint and the Aura Private Link Service](images/architecture-goal.svg)

The diagram shows the NCC path. [DNS ownership](#dns-ownership-who-answers-for-the-aura-hostname) covers the Private Link path.

## NCC: why this architecture

### Why NCC instead of a customer VNet

Azure Databricks Serverless compute runs in **Databricks-managed subscriptions**, not in your customer VNet. You cannot inject a customer-VNet-based private endpoint into serverless workloads the way you can with classic Databricks clusters. **Network Connectivity Configurations (NCC)** are the only supported method for giving serverless compute access to private resources.

An NCC is an **account-level, region-scoped** object that:

- Holds a set of private endpoint rules
- Attaches to one or more workspaces in the same region
- Manages DNS routing for the private endpoints from inside the serverless compute plane

### Why a REST API call for the private endpoint rule

The account console UI for private endpoint rules assumes an Azure-native resource. It asks for a Destination Azure resource ID and a sub-resource ID. These are the resource's ARM ID and its group ID. This works for first-party Azure services such as Storage, Key Vault, and SQL. Microsoft publishes a known list of sub-resource types for those services.

Neo4j Aura is a **third-party Private Link Service**. The PLS lives in Aura's own managed subscription, not yours. You never see an ARM ID for it. You see a **PLS alias** instead, which is the canonical way Azure exposes PLS to consumers across subscription boundaries.

Databricks' Network Connectivity API supports this case through the private endpoint rule payload. The `resource_id` field holds the PLS alias Aura gave you. The `domain_names` field holds the hostnames clients resolve. The `domain_names` field is the key piece. It tells NCC's managed DNS to route that hostname to the private endpoint instead of the public Aura IP.

The UI flow does not expose `domain_names`, which is why third-party PLS routing requires the API.

## Data plane vs control plane

Azure Databricks has two network planes that often get conflated:

| Plane | What flows through it | How it reaches resources |
|-------|----------------------|--------------------------|
| **Control plane** | Cluster orchestration, workspace UI, metadata | Managed by Databricks |
| **Serverless compute plane (data plane)** | Notebook/job/SQL compute, customer queries | Configurable via NCC: uses private endpoints when defined |

The NCC path configures the **serverless compute plane** to reach Aura privately. The control plane is managed by Databricks and is not part of this setup.

![The control plane is managed by Databricks. The serverless compute plane reaches Aura through the NCC private endpoint](images/architecture-planes.svg)

## NCC: DNS resolution flow

On the NCC path, this is what happens when a notebook executes `socket.gethostbyname("<aura-instance-id>.databases.neo4j.io")`:

1. The serverless compute resolver receives the query
2. NCC's managed DNS has an entry for `<aura-instance-id>.databases.neo4j.io` because you registered it as `domain_names` in the private endpoint rule
3. NCC returns the private IP of the private endpoint allocated for your NCC
4. The Neo4j driver opens a TLS connection to that private IP
5. The TLS handshake includes SNI `<aura-instance-id>.databases.neo4j.io`
6. Traffic flows over the Azure backbone to Aura's PLS, which forwards it to the Aura cluster. The cluster terminates TLS using its real certificate
7. Bolt protocol proceeds normally

![A notebook resolves the Aura hostname through NCC managed DNS, then opens TLS to the private endpoint](images/architecture-dns-flow.svg)

The TLS certificate is issued for the real Aura hostname, so no certificate-trust manipulation is needed on the client side.

## DNS ownership: who answers for the Aura hostname

Each path gives a different owner the job of mapping `<aura-instance-id>.databases.neo4j.io` to a private IP. Confusion over that owner is the most common reason an approved private endpoint still does not connect.

- **NCC stack.** Databricks manages DNS for you. NCC holds a managed private-DNS layer inside the serverless compute plane. The `domain_names` on the private endpoint rule populate it. You have no zone to create or link. See [the NCC stack](../infra/terraform/databricks-ncc/).

- **Private Endpoint stack.** You manage DNS. A private endpoint only allocates a private IP on a NIC. It does not make the hostname resolve. You need an Azure **private DNS zone** for `databases.neo4j.io` that holds an A record for the instance host. The zone also needs a virtual-network link so resolvers in the consuming VNet see it. The zone is authoritative for `databases.neo4j.io` in every linked VNet. Without the A record, lookups return NXDOMAIN and the connection fails. The hostname does not fall back to public DNS unless the VNet link has the NxDomainRedirect resolution policy, shown in the portal as **Enable fallback to internet**. Leave that setting off. With it on, a missing record silently sends traffic to the public IP. A VNet with no link to the zone also resolves the public IP. See [Why `databases.neo4j.io` lives in your private DNS zone](shared/private-dns-central.md#why-databasesneo4jio-lives-in-your-private-dns-zone).

![DNS ownership for the Aura hostname in the NCC stack and in the Private Endpoint stack, single-VNet and hub-and-spoke](images/architecture-dns-ownership.svg)

The Private Endpoint stack supports two topologies. They differ in who creates the zone:

| Topology | Who owns `databases.neo4j.io` | Terraform setting |
|---|---|---|
| **Self-managed (single VNet)** | This stack, in your resource group | `manage_private_dns = true` (default) |
| **Central / hub-and-spoke** | A shared hub VNet, often behind Azure DNS Private Resolver. Spokes use it through peering and zone links. | `manage_private_dns = false`: you add the A record and link in the hub |

Enterprises almost always centralize DNS. The hub owns every private zone. Convention or Azure Policy often forbids spokes from creating their own zones. If this stack created a second `databases.neo4j.io` zone inside a spoke, one of two things would happen. Resolution could go split-brain, with two zones holding one name and each VNet's zone link deciding the answer. Azure Policy could also deny the apply. Setting `manage_private_dns = false` defers to central DNS. The stack then provisions only the private endpoint. The platform team adds the A record for the private endpoint NIC IP and the routing-host records in the hub. See [Self-managed vs. central DNS](shared/private-dns-central.md#self-managed-vs-central-dns) and [Set up central hub DNS](shared/private-dns-central.md#set-up-central-hub-dns).

### Routing hosts on both paths

Aura advertises routing addresses such as `p-<aura-instance-id>-<suffix>.<orch>.neo4j.io` after the first connection. A client that follows the routing table needs these hosts to resolve to the same private IP as the instance host. The `ncc-notebooks/` set installs a Neo4j driver resolver that maps routing hosts back to the instance host, so it passes without extra records. The `pl-notebooks/` set uses plain DNS by default, so it fails until each routing host has a record. Your own apps and jobs, neo4j-cli, and Neo4j Desktop have no such resolver. In those clients, a missing record shows up as `Cannot resolve address p-...`. The fix depends on the path:

- **NCC:** Add the host to the rule's domain names. In Terraform, set `aura_extra_domain_names`. By hand, follow [Add a routing hostname later](setup-ncc-manual.md#add-a-routing-hostname-later).
- **Private Link:** Add one A record per host in a private DNS zone for its `<orch>.neo4j.io` domain. Routing hosts do not sit under `databases.neo4j.io`, so the instance zone never answers for them. Follow [Add routing-host records](setup-private-link-manual.md#add-routing-host-records) or the Terraform [Routing-host records](setup-private-link-terraform.md#routing-host-records).

## Supported resource categories in Databricks NCC

NCC private endpoints support many Azure services, such as Storage, Key Vault, SQL Database, and Event Hub. See [Supported resources](https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/manage-private-endpoint-rules#resources) for the current list.

Neo4j Aura falls under **Resources behind a Standard Load Balancer**. This category is the supported path for third-party Private Link providers, including Neo4j Aura.

## NCC: region considerations

- The NCC region **must** match the Databricks workspace region. This is a hard constraint.
- The Aura instance can be in a different Azure region from the workspace. This repo validated a workspace in `eastus2` reaching an Aura instance in `uksouth` through an NCC private endpoint rule. Notebook 01 resolved the instance host to a private address, connected over Bolt, and read data.
- Use the same region for the workspace and the Aura instance when you can. It avoids the latency and egress cost below.
- Cross-region traffic adds latency and a cross-region egress charge. See [NCC: cost model](#ncc-cost-model).

## NCC: failure modes and recovery

| Failure | Symptom | Recovery |
|---------|---------|----------|
| Subscription not registered in the Aura network access configuration | Creating the private endpoint rule fails with a Private Link service visibility error | Add the subscription ID as in [Aura console Step 3](shared/aura-console-steps.md#step-3-allow-list-the-consumer-subscription). Then re-create the rule as in [manual NCC Step 3](setup-ncc-manual.md#step-3-create-the-private-endpoint-rule). |
| `domain_names` omitted in NCC rule | DNS resolves to the public Aura IP. The connection works but is not private. | Update the rule with a PATCH request and `update_mask=domain_names`. Then restart serverless compute. |
| NCC attached but services not restarted | Existing sessions still use old routing | Restart all serverless compute, including SQL warehouses and running jobs |
| Rule expired after 14 days in `PENDING` | NCC rule disappears or is in `EXPIRED` state | Re-create the rule through the API. Then approve it again in the Aura console. |
| Public access disabled before validation | Clients cannot reach Aura at all | Re-enable public access for now. Debug DNS and the private endpoint, then disable public access again. |

## NCC: cost model

Azure Databricks bills for network egress when serverless workloads communicate with customer resources, including private-link traffic. Cross-region traffic adds an extra premium. Plan for this in your TCO model. High-throughput Delta-to-Neo4j syncs carry a real egress cost. See [Understand Databricks networking costs](https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/cost-management).
