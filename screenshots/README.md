# Screenshots

Console screenshots that go with the steps in the top-level README. Captured against the reference deployment: Aura instance `b7253d3b` (UK South), Databricks workspace `dbxuk-svrless-drose`.

| File | Step | What it shows |
|------|------|---------------|
| `01-aura-network-access-overview.png` | Step 2 | Aura *Network Access* page for the org. Confirms `uksouth` region has `Private traffic: Enabled` and `Public traffic: Disabled`. |
| `02-aura-network-access-config-subscriptions.png` | Step 2 | Edit wizard *Step 1 of 4* — the Target Azure Subscription IDs registered for inbound PrivateLink. The Databricks subscription `70bb2c8c-6c76-47c0-b6c9-82f0204f30ac` must appear here. |
| `03-aura-pls-service-name-and-instructions.png` | Step 2 | Edit wizard *Step 2 of 4* — the PLS service name (`production-orch-0477-service.<guid>.uksouth.azure.privatelinkservice`) and the Aura-provided portal instructions for creating a customer-managed PE. The Terraform stacks in `infra/terraform/` automate both paths. |

## Add your own

For each new deployment, capture additionally:

| Suggested file | What to capture |
|----------------|-----------------|
| `04-databricks-account-console-ncc.png` | NCC list page in the Databricks account console |
| `05-databricks-ncc-detail.png` | NCC detail with the PE rule (PENDING / ESTABLISHED) |
| `06-aura-approve-endpoint.png` | Aura approval screen for the incoming endpoint request |
| `07-databricks-rule-established.png` | NCC view showing rule status ESTABLISHED |
| `08-validation-notebook-success.png` | `notebooks/03_dbxuk_svrless_drose_smoke_test.py` output |

PNG only. Keep file sizes under ~500KB each. Redact any account IDs or subscription IDs you do not want in a public repo.
