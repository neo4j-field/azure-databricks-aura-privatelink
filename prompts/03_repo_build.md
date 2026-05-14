# Repo Build Prompt

**Date:** 2026-05-13
**Tool:** Claude Code (Opus 4.7, 1M context)

## Prompt

> Create a GitHub repo under github.com/neo4j-field with all the validated details and steps, including a Databricks notebook for the connectivity validation.

## Scope

| Asset | Purpose |
|-------|---------|
| `README.md` | Top-level guide with the corrected step-by-step |
| `docs/architecture.md` | Architecture reasoning, data-plane vs control-plane, DNS flow |
| `docs/validation-report.md` | Concrete deltas between PDF and current official docs |
| `docs/troubleshooting.md` | Common failure modes and recovery |
| `notebooks/01_validate_connectivity.py` | DNS + TCP + Bolt sanity check |
| `notebooks/02_delta_to_neo4j.py` | Production-pattern round trip with retry + idempotent MERGE |
| `infra/terraform/` | NCC + PE rule provisioning |
| `scripts/` | Secret scope creation, REST API helper, standalone DNS check |
| `prompts/` | This folder — reproducibility per CLAUDE.md convention |

## Design choices

1. **Repo name**: `azure-databricks-aura-privatelink` — consumer-then-provider order, kebab-case.
2. **License**: Apache 2.0 — Neo4j default for open source.
3. **Notebooks as `.py` not `.ipynb`**: Databricks `# COMMAND ----------` cell-separator format renders correctly in the workspace and diffs cleanly in git, unlike JSON `.ipynb`.
4. **Terraform uses account-level provider**: NCC and PE rules are account-scoped, not workspace-scoped.
5. **REST API helper script is shell, not Python**: keeps the surface minimal and matches how customers usually run this kind of one-shot call.
6. **Secrets**: Databricks-backed scope is shown as the default; Azure Key Vault-backed scope is documented as the production recommendation.
7. **Retry pattern**: `tenacity` with bounded exponential backoff on `ServiceUnavailable`/`TransientError` only — not blanket retries.
