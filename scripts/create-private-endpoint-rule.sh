#!/usr/bin/env bash
#
# create-private-endpoint-rule.sh
#
# Create an NCC private endpoint rule for a third-party Private Link Service
# (Neo4j Aura, App Gateway v2, etc.) using the Databricks REST API.
#
# The account console UI does NOT support `domain_names`, which is required for
# DNS routing of third-party PLS hostnames from serverless compute. This is the
# canonical path documented in Microsoft Learn under "Configure Private Link to
# Azure App Gateway v2" — the same shape works for any third-party PLS.
#
# Usage:
#   export DATABRICKS_HOST=https://accounts.azuredatabricks.net
#   export DATABRICKS_ACCOUNT_ID=...
#   export DATABRICKS_TOKEN=...
#   export NCC_ID=...
#   export AURA_PLS_ALIAS="pls-aura-xxx.guid.region.azure.privatelinkservice"
#   export AURA_PRIVATE_HOSTNAME="d48d6199.databases.neo4j.io"
#   # Optional, comma-separated Neo4j routing hosts returned by the driver:
#   export AURA_EXTRA_DOMAIN_NAMES="p-d48d6199-....neo4j.io"
#   ./scripts/create-private-endpoint-rule.sh

set -euo pipefail

: "${DATABRICKS_ACCOUNT_ID:?Need DATABRICKS_ACCOUNT_ID}"
: "${DATABRICKS_TOKEN:?Need DATABRICKS_TOKEN}"
: "${NCC_ID:?Need NCC_ID}"
: "${AURA_PLS_ALIAS:?Need AURA_PLS_ALIAS (PLS alias from Aura console)}"
: "${AURA_PRIVATE_HOSTNAME:?Need AURA_PRIVATE_HOSTNAME (Aura Private URI host)}"

API_HOST="https://accounts.azuredatabricks.net"

echo "Submitting private endpoint rule..."
echo "  NCC          : ${NCC_ID}"
echo "  Resource ID  : ${AURA_PLS_ALIAS}"
echo "  Domain names : ${AURA_PRIVATE_HOSTNAME}"
if [[ -n "${AURA_EXTRA_DOMAIN_NAMES:-}" ]]; then
  echo "  Extra domains: ${AURA_EXTRA_DOMAIN_NAMES}"
fi
echo

PAYLOAD=$(python3 - <<'PY'
import json
import os

domains = [os.environ["AURA_PRIVATE_HOSTNAME"]]
extra = os.environ.get("AURA_EXTRA_DOMAIN_NAMES", "")
domains.extend(d.strip() for d in extra.split(",") if d.strip())
domains = list(dict.fromkeys(domains))

print(json.dumps({
    "resource_id": os.environ["AURA_PLS_ALIAS"],
    "domain_names": domains,
}))
PY
)

RESPONSE=$(curl --silent --show-error --fail \
  --request POST \
  "${API_HOST}/api/2.0/accounts/${DATABRICKS_ACCOUNT_ID}/network-connectivity-configs/${NCC_ID}/private-endpoint-rules" \
  --header "Authorization: Bearer ${DATABRICKS_TOKEN}" \
  --header "Content-Type: application/json" \
  --data "${PAYLOAD}"
)

echo "Response:"
echo "${RESPONSE}" | python3 -m json.tool

RULE_ID=$(echo "${RESPONSE}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('rule_id',''))")

echo
echo "Rule created with ID: ${RULE_ID}"
echo
echo "Next steps:"
echo "  1. Open the Aura console -> Security -> Network Access"
echo "  2. Approve the incoming private endpoint request"
echo "  3. Wait until the rule status reads ESTABLISHED in the Databricks NCC view"
echo "  4. Restart any running serverless compute, then run the validation notebook"
