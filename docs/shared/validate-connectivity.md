# Validate connectivity

Run these checks after the private endpoint reads `ESTABLISHED` or `Approved`. Every setup guide ends here.

The notebooks run in Databricks. If your consumer is not Databricks, such as a jump VM or AKS, use `uv run scripts/private_link.py verify --bolt` from [Private Link manual setup](../setup-private-link-manual.md) instead.

## Create the secret scope

The notebooks read Neo4j credentials from a secret scope named `neo4j` in the workspace. Copy the sample file and fill it in. The URI host must match the Private URI host you gave the private endpoint rule:

```bash
cp env.sample .env
```

`.env` is gitignored. It holds `WORKSPACE_PROFILE` and the `NEO4J_*` values. Then run the script from the repository root. It creates the scope and stores the `uri`, `username`, `password`, and `database` keys:

```bash
./scripts/create-secret-scope.sh
```

## Upload the notebooks

Run this from the repository root. It copies the notebooks into a `neo4j-privatelink` folder in your user area of the workspace:

```bash
: "${WORKSPACE_PROFILE:?WORKSPACE_PROFILE is not set}"
export NOTEBOOK_DIR="/Users/$(databricks --profile "$WORKSPACE_PROFILE" current-user me -o json | jq -r .userName)/neo4j-privatelink"
databricks --profile "$WORKSPACE_PROFILE" workspace mkdirs "$NOTEBOOK_DIR"
for f in notebooks/0*.py; do
  databricks --profile "$WORKSPACE_PROFILE" workspace import "$NOTEBOOK_DIR/$(basename "$f" .py)" \
    --file "$f" --format SOURCE --language PYTHON --overwrite
done
```

Open the `neo4j-privatelink` folder in the workspace and attach each notebook to serverless compute before you run it.

## Run the validation notebook

Run [notebooks/01_validate_connectivity.py](../../notebooks/01_validate_connectivity.py) from the uploaded folder. DNS resolution for the Aura Private URI is handled by Databricks NCC because you supplied `domain_names` on the private endpoint rule, and the notebook checks it for you. It resolves the Aura host, asserts the address is private, and then runs the Bolt connectivity check. No separate DNS check is needed.

For a fuller test that writes and reads back sample data, use [notebooks/04_smoke_test.py](../../notebooks/04_smoke_test.py). It repeats the same DNS assertion.

## Debug DNS (optional)

Run this from a serverless notebook only if a notebook fails and you need to see which hostname is not resolving privately:

```python
import socket
for host in [
    "<aura-id>.databases.neo4j.io",                # replace <aura-id> with your Aura instance id
    "p-<aura-id>-<suffix>.<orch>.neo4j.io",        # routing hostname, if the Bolt check reports one
]:
    print(host, socket.gethostbyname(host))        # should return a 10.x or similar private IP
```

Notebook 01 maps `p-*.neo4j.io` routing hostnames to the private Aura host, so it does not report an unresolved one as a DNS failure. Use this snippet to check them directly.

If a hostname resolves to a public IP or not at all, see [Troubleshooting](../operations/troubleshooting.md).

## Next

Close the public endpoint with [Aura console Step 5](aura-console-steps.md#step-5-disable-public-access-on-aura), then re-run the validation notebook.
