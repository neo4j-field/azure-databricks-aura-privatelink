# Production notes

## Production best practices

- **Run as Databricks Jobs**, not interactive notebooks. Notebooks are for development and validation.
- **Store credentials in Azure Key Vault**, exposed via a Key-Vault-backed Databricks secret scope. Never put credentials in notebooks or repo files.
- **Idempotent writes**: use Cypher `MERGE` (not `CREATE`) on `:Label {id: $id}` keys.
- **Batch writes**: use `UNWIND` with batch sizes of 1k-10k rows depending on payload.
- **Retry transient failures**: the `neo4j` driver raises `TransientError`. Wrap writes with bounded retries, as in [notebooks/02_delta_to_neo4j.py](../../notebooks/02_delta_to_neo4j.py).
- **Monitor**: NCC private endpoint status, Databricks job runs, Aura performance metrics.
- **Cost awareness**: Azure Databricks bills for networking costs when serverless workloads connect to customer resources. Plan for this in your TCO.

## Limitations and gotchas

| Gotcha | Mitigation |
|--------|-----------|
| Aura tier must be VDC. Professional and Business Critical do not support Azure Private Link | Verify tier before any work |
| NCC private endpoint rules for third-party PLS need the REST API or CLI, because the console UI is Azure-native only | Use the [manual NCC guide](../setup-ncc-manual.md#step-3-create-the-private-endpoint-rule) or [scripts/create-private-endpoint-rule.sh](../../scripts/create-private-endpoint-rule.sh) |
| `domain_names` must be supplied or DNS will resolve to the public IP | Always include the Aura Private URI hostname in the API call |
| 14-day expiry on unapproved rules | Approve promptly in the Aura console |
| 10-minute NCC propagation after attach | Wait, then restart serverless services |
| Aura Private Link is region-scoped, not instance-scoped | Plan multi-region setups accordingly |
| Private Link does not close the public endpoint | Complete [Aura console Step 5](../shared/aura-console-steps.md#step-5-disable-public-access-on-aura) and run its outside-in check |
| An NCC does not restrict other outbound traffic from serverless compute | This repo does not configure serverless egress control. If you add a restricted network policy, allow the Aura hostnames and any package index your notebooks use, such as PyPI for `%pip install` |

After public access is off, see [Developer desktop access](developer-desktop-access.md) and [Batch jobs in other VNets](batch-jobs-other-vnets.md) for the access that breaks.
