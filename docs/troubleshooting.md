# Troubleshooting

## DNS resolves to public IP

**Symptom:** `socket.gethostbyname("<dbid>.databases.neo4j.io")` returns a public IP instead of a private one.

**Cause:** The `domain_names` field was not set on the NCC private endpoint rule.

**Fix:** PATCH the rule via API to add `domain_names`:

```bash
curl --location --request PATCH \
  "https://accounts.azuredatabricks.net/api/2.0/accounts/${ACCOUNT_ID}/network-connectivity-configs/${NCC_ID}/private-endpoint-rules/${RULE_ID}?update_mask=domain_names" \
  --header "Authorization: Bearer ${DATABRICKS_TOKEN}" \
  --header "Content-Type: application/json" \
  --data '{ "domain_names": ["<dbid>.databases.neo4j.io"] }'
```

Wait 5 minutes and restart serverless compute resources.

## NCC rule stuck in PENDING

**Symptom:** Rule status remains `PENDING` indefinitely.

**Possible causes:**

1. **Subscription not registered in Aura**: The target Azure subscription ID was not added to the Aura Network Access configuration.
2. **Approval pending in Aura console**: The request is sitting unapproved in the Aura UI.
3. **Wrong region**: NCC region does not match where the Aura PLS is exposed.

**Diagnosis order:**

1. Check Aura → Security → Network Access for an incoming request. Approve it if present.
2. If no request appears, verify the subscription ID in the Aura Network Access config matches the subscription holding the Databricks workspace.
3. Verify regions align.

**Note:** Rules in `PENDING` for 14 days **expire** automatically. If you suspect this happened, recreate the rule.

## Bolt connection times out

**Symptom:** `neo4j` driver hangs or returns `ServiceUnavailable`.

**Diagnosis:**

1. **Confirm DNS** resolves to private IP (see above).
2. **Test TCP reach**:
   ```python
   import socket
   s = socket.create_connection(("d48d6199.databases.neo4j.io", 7687), timeout=10)
   s.close()
   ```
3. **Verify Bolt port**: Aura uses `7687` for Bolt+TLS. Confirm the URI scheme is `neo4j+s://` not `neo4j://`.
4. **Check Aura instance state** in the console — must be `Running`.
5. **Public access disabled too soon?** If you disabled public access before confirming private path works, temporarily re-enable to isolate which layer is broken.

## TLS handshake fails

**Symptom:** `SSL: CERTIFICATE_VERIFY_FAILED` or similar.

**Cause:** SNI/hostname mismatch — usually because the client connected to a different hostname than the certificate covers.

**Fix:** Confirm you are using the **Private URI** from the Aura console, not a custom hostname or IP. The TLS cert is issued for the Aura hostname.

## "Resource type not supported" when creating rule via UI

**Symptom:** Databricks UI rejects the resource ID format.

**Cause:** You're trying to add a third-party PLS via the UI. The UI only supports native Azure resources.

**Fix:** Use the REST API path documented in [scripts/create-private-endpoint-rule.sh](../scripts/create-private-endpoint-rule.sh).

## After disabling public access, jobs fail

**Symptom:** Disabled public access in Aura, now scheduled jobs fail to connect.

**Diagnosis:**

1. **Did you restart serverless compute** after attaching NCC and adding the rule? Long-running jobs may have cached the old (public) DNS resolution.
2. **Are there any non-NCC connections** (e.g., from classic clusters, external services, dev laptops) still trying to hit the public endpoint?
3. **Aura propagation**: the public-disable toggle is not instant. Wait and monitor the Aura status.

## Secret scope not found

**Symptom:** `dbutils.secrets.get` raises `ResourceDoesNotExist`.

**Fix:** Create the scope:

```bash
databricks secrets create-scope neo4j
databricks secrets put-secret neo4j username --string-value "neo4j"
databricks secrets put-secret neo4j password --string-value "<password>"
databricks secrets put-secret neo4j uri --string-value "neo4j+s://<dbid>.databases.neo4j.io"
```

See [scripts/create-secret-scope.sh](../scripts/create-secret-scope.sh) for the full setup.
