# Troubleshooting

Entries are grouped by path. **NCC** covers Databricks Serverless. **Private Link** covers a private endpoint in your own VNet. **Both paths** covers Bolt, TLS, public access, and secret scope problems that can happen on either path.

## NCC (Databricks Serverless)

### DNS resolves to public IP

**Symptom:** `socket.gethostbyname("<aura-instance-id>.databases.neo4j.io")` returns a public IP instead of a private one.

**Cause:** The `domain_names` list on the NCC private endpoint rule does not contain the Aura instance host.

**Fix:** Add the host to the rule's `domain_names` list. With the manual setup, run the CLI update call in [Add a routing hostname later](../setup-ncc-manual.md#add-a-routing-hostname-later). That call replaces the whole list, so include the instance host and every routing host the rule already has. With Terraform, check that `aura_private_hostname` matches the Private URI host from Aura, then apply again. Wait 10 minutes, then restart serverless compute.

### Neo4j driver cannot resolve `p-*.neo4j.io`

**Symptom:** DNS and TCP checks pass for `<aura-instance-id>.databases.neo4j.io`, but your own app, job, neo4j-cli, or Neo4j Desktop fails with this error:

```
ValueError: Cannot resolve address p-...neo4j.io:7687
```

The notebooks in this repo never show this error. Each of the four notebooks installs a driver resolver that maps routing hosts back to the instance host, so they pass without extra NCC domain names.

**Cause:** Aura VDC returns extra Bolt routing hosts in the Neo4j routing table. They look like `p-<aura-instance-id>-<suffix>.<orch>.neo4j.io`. The NCC rule routes only the hosts in its `domain_names` list. A client without a resolver tries to resolve the routing host directly and fails.

**Fix:** Add the routing host to the NCC rule. With Terraform, add the full host name to `aura_extra_domain_names` and apply again. With the manual setup, run the CLI update call in [Add a routing hostname later](../setup-ncc-manual.md#add-a-routing-hostname-later). Wait 10 minutes, then restart serverless compute. Check that both hosts resolve privately from a serverless notebook:

```python
import socket, ipaddress

for host in [
    "<aura-instance-id>.databases.neo4j.io",
    "p-<aura-instance-id>-<suffix>.<orch>.neo4j.io",
]:
    ip = socket.gethostbyname(host)
    print(host, ip, ipaddress.ip_address(ip).is_private)
```

The alternative is to give your app the same resolver the notebooks use. It keeps the `neo4j+s://` URI and maps any host that matches `^p-<aura-instance-id>-.*\.neo4j\.io$` back to the instance host. This version follows [ncc-notebooks/01_validate_connectivity.py](../../ncc-notebooks/01_validate_connectivity.py). The Private Link notebook [pl-notebooks/01_validate_connectivity.py](../../pl-notebooks/01_validate_connectivity.py) has the same function behind a `use_resolver` widget that is off by default:

```python
import re
from neo4j import GraphDatabase

aura_host = "<aura-instance-id>.databases.neo4j.io"
dbid = aura_host.split(".")[0]
routing_host_pattern = re.compile(rf"^p-{re.escape(dbid)}-.*\.neo4j\.io$")

def aura_private_resolver(address):
    mapped_host = aura_host if routing_host_pattern.match(address.host) else address.host
    return [(mapped_host, address.port)]

driver = GraphDatabase.driver(
    f"neo4j+s://{aura_host}",
    auth=(NEO4J_USER, NEO4J_PASSWORD),
    resolver=aura_private_resolver,
)
```

### NCC rule stuck in PENDING

**Symptom:** The rule status remains `PENDING` indefinitely.

**Possible causes:** One of three things usually holds the rule in `PENDING`.

1. **Subscription not registered in Aura:** The Databricks-managed Azure subscription ID is missing from the Aura network access configuration.
2. **Approval pending in Aura console:** The request is sitting unapproved in the Aura UI.
3. **Wrong region:** The NCC region does not match the region where the Aura PLS is exposed.

**Diagnosis order:** Work through these checks in order.

1. Check the Aura private endpoints page for an incoming request, as described in [Aura console Step 4](../shared/aura-console-steps.md#step-4-approve-the-private-endpoint-in-the-aura-console). Approve it if present.
2. If no request appears, verify that the subscription ID in the Aura network access configuration matches the Databricks-managed subscription shown in the Terraform or Azure error path. Databricks may use more than one managed subscription per region. Repeat this check for each new `/subscriptions/<guid>/resourceGroups/prod-<region>-snp-...` value that retries expose.
3. Verify that the regions align.

**Note:** A rule that stays in `PENDING` for 14 days **expires** automatically. If you suspect this happened, recreate the rule.

### "Resource type not supported" when creating rule via UI

**Symptom:** The Databricks UI rejects the resource ID format.

**Cause:** You are trying to add a third-party PLS through the UI. The UI only supports native Azure resources.

**Fix:** Use the REST API path documented in [scripts/create-private-endpoint-rule.sh](../../scripts/create-private-endpoint-rule.sh).

## Private Link (your VNet)

### Private Link: DNS resolves to public IP

**Symptom:** On the Private Link path, `nslookup <aura-instance-id>.databases.neo4j.io` from a VM or cluster in your VNet returns a public IP instead of the private endpoint IP.

**Cause:** The VNet is not linked to the `databases.neo4j.io` private DNS zone, or the zone has no A record for the instance label.

**Fix:** Check the link and the record:

```bash
az network private-dns link vnet list --resource-group "$RG" --zone-name databases.neo4j.io -o table
az network private-dns record-set a show --resource-group "$RG" --zone-name databases.neo4j.io --name "$AURA_INSTANCE_ID"
```

Add a missing link or record as in [Private Link manual setup](../setup-private-link-manual.md#step-5-create-the-private-dns-zone-and-link-it). With central hub DNS, check the hub zone and the resolver forwarding rule instead, as in [Set up central hub DNS](../shared/private-dns-central.md#set-up-central-hub-dns). A check run from a laptop always returns the public IP, so run it from inside the linked VNet.

### Private Link: routing host does not resolve

**Symptom:** On the Private Link path, the instance host resolves privately, but your own app, job, neo4j-cli, or Neo4j Desktop fails with `Cannot resolve address p-...neo4j.io:7687`.

The notebooks in this repo never show this error. They install a driver resolver that maps routing hosts back to the instance host, so they pass without routing-host records. Your own apps can copy that resolver from [Neo4j driver cannot resolve `p-*.neo4j.io`](#neo4j-driver-cannot-resolve-p-neo4jio).

**Cause:** Routing hosts sit under `<orch>.neo4j.io`, not under `databases.neo4j.io`, so the instance zone never answers for them. Each one needs an A record in its own `<orch>.neo4j.io` zone.

**Fix:** Add the routing-host records as in [Add routing-host records](../setup-private-link-manual.md#add-routing-host-records) for the manual setup, or [Routing-host records](../setup-private-link-terraform.md#routing-host-records) for Terraform. If you used the script, run `uv run scripts/private_link.py add-routing-host <host>`. Once the `<orch>.neo4j.io` zone is linked, every host in that domain needs a record, or it stops resolving from linked VNets.

### Private Link: endpoint request does not appear in Aura

**Symptom:** `az network private-endpoint create` fails with `...DoesNotExistOrIsNotVisible`, or the endpoint stays `Pending` and no request shows up on the Aura approval screen.

**Cause:** The subscription that holds the private endpoint is not in **Target Azure Subscription IDs**.

**Fix:** Add your own subscription ID, as in [Aura console Step 3](../shared/aura-console-steps.md#step-3-allow-list-the-consumer-subscription). Print it with `az account show --query id -o tsv`. Wait about a minute, then run the create again. If an endpoint already exists without a request in Aura, recreate it as in [Recreate a rejected or disconnected endpoint](../setup-private-link-manual.md#recreate-a-rejected-or-disconnected-endpoint).

## Both paths

### After disabling public access, jobs fail

**Symptom:** Scheduled jobs fail to connect after you disabled public access in Aura.

**Diagnosis:** Check these three causes in order.

1. **Stale serverless compute:** On the NCC path, serverless compute that started before the NCC attach and the rule can still hold the old public DNS answer. Restart serverless compute after you attach the NCC and add the rule.
2. **Clients outside the private path:** Classic clusters, external services, or developer laptops may still be trying to reach the public endpoint. See [Batch jobs in other VNets](batch-jobs-other-vnets.md) and [Developer desktop access](developer-desktop-access.md) for the access they need.
3. **Aura propagation:** The public-disable toggle takes effect after a delay. Wait, and watch the instance status in the Aura console.

### Bolt connection times out

**Symptom:** The `neo4j` driver hangs or returns `ServiceUnavailable`.

**Diagnosis:** Check each layer in order.

1. **Confirm DNS:** The Aura host must resolve to a private IP. On the NCC path, see [DNS resolves to public IP](#dns-resolves-to-public-ip). On the Private Link path, see [Private Link: DNS resolves to public IP](#private-link-dns-resolves-to-public-ip).
2. **Test TCP reach:** Open a socket to port 7687 from the client:
   ```python
   import socket
   s = socket.create_connection(("<aura-instance-id>.databases.neo4j.io", 7687), timeout=10)
   s.close()
   ```
3. **Verify the Bolt port:** Aura uses `7687` for Bolt+TLS. Confirm that the URI scheme is `neo4j+s://` and not `neo4j://`.
4. **Check the Aura instance state:** The instance must show `Running` in the console.
5. **Check whether public access was disabled too soon:** If you disabled public access before the private path worked, re-enable it for a while to isolate which layer is broken.

### TLS handshake fails

**Symptom:** The driver reports `SSL: CERTIFICATE_VERIFY_FAILED` or a similar error.

**Cause:** This is an SNI or hostname mismatch. The client usually connected to a different hostname than the certificate covers.

**Fix:** Confirm you are using the **Private URI** from the Aura console, not a custom hostname or IP. The TLS certificate is issued for the Aura hostname.

### Secret scope not found

**Symptom:** `dbutils.secrets.get` raises `ResourceDoesNotExist`.

**Fix:** Fill in `.env` from `env.sample`, then run the script from the repository root. It creates the `neo4j` scope and stores the `uri`, `username`, `password`, and `database` keys:

```bash
cp env.sample .env
./scripts/create-secret-scope.sh
```

To create the scope by hand instead, run the same calls the script makes:

```bash
databricks --profile "$WORKSPACE_PROFILE" secrets create-scope neo4j
databricks --profile "$WORKSPACE_PROFILE" secrets put-secret neo4j uri --string-value "neo4j+s://<aura-instance-id>.databases.neo4j.io"
databricks --profile "$WORKSPACE_PROFILE" secrets put-secret neo4j username --string-value "neo4j"
databricks --profile "$WORKSPACE_PROFILE" secrets put-secret neo4j password --string-value "<password>"
databricks --profile "$WORKSPACE_PROFILE" secrets put-secret neo4j database --string-value "neo4j"
```

Check the result with `databricks --profile "$WORKSPACE_PROFILE" secrets list-secrets neo4j`.
