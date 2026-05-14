#!/usr/bin/env bash
#
# create-secret-scope.sh
#
# Create a Databricks-backed secret scope called `neo4j` and populate it
# with the four credentials needed by the validation notebooks.
#
# For production, prefer an Azure Key Vault-backed scope:
#   databricks secrets create-scope neo4j --scope-backend-type AZURE_KEYVAULT \
#     --azure-keyvault <kv-resource-id> --azure-keyvault-dns-name <kv-dns>
#
# Usage:
#   export DATABRICKS_HOST=https://adb-xxx.x.azuredatabricks.net
#   export DATABRICKS_TOKEN=...
#   export NEO4J_URI="neo4j+s://d48d6199.databases.neo4j.io"
#   export NEO4J_USERNAME="neo4j"
#   export NEO4J_PASSWORD="..."
#   export NEO4J_DATABASE="neo4j"
#   ./scripts/create-secret-scope.sh

set -euo pipefail

: "${DATABRICKS_HOST:?Need DATABRICKS_HOST}"
: "${DATABRICKS_TOKEN:?Need DATABRICKS_TOKEN}"
: "${NEO4J_URI:?Need NEO4J_URI}"
: "${NEO4J_USERNAME:?Need NEO4J_USERNAME}"
: "${NEO4J_PASSWORD:?Need NEO4J_PASSWORD}"
: "${NEO4J_DATABASE:=neo4j}"

SCOPE="neo4j"

if ! command -v databricks >/dev/null 2>&1; then
  echo "databricks CLI not found. Install: https://docs.databricks.com/dev-tools/cli/install.html" >&2
  exit 1
fi

echo "Creating scope: ${SCOPE}"
databricks secrets create-scope "${SCOPE}" 2>/dev/null || echo "Scope ${SCOPE} already exists, continuing."

put() {
  local key="$1"; local value="$2"
  echo "Setting secret: ${SCOPE}/${key}"
  databricks secrets put-secret "${SCOPE}" "${key}" --string-value "${value}"
}

put "uri"      "${NEO4J_URI}"
put "username" "${NEO4J_USERNAME}"
put "password" "${NEO4J_PASSWORD}"
put "database" "${NEO4J_DATABASE}"

echo
echo "Done. Verify with:"
echo "  databricks secrets list-secrets ${SCOPE}"
