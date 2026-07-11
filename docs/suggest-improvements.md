# Suggested improvements

Ideas that would improve the orchestrator (`scripts/automate.py`) but are not built,
because the current reference target is a demo. Each entry describes the problem, the
proposed change, and how to build it if someone picks it up later.

## Proactive routing-host discovery and opt-in auto-apply

**Status:** not built. Recorded here instead of implemented.

### The problem

Neo4j Aura VDC advertises Bolt *routing* hosts like `p-<dbid>-....neo4j.io` only after
the first connection to `<dbid>.databases.neo4j.io`. Each of those routing hosts must
also resolve through NCC-managed DNS, or the driver fails with
`Cannot resolve address p-...neo4j.io:7687`.

Today the orchestrator finds these hosts reactively, one failed run at a time. When a
validation run fails on an unresolved routing host, `report_routing_hosts()` in
`scripts/automate.py` scrapes the hostname out of the notebook output and prints the
exact `aura_extra_domain_names` line to add to `terraform.tfvars`. The operator adds it,
re-applies, and re-runs. If Aura advertises several routing hosts, this repeats once per
host: one Terraform apply per hostname.

### The proposed change

Discover every routing host up front, so one extra apply covers them all.

1. Add a small probe notebook or script, submitted the same way as the validation run in
   `run_validation()`. It connects to `<dbid>.databases.neo4j.io`, reads the routing
   table, and returns every `p-*.neo4j.io` host.
2. Run the probe as soon as the NCC rule reaches `ESTABLISHED`. Merge the results into a
   single suggested `aura_extra_domain_names` value, deduped against hosts already in
   `terraform.tfvars`.
3. Add an opt-in `--apply` flag that edits `terraform.tfvars` in place by appending, not
   overwriting, and re-runs `terraform apply`. Print-only stays the default so Terraform
   edits remain reviewable.

### Exit criterion

A fresh setup needs at most two Terraform applies: the initial one, and one for the
routing hosts. Not one apply per reactively discovered hostname.

### Notes for whoever builds it

- The reactive scraper (`ROUTING_HOST_RE`, `report_routing_hosts()`) already encodes the
  hostname pattern and the tfvars line format. Reuse both.
- The routing table read belongs in the driver call, not in a regex. A `neo4j` driver
  session against the cluster URI exposes the routing table through the routing-aware
  connection; the probe should surface the resolved hosts it is handed.
- Keep the tfvars edit append-only and idempotent, so re-running the probe does not
  duplicate hosts already present.
