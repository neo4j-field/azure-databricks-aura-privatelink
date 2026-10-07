# NCC stack

Terraform for a Databricks network connectivity configuration, a private endpoint rule into the Aura Private Link service, and the binding that attaches the NCC to your existing workspace.

Use this stack when the consumer is Databricks Serverless. For classic Databricks, AKS, ADF, or a jump VM, use the [Private Endpoint stack](../azure-private-endpoint/) instead.

[`scripts/automate.py`](../../../scripts/automate.py) drives this stack end to end. For prerequisites, usage, variables, and routing hosts, see [NCC Terraform setup](../../../docs/setup-ncc-terraform.md). The first apply fails until Aura allow-lists the Databricks-managed subscription, as described in [Aura console Step 3](../../../docs/shared/aura-console-steps.md#step-3-allow-list-the-consumer-subscription). To pick between stacks, see [infra/terraform/README.md](../README.md).
