# Production notes

Notes are grouped by path. **NCC** covers Databricks Serverless. **Private Link** covers a private endpoint in your own VNet. **Databricks consumers** covers items for any Databricks workload, on either path. **Both paths** covers items that apply to every consumer.

## Production best practices

### NCC (Databricks Serverless)

- **Monitor:** Watch the NCC private endpoint status, Databricks job runs, and Aura performance metrics.
- **Cost awareness:** Azure Databricks bills for networking costs when serverless workloads connect to customer resources. Plan for this in your TCO.

### Private Link (your VNet)

- **DNS links:** Link the private DNS zone to every consumer VNet. A VNet without its own link resolves the public IP.
- **Endpoint IP changes:** A recreated private endpoint can receive a new private IP. Update every A record when that happens, including the routing-host records.
- **Custom DNS servers:** A VNet that uses custom DNS servers must forward to Azure DNS at `168.63.129.16`. Without that forwarder, the private zone never answers.
- **Routing-host records:** Keep the routing-host records in step with the `p-<aura-instance-id>-<suffix>.<orch>.neo4j.io` hosts that clients report. Add a record whenever a client reports a new host.

### Databricks consumers

- **Run as Databricks Jobs:** Production workloads should run as Databricks Jobs, not interactive notebooks. Notebooks are for development and validation.
- **Store credentials in Azure Key Vault:** Expose the credentials through a Key-Vault-backed Databricks secret scope. Never put credentials in notebooks or repo files.

### Both paths

- **Idempotent writes:** Use Cypher `MERGE` instead of `CREATE` on `:Label {id: $id}` keys.
- **Batch writes:** Use `UNWIND` with batch sizes of 1k to 10k rows, depending on payload.
- **Retry transient failures:** The `neo4j` driver raises `TransientError`. Wrap writes with bounded retries, as in [ncc-notebooks/02_delta_to_neo4j.py](../../ncc-notebooks/02_delta_to_neo4j.py).

## Limitations and gotchas

### NCC (Databricks Serverless)

| Gotcha | Mitigation |
|--------|-----------|
| NCC private endpoint rules for third-party PLS need the REST API or CLI, because the console UI is Azure-native only | Use the [manual NCC guide](../setup-ncc-manual.md#step-3-create-the-private-endpoint-rule) or [scripts/create-private-endpoint-rule.sh](../../scripts/create-private-endpoint-rule.sh) |
| `domain_names` must be supplied or DNS will resolve to the public IP | Always include the Aura Private URI hostname in the API call |
| 14-day expiry on unapproved rules | Approve promptly in the Aura console |
| 10-minute NCC propagation after attach | Wait 10 minutes, then restart serverless services |
| An NCC does not restrict other outbound traffic from serverless compute | This repo does not configure serverless egress control. If you add a restricted network policy, allow the Aura hostnames and any package index your notebooks use, such as PyPI for `%pip install` |

### Both paths

| Gotcha | Mitigation |
|--------|-----------|
| Azure Private Link needs AuraDB Virtual Dedicated Cloud (VDC) or AuraDS Enterprise. Professional and Business Critical do not support it | Verify the tier before any work |
| Aura Private Link is region-scoped, not instance-scoped | Plan multi-region setups accordingly |
| Private Link does not close the public endpoint | Complete [Aura console Step 5](../shared/aura-console-steps.md#step-5-disable-public-access-on-aura) and run its outside-in check |

After public access is off, see [Developer desktop access](developer-desktop-access.md) and [Batch jobs in other VNets](batch-jobs-other-vnets.md) for the access that breaks.
