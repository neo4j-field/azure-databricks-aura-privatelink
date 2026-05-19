# Terraform stacks

Two independent Terraform stacks, picked by **which compute is reaching Aura**:

| Stack | Use when your consumer is | What it creates |
|---|---|---|
| [`databricks-ncc/`](databricks-ncc/) | **Azure Databricks Serverless** | Databricks NCC + private endpoint rule + workspace binding. The PE itself lives in the Databricks-managed subscription; nothing is created in your subscription. |
| [`azure-private-endpoint/`](azure-private-endpoint/) | Classic Databricks (VNet-injected), AKS, ADF self-hosted IR, jump VMs, Functions on VNet integration | A customer-managed `azurerm_private_endpoint` in your VNet + a `databases.neo4j.io` private DNS zone + VNet link + A record. |

The two stacks are independent root modules. You can apply only one, only the other, or both side by side if different consumer classes in the same subscription need to reach Aura over both surfaces.

## Decision flow

```
Is the consumer Databricks Serverless?
├── Yes  →  databricks-ncc/   (only stack you need)
└── No
    ├── Customer-VNet workload (AKS / ADF / VM / classic Databricks)?
    │       →  azure-private-endpoint/
    └── Both consumer classes exist?
            →  apply both
```

## Important gotcha for the `databricks-ncc/` stack with third-party PLS

When the NCC creates the PE, the request originates from the **Databricks-managed subscription** for that region — not yours. Aura's PLS has a visibility allow-list ("Target Azure Subscription IDs" in the Aura Network Access wizard) and will reject PE creation from a sub that isn't on it. Symptoms:

```
ThirdPartyPrivateLinkServiceProvidedDuringPrivateEndpointCreationDoesNotExistOrIsNotVisible
```

The Databricks-managed sub appears in the error path of the failed `terraform apply` (`/subscriptions/<guid>/resourceGroups/prod-<region>-snp-...`). Take that sub ID, add it to the Aura Network Access config for the corresponding Aura region (alongside your own sub), wait ~1 minute, and re-apply.

This is **not** documented in Neo4j or Microsoft public docs; it's a known operational gotcha for NCC + third-party PLS. See [`databricks-ncc/README.md`](databricks-ncc/README.md#third-party-pls-visibility) for the troubleshooting recipe.
