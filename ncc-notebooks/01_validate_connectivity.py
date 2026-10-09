# Databricks notebook source
# MAGIC %md
# MAGIC # Neo4j Aura NCC Connectivity Validation
# MAGIC
# MAGIC End-to-end check that Azure Databricks serverless compute reaches Neo4j Aura over a
# MAGIC private endpoint managed by a Network Connectivity Configuration (NCC).
# MAGIC
# MAGIC **Prerequisites**
# MAGIC - The NCC is attached to this workspace and its private endpoint rule reads `ESTABLISHED`.
# MAGIC   See docs/setup-ncc-manual.md or docs/setup-ncc-terraform.md.
# MAGIC - The Databricks secret scope `neo4j` holds the keys `uri`, `username`, and `password`.
# MAGIC   An optional `database` key overrides the default database `neo4j`.
# MAGIC
# MAGIC The driver in step 4 maps Aura routing hosts (`p-<aura-instance-id>-*.neo4j.io`) to the
# MAGIC instance host, so this notebook passes without routing-host DNS entries.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Debug DNS (optional, run first)
# MAGIC
# MAGIC Run this cell first when you suspect a DNS or NCC problem. It needs no other cell and
# MAGIC no package install, and it does not affect the pass/fail result. It reads the host from
# MAGIC the `uri` secret. To check a routing host too, set `ROUTING_HOST` in `.env` and re-run
# MAGIC `scripts/create-secret-scope.sh`. The cell then reads it from the `routing_host` secret.
# MAGIC See TROUBLESHOOTING.md in the repo for the Databricks CLI checks of the NCC itself.
# MAGIC
# MAGIC **Run it twice.** Check 3 needs `dnspython`, which the install cell below adds. The first
# MAGIC run skips check 3. Once the install cell has finished, run this cell again to get check 3.
# MAGIC
# MAGIC The output starts with an environment block. Include it when you contact support.
# MAGIC
# MAGIC The cell runs four checks:
# MAGIC 1. Resolve each host and print the resolver error code. `EAI_NONAME` means the resolver
# MAGIC    answered that the name does not exist. `EAI_AGAIN` means it did not answer (timeout or blocked).
# MAGIC 2. Resolve control hosts that should always work. If they fail too, DNS is broken or blocked
# MAGIC    for the whole compute, not just the Aura name.
# MAGIC 3. Query DNS directly with `dnspython` and print the nameservers, the response code and the
# MAGIC    CNAME chain. A `privatelink` CNAME means the private DNS override applied. This check is
# MAGIC    skipped when `dnspython` is not installed.
# MAGIC 4. Open TCP to a public IP on 443. A failure points to the serverless egress policy, not Aura.

# COMMAND ----------

import ipaddress
import os
import socket
import sys
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from urllib.parse import urlparse

try:
    import dns.exception
    import dns.resolver
except ImportError:
    dns = None

SECRET_SCOPE = "neo4j"
try:
    SECRET_KEYS = {s.key for s in dbutils.secrets.list(SECRET_SCOPE)}
    host = urlparse(dbutils.secrets.get(scope=SECRET_SCOPE, key="uri")).hostname
except Exception as exc:
    raise RuntimeError(
        f"ERROR: cannot read the 'uri' secret in scope '{SECRET_SCOPE}'. "
        "Create the scope and keys first, as in step 1."
    ) from exc

CONTROL_HOSTS = ("example.com", "neo4j.com")
PUBLIC_EGRESS_IP = "1.1.1.1"
EAI_MEANING = {
    socket.EAI_NONAME: "resolver answered: name not found",
    socket.EAI_AGAIN: "no answer: DNS timed out or is blocked",
}


def package_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "not installed"


def resolve_report(name: str) -> None:
    try:
        ip = socket.gethostbyname(name)
    except socket.gaierror as err:
        meaning = EAI_MEANING.get(err.errno, "other resolver error")
        print(f"{name} -> UNRESOLVED (errno {err.errno}: {meaning}; {err})")
        return
    kind = "private" if ipaddress.ip_address(ip).is_private else "PUBLIC"
    print(f"{name} -> {ip} ({kind})")


def dns_query_report(name: str) -> None:
    try:
        resolver = dns.resolver.Resolver()
    except dns.resolver.NoResolverConfiguration as err:
        print(f"{name}: no resolver configuration ({err})")
        return
    print(f"{name}: nameservers {resolver.nameservers}")
    try:
        answer = resolver.resolve(name, "A", lifetime=5)
    except dns.resolver.NXDOMAIN:
        print(f"{name}: NXDOMAIN (the nameserver says the name does not exist)")
    except dns.resolver.NoAnswer:
        print(f"{name}: NOERROR but no A record")
    except dns.resolver.NoNameservers as err:
        print(f"{name}: every nameserver failed (SERVFAIL or refused): {err}")
    except dns.exception.Timeout:
        print(f"{name}: query timed out (nameserver unreachable or blocked)")
    else:
        for rrset in answer.response.answer:
            print(f"{name}: {rrset.to_text()}")


def tcp_report(address: str, port: int) -> None:
    try:
        with socket.create_connection((address, port), timeout=5):
            print(f"TCP connect to {address}:{port} succeeded")
    except OSError as err:
        print(f"TCP connect to {address}:{port} FAILED ({err})")


debug_hosts = [host]
if "routing_host" in SECRET_KEYS:
    debug_hosts.append(dbutils.secrets.get(scope="neo4j", key="routing_host"))

print("--- Environment ---")
print(f"time (UTC)      : {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
print(f"python          : {sys.version.split()[0]}")
print(f"runtime         : {os.environ.get('DATABRICKS_RUNTIME_VERSION', 'unknown')}")
print(f"neo4j driver    : {package_version('neo4j')}")
print(f"dnspython       : {package_version('dnspython')}")
print(f"target host     : {host}")

print("\n--- 1. Resolve target hosts ---")
for debug_host in debug_hosts:
    resolve_report(debug_host)

print("\n--- 2. Resolve control hosts ---")
for control_host in CONTROL_HOSTS:
    resolve_report(control_host)

print("\n--- 3. Direct DNS query ---")
if dns is None:
    print("skipped: dnspython is not installed. Once the install cell below has finished, run this cell again.")
else:
    for query_host in (*debug_hosts, *CONTROL_HOSTS):
        dns_query_report(query_host)

print("\n--- 4. Egress to a public IP ---")
tcp_report(PUBLIC_EGRESS_IP, 443)

# COMMAND ----------

# MAGIC %pip install --quiet neo4j==5.* tenacity==9.* dnspython==2.*
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %md ## 1. Load credentials from Databricks Secrets

# COMMAND ----------

SECRET_SCOPE     = "neo4j"
REQUIRED_SECRETS = ("uri", "username", "password")

try:
    SECRET_KEYS = {s.key for s in dbutils.secrets.list(SECRET_SCOPE)}
except Exception as exc:
    raise RuntimeError(
        f"ERROR: secret scope '{SECRET_SCOPE}' does not exist or is not readable. "
        "Create it and add the keys uri, username and password (optional: database)."
    ) from exc

missing = [k for k in REQUIRED_SECRETS if k not in SECRET_KEYS]
if missing:
    raise RuntimeError(
        f"ERROR: missing secret(s) in scope '{SECRET_SCOPE}': {', '.join(missing)}. "
        "Add them with `databricks secrets put-secret`."
    )

NEO4J_URI      = dbutils.secrets.get(scope=SECRET_SCOPE, key="uri")
NEO4J_USER     = dbutils.secrets.get(scope=SECRET_SCOPE, key="username")
NEO4J_PASSWORD = dbutils.secrets.get(scope=SECRET_SCOPE, key="password")
NEO4J_DATABASE = (
    dbutils.secrets.get(scope="neo4j", key="database")
    if "database" in SECRET_KEYS
    else "neo4j"
)

print(f"URI scheme/host : {NEO4J_URI.split('@')[-1]}")
print(f"User            : {NEO4J_USER}")
print(f"Database        : {NEO4J_DATABASE}")

# COMMAND ----------

# MAGIC %md ## 2. DNS sanity check: must resolve to a PRIVATE IP

# COMMAND ----------

import ipaddress
import socket
from urllib.parse import urlparse

host = urlparse(NEO4J_URI).hostname
try:
    ip = socket.gethostbyname(host)
except socket.gaierror as exc:
    raise RuntimeError(
        f"ERROR: DNS lookup failed for {host} (errno {exc.errno}: {exc}). The driver has "
        "no address to connect to, so the NCC private path is not in use. Run the "
        "'Debug DNS' cell at the top, then see TROUBLESHOOTING.md in the repo."
    ) from exc
print(f"{host} -> {ip}")

assert ipaddress.ip_address(ip).is_private, (
    f"DNS resolved to a public IP ({ip}). The private path is NOT in use. "
    "Check that the NCC private endpoint rule includes domain_names and is ESTABLISHED."
)
print("OK: resolves to private address space.")

# COMMAND ----------

# MAGIC %md ## 3. TCP reachability on Bolt port 7687

# COMMAND ----------

s = socket.create_connection((host, 7687), timeout=10)
print(f"TCP connect succeeded to {host}:7687")
s.close()

# COMMAND ----------

# MAGIC %md ## 4. Bolt+TLS connect and run a query

# COMMAND ----------

import re

from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable, TransientError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# Derive the routing-host pattern from the connection URI (populated from the
# `neo4j` secret scope / .env), so this notebook follows the configured Aura
# instance instead of a pinned instance id. Aura VDC advertises routing hosts shaped
# like p-<aura-instance-id>-<suffix>.<orch>.neo4j.io; the instance id is the first
# label of the host.
dbid = host.split(".")[0]
ROUTING_HOST_PATTERN = re.compile(rf"^p-{re.escape(dbid)}-.*\.neo4j\.io$")

def aura_private_resolver(address):
    # Aura VDC can return p-*.neo4j.io addresses in the routing table. If those
    # names do not resolve privately, map them to the Aura instance hostname,
    # which the NCC rule or the private DNS zone already resolves.
    mapped_host = host if ROUTING_HOST_PATTERN.match(address.host) else address.host
    note = " (aliased)" if mapped_host != address.host else ""
    print(f"Resolver call: {address.host}:{address.port} -> {mapped_host}:{address.port}{note}")
    return [(mapped_host, address.port)]

driver = GraphDatabase.driver(
    NEO4J_URI,
    auth=(NEO4J_USER, NEO4J_PASSWORD),
    resolver=aura_private_resolver,
    connection_timeout=10,
    max_connection_lifetime=300,
)

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type((ServiceUnavailable, TransientError)),
    reraise=True,
)
def count_nodes():
    with driver.session(database=NEO4J_DATABASE) as session:
        return session.execute_read(
            lambda tx: tx.run("MATCH (n) RETURN count(n) AS c").single()["c"]
        )

node_count = count_nodes()
print(f"Connected successfully. Current node count: {node_count}")

# COMMAND ----------

driver.close()
print("Validation complete. NCC private path is working end-to-end.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4b. Resolver experiment (optional)
# MAGIC
# MAGIC Neo4j support asked to test a resolver that maps every address to the instance host.
# MAGIC That includes the first address from the URI and every routing-table entry. This cell
# MAGIC builds its own driver, so it does not touch the driver from step 4 and it does not
# MAGIC affect the pass/fail result. It makes one attempt with no retries and prints every
# MAGIC resolver call, the outcome, and the time taken.
# MAGIC
# MAGIC Run it after step 4 to compare the two resolvers in one session. If step 4 failed,
# MAGIC "Run all" stops there, so run this cell by hand. It needs steps 1 and 2 to have run.

# COMMAND ----------

import time

from neo4j import GraphDatabase
from neo4j.exceptions import DriverError, Neo4jError


def host_only_resolver(address):
    print(f"resolver call: {address.host}:{address.port} -> {host}:{address.port}")
    return [(host, address.port)]


experiment_driver = GraphDatabase.driver(
    NEO4J_URI,
    auth=(NEO4J_USER, NEO4J_PASSWORD),
    resolver=host_only_resolver,
    connection_timeout=10,
    max_connection_lifetime=300,
)

started = time.perf_counter()
try:
    experiment_driver.verify_connectivity()
    print("verify_connectivity: OK")
    with experiment_driver.session(database=NEO4J_DATABASE) as session:
        experiment_count = session.execute_read(
            lambda tx: tx.run("MATCH (n) RETURN count(n) AS c").single()["c"]
        )
    print(f"RESULT: connected, node count {experiment_count}")
except (DriverError, Neo4jError, OSError, ValueError) as err:
    print(f"RESULT: FAILED {type(err).__name__}: {err}")
    cause = err.__cause__ or err.__context__
    while cause is not None:
        print(f"  caused by {type(cause).__name__}: {cause}")
        cause = cause.__cause__ or cause.__context__
finally:
    print(f"elapsed: {time.perf_counter() - started:.2f}s")
    experiment_driver.close()
