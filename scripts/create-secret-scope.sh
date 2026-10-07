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
#   [ -f .env ] || cp env.sample .env   # then fill in the values
#   ./scripts/create-secret-scope.sh
#
# Values are read from the repo-root .env (override the path with ENV_FILE).
# A real environment variable, if exported, takes precedence over the .env value.
#
# Required: NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD
# Optional: NEO4J_DATABASE (defaults to neo4j)
#           ROUTING_HOST (stored as routing_host, for the debug DNS cell in notebook 01)
# Workspace: WORKSPACE_PROFILE (a Databricks CLI profile), or DATABRICKS_HOST
#            and DATABRICKS_TOKEN

set -euo pipefail

ENV_FILE="${ENV_FILE:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.env}"

# Read KEY=VALUE lines without executing them. Existing exported variables win.
load_env() {
  local line key value
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]] || continue
    key="${BASH_REMATCH[1]}"
    value="${BASH_REMATCH[2]}"
    if [[ "$value" =~ ^\"([^\"]*)\" ]]; then
      value="${BASH_REMATCH[1]}"
    elif [[ "$value" =~ ^\'([^\']*)\' ]]; then
      value="${BASH_REMATCH[1]}"
    else
      value="${value%%[[:space:]]#*}"
      value="${value%"${value##*[![:space:]]}"}"
    fi
    if [[ -z "${!key:-}" ]]; then
      export "$key=$value"
    fi
  done < "$1"
}

if [[ -f "$ENV_FILE" ]]; then
  load_env "$ENV_FILE"
fi

: "${NEO4J_URI:?Need NEO4J_URI (set it in .env, see env.sample)}"
: "${NEO4J_USERNAME:?Need NEO4J_USERNAME (set it in .env, see env.sample)}"
: "${NEO4J_PASSWORD:?Need NEO4J_PASSWORD (set it in .env, see env.sample)}"
: "${NEO4J_DATABASE:=neo4j}"

SCOPE="neo4j"

if ! command -v databricks >/dev/null 2>&1; then
  echo "databricks CLI not found. Install: https://docs.databricks.com/dev-tools/cli/install.html" >&2
  exit 1
fi

if [[ -n "${WORKSPACE_PROFILE:-}" ]]; then
  DBX=(databricks --profile "${WORKSPACE_PROFILE}")
else
  : "${DATABRICKS_HOST:?Need WORKSPACE_PROFILE, or DATABRICKS_HOST and DATABRICKS_TOKEN}"
  : "${DATABRICKS_TOKEN:?Need WORKSPACE_PROFILE, or DATABRICKS_HOST and DATABRICKS_TOKEN}"
  DBX=(databricks)
fi

echo "Creating scope: ${SCOPE}"
"${DBX[@]}" secrets create-scope "${SCOPE}" 2>/dev/null || echo "Scope ${SCOPE} already exists, continuing."

put() {
  local key="$1"; local value="$2"
  echo "Setting secret: ${SCOPE}/${key}"
  "${DBX[@]}" secrets put-secret "${SCOPE}" "${key}" --string-value "${value}"
}

put "uri"      "${NEO4J_URI}"
put "username" "${NEO4J_USERNAME}"
put "password" "${NEO4J_PASSWORD}"
put "database" "${NEO4J_DATABASE}"
# Databricks rejects empty secret values, so skip this key when it is unset.
if [[ -n "${ROUTING_HOST:-}" ]]; then
  put "routing_host" "${ROUTING_HOST}"
fi

echo
echo "Done. Verify with:"
echo "  ${DBX[*]} secrets list-secrets ${SCOPE}"
