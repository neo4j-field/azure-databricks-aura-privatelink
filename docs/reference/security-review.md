# Security Review

Review date: 2026-10-06

File paths and line numbers in this review refer to the repo layout at the time of the review. Docs have since moved. For example, `docs/manual-ncc-setup.md` is now `docs/setup-ncc-manual.md`, and README Steps 1, 2, 7, and 9 are now in `docs/shared/aura-console-steps.md`.

## Overview

This repo connects Azure Databricks serverless compute to Neo4j Aura over Azure Private Link. It has three Terraform stacks, a set of scripts, four notebooks, and a docs folder.

The design is sound. Private endpoint connections need manual approval. The VM has no public IP. The jump box NSG denies all other inbound traffic. The git history holds no secrets.

The main gaps sit around the design, not inside the Terraform syntax. No script verifies that the Aura public endpoint is closed. Serverless egress is open. Every notebook uses one shared admin database account. Fix these three first.

No Critical findings exist. The review found 3 High, 10 Medium, and 7 Low findings.

## Scope and method

- **Reviewed:** The review covered all Terraform in `infra/terraform/`, everything in `scripts/` and `notebooks/`, all docs, `.gitignore`, and the full git history.
- **Method:** The review was manual. No scanner such as tfsec, checkov, or trivy is installed, so none ran.
- **Secrets handling:** The review read only the key names and file permissions of `.env`, `terraform.tfvars`, and the state files. It never read their values.
- **Live systems:** The review did not inspect live Azure, Databricks, or Aura settings. Any finding about a live setting says so.
- **External facts:** Claims about Azure and Databricks behavior come from Microsoft Learn pages listed at the end.

## Terms

- **Aura:** Aura is the managed Neo4j database service.
- **PE:** A private endpoint is a network card in your subnet that holds a private IP and connects to a remote service.
- **PLS:** A Private Link Service is the Aura side of the connection. You approve each incoming connection in the Aura console.
- **NCC:** A Network Connectivity Configuration tells Databricks serverless compute how to reach private resources.
- **Bastion:** Azure Bastion is a managed host that gives you SSH access to a VM with no public IP.

## Severity scale

- **High:** A mistake or an attacker can expose data or admin access, or the design goal fails.
- **Medium:** The weakness raises risk when it combines with another problem.
- **Low:** The gap is a hardening step or a documentation error.

## Findings at a glance

| ID | Severity | Finding |
|----|----------|---------|
| H1 | High | Aura public access stays on and no script checks that it is off |
| H2 | High | Serverless egress is open to the internet |
| H3 | High | One shared admin database account serves every notebook |
| M1 | Medium | Secrets and state files sit on disk in plaintext with open permissions |
| M2 | Medium | The public repo carries real identifiers and has no secret scanning |
| M3 | Medium | Apply runs with `-auto-approve` against a hardcoded personal profile |
| M4 | Medium | Tokens and passwords appear on command lines |
| M5 | Medium | The jump box relay listens on every network interface |
| M6 | Medium | The PE subnet has no network filtering and peering is broad |
| M7 | Medium | Private DNS covers one hostname and the wildcard advice is wrong |
| M8 | Medium | The Aura subscription allow-list and approval step need tighter control |
| M9 | Medium | Providers, packages, and images float and the lock file is ignored |
| M10 | Medium | The account-level credential is over-privileged and can sit in tfvars |
| L1 | Low | No resource locks, diagnostics, or alerts |
| L2 | Low | The jump box uses one static SSH key and no host hardening |
| L3 | Low | Notebooks live in `/Shared` and the delete demos have no guard |
| L4 | Low | DNS validation can pass for the wrong address |
| L5 | Low | Terraform inputs have no validation |
| L6 | Low | Docs overstate what is private |
| L7 | Low | VPN guidance uses the manual app ID and a weak certificate fallback |

## High findings

### H1. Aura public access stays on and no script checks that it is off

- **Where:** The setup guidance sits in `docs/private-endpoint-stack-setup.md` line 89. `scripts/automate.py` never mentions the step.
- **Risk:** Private Link protects traffic only when the public endpoint is off. The docs say to turn public access off after validation. No script confirms it. Until then, the admin account is reachable from the internet with only a password.
- **Impact:** The private path adds no protection while the public path stays open.
- **Fix:** Add a final "definition of done" step to `scripts/automate.py` that prints a required manual action to disable public traffic in the Aura console.
- **Fix:** Record the outside-in result in `docs/validation-report.md`.
- **Fix:** Confirm the live Aura setting now. This review could not check it.

### H2. Serverless egress is open to the internet

- **Where:** `infra/terraform/databricks-ncc/main.tf` creates a private route but no egress policy. `docs/architecture.md` line 5 promises no traffic leaves the Azure backbone.
- **Risk:** An NCC adds a private path. It does not block other paths. By default, serverless compute can reach the internet. A bad notebook or a malicious package can send data to any host.
- **Impact:** The stated goal fails for any traffic that is not the Aura connection.
- **Fix:** Create a serverless network policy in restricted access mode and bind it to the workspace.
- **Fix:** Allow only the Aura hostnames and the package index you need.
- **Fix:** List the Aura hostname in the policy. Microsoft documents that private endpoint traffic is also subject to the policy.
- **Fix:** Check the Databricks provider docs for the account network policy and workspace network option resources. Add them to the NCC stack if the pinned version supports them.

### H3. One shared admin database account serves every notebook

- **Where:** `notebooks/01_validate_connectivity.py` line 25, and the same pattern in notebooks 02, 03, and 04. The `.env` template defaults the user to `neo4j`.
- **Risk:** Validation, ETL, and demo code all use the same admin login. Any code that runs with read access to the `neo4j` scope gets full control of the database. That includes `DETACH DELETE`.
- **Risk:** `scripts/automate.py` line 375 creates the scope with default permissions. The creator holds MANAGE, and the scope name is readable by every workspace user.
- **Fix:** Create separate Aura users and roles. Use a read-only role for validation and a narrow write role for ETL.
- **Fix:** Create one scope per purpose. Grant READ to a named group with `databricks secrets put-acl`.
- **Fix:** Prefer a Key Vault-backed scope with a dedicated vault. Access to a Key Vault-backed scope covers every secret in that vault.
- **Fix:** Rotate the admin password after setup and keep it out of daily use.
- **Fix:** Confirm that your Aura plan supports extra users and roles.

## Medium findings

### M1. Secrets and state files sit on disk in plaintext with open permissions

- **Where:** The files are `.env`, `infra/terraform/databricks-ncc/terraform.tfstate`, `terraform.tfstate.backup`, and `terraform.tfstate.1783729732.backup`. All have mode 644.
- **Risk:** Mode 644 lets any local user read the files. The `.env` file holds the Aura admin password. State files hold resource IDs and can hold secrets if a later change adds them.
- **Good news:** Git history contains none of these files. The local `terraform.tfvars` holds no client secret.
- **Fix:** Run `chmod 600` on `.env`, the tfvars file, and every state file. Set `umask 077` for the shell that runs Terraform.
- **Fix:** Move state to an Azure Storage backend with Entra ID auth, versioning, soft delete, and state locking.
- **Fix:** Keep `.env` outside the repo folder or load it from a secrets manager.
- **Fix:** Delete old state backups once the remote backend works.

### M2. The public repo carries real identifiers and has no secret scanning

- **Where:** The GitHub repo `neo4j-field/azure-databricks-aura-privatelink` is public. `docs/automate-tf-ncc-setup.md` line 49 holds the Databricks account ID and workspace host. `README.md` now holds only placeholders. `notebooks/04_smoke_test.py` hardcodes an Aura hostname and a workspace name.
- **Risk:** These values are identifiers, not credentials. They still map your environment for phishing and targeted attacks.
- **Risk:** The repo has no CI, no secret scanning, and no pre-commit checks. A future mistake would ship a real secret to a public repo.
- **Fix:** Replace real values in tracked files with placeholders. Read them from environment variables or tfvars.
- **Fix:** Turn on GitHub secret scanning and push protection.
- **Fix:** Add a `gitleaks` pre-commit hook. Add a CI job that runs `trivy config` or `checkov` on `infra/`.
- **Fix:** Add branch protection and a CODEOWNERS file for `infra/` and `scripts/`.
- **Fix:** Delete or rotate the Aura instance named in notebook 03 if it is still live.
- **Note:** Rewriting git history is optional because the exposed values are identifiers.

### M3. Apply runs with `-auto-approve` against a hardcoded personal profile

- **Where:** `scripts/automate.py` line 175 passes `-auto-approve`. Line 539 defaults `--workspace-profile` to `azure-rk-knight`. Line 358 deletes the secret scope on `--reset-secret-scope`. Line 397 overwrites the notebook.
- **Risk:** No person reads the plan before it applies. The stack changes account-level objects, including the workspace NCC binding. A default personal profile can point the run at the wrong workspace.
- **Fix:** Run `terraform plan -out`, print the summary, and apply that saved plan only after a `--yes` flag or a typed confirmation.
- **Fix:** Remove the default profile. Require `--workspace-profile` and print the target account and workspace before any change.
- **Fix:** Ask for confirmation before `delete_scope`.

### M4. Tokens and passwords appear on command lines

- **Where:** `scripts/create-secret-scope.sh` line 43 uses `--string-value "${value}"`. `scripts/create-private-endpoint-rule.sh` line 62 puts the bearer token in a curl header.
- **Risk:** Other local users can read command-line arguments in the process list.
- **Risk:** `create-private-endpoint-rule.sh` prints the full API response. `create-secret-scope.sh` line 38 hides every error with `2>/dev/null || echo`, so a permission error looks like "scope already exists".
- **Fix:** Pipe the value on standard input: `printf %s "$value" | databricks secrets put-secret "$SCOPE" "$key"`. The CLI supports this.
- **Fix:** Replace the raw curl call with `databricks api post --profile <name>`. Alternatively, pass the header from a file with `curl -H @file`.
- **Fix:** Print only the fields you need from the API response.
- **Fix:** Check for the scope with `databricks secrets list-scopes` and let real errors fail the script.

### M5. The jump box relay listens on every network interface

- **Where:** `infra/terraform/jumpbox/main.tf` line 22 starts `socat TCP-LISTEN:${port},fork,reuseaddr` with no `bind=`. The unit runs as root.
- **Risk:** Only the NSG on the NIC stops other hosts from reaching the relay. One NSG edit or a missing association exposes the private endpoint to the whole VNet.
- **Fix:** Add `bind=127.0.0.1` to the `TCP-LISTEN` option. SSH `-L` forwards still work because they connect to localhost on the VM.
- **Fix:** Add `DynamicUser=yes`, `NoNewPrivileges=yes`, and `ProtectSystem=strict` to each unit. The ports are above 1024, so root is unnecessary.

### M6. The PE subnet has no network filtering and peering is broad

- **Where:** `infra/terraform/azure-private-endpoint/variables.tf` line 29 says the subnet must have `privateEndpointNetworkPolicies` disabled. `docs/batch-jobs-other-vnets.md` lines 82 and 91 set `allow_forwarded_traffic = true`.
- **Risk:** Microsoft documents that NSG rules apply to a private endpoint only when network policies are enabled on its subnet. With policies disabled, nothing filters traffic to the PE NIC.
- **Risk:** Every workload in a peered or zone-linked VNet can reach Aura through the PE. The database password is the only control.
- **Fix:** Enable network policies for NSGs on the PE subnet. Update the variable description to match.
- **Fix:** Attach an NSG that allows only the client subnets to the Bolt port and the other ports you use, and denies the rest.
- **Fix:** Set `allow_forwarded_traffic = false` unless a network appliance needs it. Peer only the VNets that need access.
- **Fix:** Log traffic on the client subnets. Microsoft documents that NSG flow logs do not cover inbound traffic to a private endpoint.

### M7. Private DNS covers one hostname and the wildcard advice is wrong

- **Where:** `infra/terraform/azure-private-endpoint/main.tf` lines 102 to 107 create one A record. `docs/private-endpoint-stack-setup.md` step 4 and `docs/architecture.md` line 133 suggest a `p-*` wildcard record.
- **Risk:** Aura routing hosts such as `p-...` need their own records. A missing record breaks the connection, and operators then loosen settings to make it work.
- **Risk:** A DNS wildcard must be the whole label `*`. A record named `p-*` matches nothing. A real `*` record would point every Aura hostname at your endpoint.
- **Fix:** Add a routing-hostname list variable. Create a private DNS zone and VNet link for each distinct `<orch>.neo4j.io` domain, then one A record per routing host. See T6.
- **Fix:** Correct the wildcard advice in `docs/architecture.md` line 133. Tell operators to add explicit records.
- **Fix:** Do not enable the NxDomainRedirect fallback on the zone link. It sends unresolved names to public DNS.

### M8. The Aura subscription allow-list and approval step need tighter control

- **Where:** `docs/automate-tf-ncc-setup.md` step 2 tells the operator to add a Databricks-managed subscription ID to the Aura allow-list. The text says this "can iterate".
- **Risk:** The allow-list lets that subscription request connections to your PLS. A Databricks-managed subscription is not yours, and it can host endpoints for other customers. Confirm this with Databricks.
- **Risk:** Manual approval is then the only control. A pending request can sit for 14 days.
- **Fix:** Before approving, match the pending request to the endpoint named in the NCC rule output. Reject any request that does not match. This waits on someone with Aura console access, who must confirm what identifier the console shows.
- **Fix:** Reject stale requests.
- **Fix:** Remove allow-list entries that you no longer need. Confirm with Databricks first that removing an entry does not affect an established rule.
- **Fix:** Add a reminder or alert for requests that stay pending.

### M9. Providers, packages, and images float and the lock file is ignored

- **Where:** `infra/terraform/databricks-ncc/main.tf` line 7 uses `>= 1.55.0`. `.gitignore` line 15 ignores `.terraform.lock.hcl`. The notebooks run `%pip install neo4j==5.*`. `infra/terraform/jumpbox/main.tf` lines 36 and 37 run unpinned `apt-get`, and line 183 uses image version `latest`.
- **Risk:** The Databricks provider runs with account admin rights. A new or hijacked release changes what runs in your account.
- **Risk:** The unlocked package and image versions make runs differ over time and weaken supply chain checks.
- **Fix:** Remove `.terraform.lock.hcl` from `.gitignore` and commit the lock files.
- **Fix:** Use a pessimistic constraint such as `~> 1.121` for the Databricks provider. Add an upper bound for each provider.
- **Fix:** Pin notebook packages to an exact version, for example `neo4j==5.28.1`, ideally through a hashed requirements file.
- **Fix:** Pin the VM image version. Turn on `unattended-upgrades` for security patches.

### M10. The account-level credential is over-privileged and can sit in tfvars

- **Where:** `infra/terraform/databricks-ncc/main.tf` lines 17 to 20 read a client ID and secret. `terraform.tfvars.example` line 25 labels the service principal secret path as "recommended for CI/CD".
- **Risk:** NCC changes need account admin rights. A long-lived secret with that power is a high-value target. Putting it in tfvars leaves it in plaintext on disk.
- **Fix:** Prefer Azure CLI sign-in as a named admin for one-off runs.
- **Fix:** For CI, use workload identity federation so no stored secret exists.
- **Fix:** If you must use a secret, pass it through an environment variable, give it a short expiry, store it in Key Vault, and rotate it.
- **Fix:** Remove the secret placeholder from the tfvars example.

## Low findings

### L1. No resource locks, diagnostics, or alerts

- **Where:** None of the three stacks defines `azurerm_management_lock`, a diagnostic setting, or an alert.
- **Risk:** Someone can delete the PE or the DNS zone by accident. Nobody sees Bastion sessions or endpoint changes.
- **Fix:** Add `CanNotDelete` locks on the PE, the DNS zone, and the jump box.
- **Fix:** Send Bastion audit logs and the Activity Log to a Log Analytics workspace. Alert on PE and NSG changes.
- **Fix:** Add an auto-shutdown schedule for the jump box.

### L2. The jump box uses one static SSH key and no host hardening

- **Where:** `infra/terraform/jumpbox/main.tf` creates the VM with one `ssh_public_key` and the default user `azureuser`. The VM has no encryption at host.
- **Risk:** A leaked key gives access to a host that can reach Aura. Shared keys leave no per-person trail.
- **Fix:** Use Entra ID SSH login with the `AADSSHLoginForLinux` extension and Conditional Access.
- **Fix:** Set `encryption_at_host_enabled = true` on the VM.
- **Fix:** Update the stale "gcloud ssh" text in the `ssh_public_key` description.

### L3. Notebooks live in `/Shared` and the delete demos have no guard

- **Where:** `scripts/automate.py` line 72 sets the import folder to `/Shared/aura-privatelink`. `notebooks/04_smoke_test.py` line 240 and `notebooks/03_serverless_push_pull_demo.py` line 109 build `DETACH DELETE` queries from f-string labels.
- **Risk:** `/Shared` is usually open to all workspace users. Confirm this in your workspace. Another user could edit a notebook that then runs with the operator's secret access.
- **Risk:** The label constants are safe today. With the admin account, a wrong label deletes real data.
- **Fix:** Import notebooks to `/Workspace/Users/<operator>/aura-privatelink` and set folder permissions. Alternatively, run from a Git folder pinned to a commit.
- **Fix:** Check that each label starts with a test prefix before any delete. Run the demos against a separate database.
- **Fix:** Remove the dead code on line 27 of `notebooks/01_validate_connectivity.py`. The `if False else "neo4j"` expression ignores the `database` secret.

### L4. DNS validation can pass for the wrong address

- **Where:** `scripts/validate-dns.py` lines 18 and 19 use `ipaddress.is_private`. Notebook 01 uses a hand-written prefix check.
- **Risk:** `is_private` is true for loopback and link-local addresses as well as private ranges. A bad answer such as `127.0.0.1` reports success. The script reads IPv4 only.
- **Fix:** Compare the answer to the PE subnet CIDR, or to the PE NIC IP from `terraform output`.
- **Fix:** Use `socket.getaddrinfo` so IPv6 answers fail the check.

### L5. Terraform inputs have no validation

- **Where:** `infra/terraform/databricks-ncc/variables.tf` has no `validation` blocks. `databricks_workspace_url` is declared and never used.
- **Risk:** A typo in the PLS alias, the account ID, or the hostname routes the rule to the wrong target. Approving a wrong request is a real risk.
- **Fix:** Validate that the account ID is a GUID and the workspace ID is numeric.
- **Fix:** Validate that the PLS alias ends with `.azure.privatelinkservice`.
- **Fix:** Validate that each Aura hostname ends with `.databases.neo4j.io`.
- **Fix:** Remove the unused variable.

### L6. Docs overstate what is private

- **Where:** `docs/architecture.md` line 53 says the control plane is "already private". Line 108 says a missing DNS record "silently falls back to public resolution".
- **Risk:** Readers skip hardening steps because the docs say the job is done. Workspace user access is public unless the workspace uses front-end Private Link.
- **Risk:** Azure documents a separate fallback setting, which suggests the default is an NXDOMAIN answer. Test this before you rely on either claim.
- **Fix:** List which paths are private and which are not.
- **Fix:** Correct the DNS fallback statement after a test.
- **Fix:** Correct the multi-instance advice in `docs/batch-jobs-other-vnets.md`. Running the stack again for a second instance creates a second `databases.neo4j.io` zone. Tell readers to add records to the existing zone.

### L7. VPN guidance uses the manual app ID and a weak certificate fallback

- **Where:** `docs/developer-desktop-access.md` line 309 uses the audience `41b23e61-6c1e-4545-b367-cd054e0ed4b4`. Lines 324 to 328 describe certificate login.
- **Risk:** Microsoft now offers a Microsoft-registered Azure VPN Client app ID, `c632b3df-fb67-4d84-bdcf-b95ad541b5c8`. It avoids the manual registration. The doc has no revocation process for client certificates.
- **Fix:** Switch the example to the Microsoft-registered audience for Windows and macOS clients.
- **Fix:** Require Conditional Access with MFA for the VPN app.
- **Fix:** If you keep certificate login, document how to issue short-lived client certificates and revoke a lost one.

## What already works well

- **Manual approval:** The PE uses `is_manual_connection = true`, so Aura must approve every connection.
- **DNS link:** The VNet link sets `registration_enabled = false`, so VMs cannot register names in the zone.
- **VM exposure:** The jump box has no public IP, disables password login, and sits behind a deny-all NSG.
- **Secret variables:** Secret inputs are marked `sensitive` and default to null.
- **Git hygiene:** `.gitignore` covers `.env*`, `*.tfvars`, `*.tfstate*`, and key files. The full history holds none of them.
- **Secret handling in code:** Notebooks read credentials from `dbutils.secrets`. `scripts/automate.py` writes secrets through the SDK, not a shell command.
- **Guard rails:** `notebooks/04_smoke_test.py` asserts the host before it runs, and the shell scripts use `set -euo pipefail`.

## Doc changes already applied

These edits to `README.md` and `docs/manual-ncc-setup.md` are done. They are doc-only and do not change the setup flow.

- **H1:** README Step 9 now says Private Link does not close the public endpoint. It adds an outside-in `nc` check. `docs/manual-ncc-setup.md` has a new Step 7 that points to it. The README Limitations table has a matching row.
- **H2:** The README Limitations table and a new "What the NCC does not cover" section in `docs/manual-ncc-setup.md` say the NCC does not restrict other outbound traffic. The Terraform fix (T5) is still open.
- **M7:** The README and the manual guide name routing hostnames in full form, `p-<aura-id>-<suffix>.<orch>.neo4j.io`, instead of `p-*.neo4j.io`.
- **M8:** Both docs say to add only the subscription ID from your own failed call. Both say to approve within a day.

## Recommendations by area

Each group lists the open work in severity order. The finding sections above hold the detail.

### Setup docs

- **H1:** Record the outside-in result in `docs/validation-report.md`. Confirm the live Aura public access setting. This needs Aura console access.
- **M2:** Replace the real identifiers in `docs/automate-tf-ncc-setup.md` line 49 with placeholders.
- **M6:** Set `allow_forwarded_traffic = false` in the examples in `docs/batch-jobs-other-vnets.md` lines 82 and 91.
- **M7:** Correct the wildcard advice in `docs/architecture.md` line 133.
- **M8:** Match each pending request to the rule before approving. This needs Aura console access. Confirm with Databricks before removing allow-list entries.
- **M10:** Update the service principal wording in the README, line 60, and `docs/automate-tf-ncc-setup.md`. This waits for T4, so it is deferred.
- **L6:** Correct `docs/architecture.md` lines 53 and 108. Correct the multi-instance advice in `docs/batch-jobs-other-vnets.md`.
- **L7:** Update `docs/developer-desktop-access.md` for the Microsoft-registered VPN app ID and Conditional Access.

### Terraform

Findings H2, M1, M5, M6, M7, M9, M10, L1, L2, and L5 need Terraform changes. The next section lists them as T1 to T11. The order and the phased plan follow it.

### Scripts and notebooks

These findings fit neither group above.

- **H1:** Add a "definition of done" step to `scripts/automate.py` that prints the manual action to disable public access.
- **H3:** Create least-privilege Aura users. Use one secret scope per purpose, with ACLs.
- **M1:** Run `chmod 600` on `.env`, the tfvars file, and every state file.
- **M2:** Replace the hardcoded Aura hostname and workspace name in `notebooks/04_smoke_test.py`. Turn on secret scanning and add pre-commit and CI checks.
- **M3:** Plan before apply, remove the default `--workspace-profile`, and confirm before `delete_scope`.
- **M4:** Pipe secret values on standard input in `scripts/create-secret-scope.sh`. Replace the raw bearer token in `scripts/create-private-endpoint-rule.sh`. The curl examples in README Step 6 and the REST section of `docs/manual-ncc-setup.md` use the same header pattern. They stay unchanged.
- **L3:** Import notebooks to a per-user folder and guard the `DETACH DELETE` demos.
- **L4:** Compare DNS answers to the PE subnet in `scripts/validate-dns.py` and notebook 01.

## Terraform changes needed

Ten findings need Terraform changes. The rest are script, notebook, docs, or process fixes. Each row names the stack, the change, and the finding it closes.

| # | Stack | Change | Finding |
|---|---|---|---|
| T1 | All three | Pin each provider with a pessimistic constraint and an upper bound. Use `~> 1.121` for Databricks. Pick the azurerm major version from a fresh lock file. Commit each `.terraform.lock.hcl`. | M9 |
| T2 | All three | Add an `azurerm` backend with Entra ID auth, so state lives in Azure Storage with versioning, soft delete, and locking. Use partial backend configuration so no storage names are committed. | M1 |
| T3 | NCC | Add `validation` blocks for the account ID, workspace ID, PLS alias, instance hostname, and extra hostnames. Remove the unused `databricks_workspace_url` variable. | L5 |
| T4 | NCC | Remove the `azure_client_secret` variable and its tfvars placeholder. A service principal secret then comes only from the environment. Prefer Azure CLI sign-in or workload identity federation. | M10 |
| T5 | NCC | Add `databricks_account_network_policy` in restricted access mode. Bind it to the workspace with `databricks_workspace_network_option`. Allow the Aura instance hostname, each routing hostname, and the package index hosts the notebooks need. | H2 |
| T6 | PE | Add a routing-hostname list variable. For each distinct `<orch>.neo4j.io` domain, create a private DNS zone and a VNet link. Create one A record per routing host that points at the PE NIC IP. Leave NxDomainRedirect off. | M7 |
| T7 | PE | Add an opt-in NSG that allows only the client CIDRs to the Neo4j ports and denies the rest, plus its subnet association. Add a precondition that fails the plan when NSG network policies are off on the PE subnet. Fix the subnet variable description. | M6 |
| T8 | Jump box | Add `bind=127.0.0.1` to each socat listener. Add `DynamicUser=yes`, `NoNewPrivileges=yes`, and `ProtectSystem=strict` to each relay unit. | M5 |
| T9 | Jump box | Pin the VM image version through a variable. Install and enable `unattended-upgrades` in the startup script. | M9 |
| T10 | Jump box | Set `encryption_at_host_enabled = true`. Add the `AADSSHLoginForLinux` extension and a system-assigned identity. Fix the stale "gcloud ssh" text in the `ssh_public_key` description. | L2 |
| T11 | PE, jump box | Add `CanNotDelete` locks on the PE, the DNS zones, and the jump box. Send Bastion audit logs and the Activity Log to Log Analytics. Alert on PE and NSG changes. Add a jump box auto-shutdown schedule. | L1 |

Three findings need a correction before their Terraform change lands:

- **M7 correction:** Routing hosts sit under `<orch>.neo4j.io`, not under `databases.neo4j.io`. An A record per name in the existing zone would never match them. T6 creates a zone per routing domain instead. `docs/private-endpoint-stack-setup.md` now describes this, so the README fix in M7 is done.
- **L5 correction:** Routing hostnames in `aura_extra_domain_names` end in `<orch>.neo4j.io`. A `.databases.neo4j.io` check on that list would reject valid input. T3 checks the instance hostname for `.databases.neo4j.io` and the extra list for `.neo4j.io`.
- **H2 dependency:** The notebooks run `%pip install` on serverless. A restricted policy blocks PyPI unless its hosts are on the allow list. T5 must list them, or the validation notebook fails before it reaches Aura.

## Order of Terraform changes

1. **T1, pin providers.** Every later diff then comes from our change, not a provider upgrade. The network policy resources in T5 also need a floor above today's `>= 1.55.0`. Databricks added them in provider v1.104.0.
2. **T2, remote state.** Migrating state before more resources land in it keeps the move small.
3. **T3 and T4, validation and credential cleanup.** Neither changes infrastructure. Both catch bad input before the high-risk change in step 4.
4. **T5, serverless egress policy.** This closes the only High finding that Terraform can fix. It comes after steps 1 to 3 so a typo or a provider surprise cannot widen the damage.
5. **T6, then T7, PE stack DNS and NSG.** DNS goes first. The NSG test needs working name resolution to tell a blocked connection from a DNS failure.
6. **T8, T9, and T10, jump box.** T8 goes first because it closes a Medium finding with a small change.
7. **T11, locks, diagnostics, and alerts.** Locks go last. They block the destroy-and-recreate cycles that earlier steps may need.

## Phased implementation and testing plan

### Goal

Apply T1 to T11 one phase at a time. After each phase, prove that the private path still works and that the fix does what it claims.

### Assumptions

- The current setup passes one clean run first. Phase 0 records that baseline.
- The demo workspace and Aura instance can break for a short time. If not, run Phase 3 in a separate workspace first.
- The operator is a Databricks account admin. Creating Azure locks also needs Owner or User Access Administrator on the resource groups.
- The workspace tier supports serverless egress control. Phase 3 confirms this before any change.
- Phases 4 to 6 apply only where the PE stack and jump box are deployed. Mark them skipped otherwise.

### Risks

- **Egress lockout:** A restricted policy that misses a host breaks every serverless workload in the workspace, not only this repo's notebooks. Start in dry-run mode if available, and keep a one-step rollback to the default policy.
- **State migration:** A failed backend migration can split state. Back up local state first, and confirm a clean plan after the move.
- **Shared subnet NSG:** An NSG on a subnet the stack does not own can cut off other workloads in that subnet. T7 is opt-in for this reason. Confirm the subnet holds only the PE before you enable it.
- **Jump box replacement:** Changing the startup script replaces the VM. Encryption at host also needs the `EncryptionAtHost` feature registered on the subscription.
- **Locks block teardown:** `CanNotDelete` locks make `terraform destroy` and `docs/teardown.md` fail until you remove them.
- **Validation too strict:** A tight pattern can reject a valid alias or hostname. Test each rule against the working tfvars before you merge it.

### Deliberately not doing

- **Non-Terraform findings:** H1, H3, M2, M3, M4, M8, L3, L4, L6, and L7 get their own pass. H1 needs no Terraform, so check it during Phase 0.
- **Secrets in Terraform:** Managing secret scopes or secret values in Terraform would put the Aura password in state. The script keeps that job.
- **Wildcard DNS records:** M7 explains why a `p-*` record matches nothing and a `*` record captures too much.
- **Git history rewrite:** M2 rates it optional, because the exposed values are identifiers.
- **CI scanners:** Wiring `trivy` and `checkov` into CI is M2 work. This plan runs them locally as a check.

### Decisions

- **Pin first.** A locked provider keeps each phase's diff limited to our change. Pinning at the end is dropped.
- **Zone per routing domain.** Routing hosts live under `<orch>.neo4j.io`, so they need their own zone. Wildcards and extra records in the instance zone are dropped.
- **Precondition, not subnet edits.** The PE stack reads the subnet and does not own it. A precondition fails the plan when network policies are off. Having the stack change the subnet is dropped.
- **Secret from the environment only.** A tfvars file holds a `sensitive` variable in plaintext on disk. Keeping the variable is dropped.
- **Reuse the Aura hostnames for the allow list.** The NCC stack already holds them, so T5 feeds the policy from the same variables. Package hosts get their own variable with a PyPI default. Hardcoded hosts are dropped.
- **Locks last.** Adding locks with each resource would block the recreate cycles earlier phases need. That option is dropped.

### Checks for every phase

- `terraform fmt -check` and `terraform validate` pass in each changed stack.
- `terraform plan -out` shows only the changes the phase intends. Apply that saved plan.
- `trivy config` and `checkov` on the changed stack show no new findings.
- After any NCC stack apply, `automate.py run --no-apply --skip-warehouse-restart` prints `SUCCESS`.
- The phase status below records the result.

### Phase 0: Baseline run

- **Status:** Pending
- **Outcome:** The current setup passes end to end, and its outputs are on record.
- **Checklist:**
  - [ ] Run `automate.py run` from a clean state, following `docs/automate-tf-ncc-setup.md`.
  - [ ] Record the NCC ID, the rule ID, any routing hostnames, and the validation output.
  - [ ] Check the live Aura public access setting, as in H1, and record it.
  - [ ] Back up local state and tfvars outside the repo. Run `chmod 600` on them, as in M1.
- **Validation:** The run exits `0` and prints `SUCCESS`.

### Phase 1: Pin providers and move state (T1, T2)

- **Status:** Pending
- **Outcome:** Each stack uses locked provider versions and remote state. No infrastructure changes.
- **Checklist:**
  - [ ] Set pessimistic constraints with upper bounds in all three stacks.
  - [ ] Remove `.terraform.lock.hcl` from `.gitignore` and commit each lock file.
  - [ ] Create the state storage account with versioning, soft delete, and Entra ID auth.
  - [ ] Add the backend block and migrate state in each deployed stack.
  - [ ] Delete local state backups once remote state checks out.
- **Validation:**
  - `terraform plan` reports no changes in each stack after the migration.
  - `terraform init` on a fresh clone installs the locked provider versions.
  - A second `terraform plan` started during the first one waits on the state lock.

### Phase 2: NCC input validation and credential cleanup (T3, T4)

- **Status:** Pending
- **Outcome:** Bad input fails at plan time. No variable can hold the service principal secret.
- **Checklist:**
  - [ ] Add validation for the account ID, workspace ID, PLS alias, instance hostname, and extra hostnames.
  - [ ] Remove `databricks_workspace_url` and `azure_client_secret`. Remove both from `terraform.tfvars.example` and from your local tfvars.
  - [ ] Document the environment variable path for service principal auth in the tfvars example and `docs/automate-tf-ncc-setup.md`.
- **Validation:**
  - The working tfvars passes `terraform plan` with no changes.
  - A bad alias, a non-numeric workspace ID, and a misspelled instance hostname each fail the plan with a clear message.
  - A routing hostname under `<orch>.neo4j.io` in the extra list passes.
  - `automate.py run --no-apply` still prints `SUCCESS`.

### Phase 3: Serverless egress policy (T5)

- **Status:** Pending
- **Outcome:** Serverless compute in the workspace reaches only the Aura hosts and the package index.
- **Checklist:**
  - [ ] Confirm that the workspace tier supports serverless egress control.
  - [ ] Add the network policy and workspace binding to the NCC stack. Feed it the existing Aura hostname variables and a new package-host variable.
  - [ ] Start in dry-run mode if the provider exposes it. Otherwise apply in a non-production workspace first.
  - [ ] Review the denial logs and add any host this workload needs.
  - [ ] Switch to enforced mode.
  - [ ] Document the rollback, which rebinds the workspace to the default policy.
- **Validation:**
  - The validation notebook passes, including its `%pip install`.
  - `notebooks/04_smoke_test.py` passes its write and read-back.
  - A serverless notebook request to a host outside the list, such as `https://example.com`, fails.
  - Denied requests appear in the outbound network system table. Confirm the table name in your workspace.

### Phase 4: PE stack DNS and NSG (T6, T7)

- **Status:** Pending
- **Outcome:** Every routing host resolves to the PE private IP from linked VNets. Only allowed client subnets reach the PE.
- **Checklist:**
  - [ ] Add the routing-hostname variable, the per-domain zones, the VNet links, and the A records. Gate all of them on `manage_private_dns`.
  - [ ] Import or remove any hand-made routing records from earlier runs, so Terraform owns them.
  - [ ] Add the opt-in NSG, its subnet association, and the network policy precondition. Fix the subnet variable description.
  - [ ] Point the routing-host steps in `docs/private-endpoint-stack-setup.md` at the new variable.
- **Validation:**
  - From a VM in a linked VNet, `nslookup` returns the PE private IP for the instance host and each routing host.
  - A Bolt connection that follows the routing table succeeds.
  - With the NSG on, a VM in an allowed subnet connects and a VM in another subnet times out.
  - The plan fails with a clear message against a subnet with network policies off.

### Phase 5: Jump box hardening (T8, T9, T10)

- **Status:** Pending
- **Outcome:** Relays listen only on localhost and run unprivileged. The image is pinned. Sign-in uses Entra ID.
- **Checklist:**
  - [ ] Add `bind=127.0.0.1` and the systemd hardening options to each relay unit.
  - [ ] Add an image version variable set to the current image. Enable unattended upgrades.
  - [ ] Register `EncryptionAtHost` on the subscription, then enable encryption at host.
  - [ ] Add the Entra SSH extension and identity. Grant the VM login role to the operator group. Fix the `ssh_public_key` description.
  - [ ] Update `docs/developer-desktop-access.md` for Entra SSH sign-in.
- **Validation:**
  - On the VM, the relay ports listen on `127.0.0.1` only and run as a non-root user.
  - The Bastion tunnel with SSH `-L` forwards still reaches Neo4j Browser and Bolt.
  - From another VM in the VNet, the relay ports are unreachable.
  - `az ssh vm` through Bastion signs in with an Entra account.
  - The VM reports encryption at host as enabled.

### Phase 6: Locks, diagnostics, and alerts (T11)

- **Status:** Pending
- **Outcome:** Key resources resist accidental deletion, and changes to them raise an alert.
- **Checklist:**
  - [ ] Add `CanNotDelete` locks on the PE, the DNS zones, and the jump box.
  - [ ] Add a Log Analytics workspace variable. Send Bastion audit logs and the Activity Log there.
  - [ ] Add activity log alerts for PE and NSG writes and deletes.
  - [ ] Add the jump box auto-shutdown schedule.
  - [ ] Add the lock removal step to `docs/teardown.md`.
- **Validation:**
  - `az lock list` shows each lock.
  - A test NSG rule edit fires the alert.
  - A Bastion session appears in the Log Analytics workspace.
  - The updated teardown steps work with the locks in place.

### Completion criteria

- T1 to T11 are applied in every deployed stack, or marked skipped with a reason.
- Each phase's validation passed, and its status records the result.
- A final clean run of `automate.py` prints `SUCCESS` with the egress policy enforced.
- `trivy config` and `checkov` on `infra/` show no unresolved High findings.
- The findings table marks H2, M1, M5, M6, M7, M9, M10, L1, L2, and L5 as fixed.

## Limits of this review

- **No live checks:** The review did not query Azure, Databricks, or Aura. Confirm the Aura public access setting, the `/Shared` folder permissions, and the PE subnet policy in the live environment.
- **No scanners:** A scanner run on `infra/` can add findings. Run `trivy config` and `checkov` after the fixes land.
- **No code changes:** The review changed no code. Each fix above is a recommendation to discuss before you apply it.

## Sources

- Azure private endpoint network policies: https://learn.microsoft.com/en-us/azure/private-link/disable-private-endpoint-network-policy
- Azure private endpoint limits and NSG flow logs: https://learn.microsoft.com/en-us/azure/private-link/private-endpoint-overview
- Databricks serverless egress control: https://learn.microsoft.com/en-us/azure/databricks/security/network/serverless-network-security/network-policies
- Databricks secret management: https://learn.microsoft.com/en-us/azure/databricks/security/secrets/
- Azure private DNS fallback to internet: https://learn.microsoft.com/en-us/azure/dns/private-dns-fallback
- Azure VPN Gateway Entra ID authentication: https://learn.microsoft.com/en-us/azure/vpn-gateway/point-to-site-entra-gateway
