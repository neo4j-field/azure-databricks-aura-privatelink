# Load the repo-root .env into your shell so the manual commands in docs/ work.
#
#   source scripts/load-env.sh
#
# Works in bash and zsh. Source it, do not run it: a child process cannot set your variables.
# Run it again after you edit .env.
#
# Values are read as KEY=VALUE lines and are never executed, so `$` and backticks in a
# password stay literal. Quote a value with "..." or '...' if it contains spaces or `#`.
# The .env value replaces any value already exported with the same name.
#
# Override the file with ENV_FILE=/path/to/file. See env.sample for every variable.
#
# Derived when unset (no network calls):
#   ZONE                     databases.neo4j.io
#   SPARK_VERSION            16.4.x-scala2.12
#   AURA_PRIVATE_HOSTNAME    the host of NEO4J_URI
#   AURA_INSTANCE_ID         the first label of AURA_PRIVATE_HOSTNAME
#   VNET_RG                  RG
#   WS_URL / WORKSPACE_URL   copied from each other
#   ORCH_ZONE, ROUTING_LABEL split from ROUTING_HOST
#
# Optional lookup (NCC path, needs a network call):
#   LOAD_ENV_LOOKUP=1 source scripts/load-env.sh
# sets WORKSPACE_ID from the account workspace list, matching WORKSPACE_NAME. Run it after
# `databricks auth login` for ACCOUNT_PROFILE. It needs the databricks CLI and jq. If the
# lookup fails it clears WORKSPACE_ID, warns, and the rest of .env still loads. A plain run leaves WORKSPACE_ID as is.

# Locate this file. bash and zsh expose the sourced path differently.
if [ -n "${ZSH_VERSION:-}" ]; then
  _pl_src="${(%):-%x}"
  case "${ZSH_EVAL_CONTEXT:-}" in *:file*) _pl_sourced=1 ;; *) _pl_sourced=0 ;; esac
else
  _pl_src="${BASH_SOURCE[0]:-$0}"
  if [ "${BASH_SOURCE[0]:-$0}" != "$0" ]; then _pl_sourced=1; else _pl_sourced=0; fi
fi

if [ "$_pl_sourced" != 1 ]; then
  echo "load-env.sh sets variables in your current shell. Run: source scripts/load-env.sh" >&2
  exit 1
fi

_pl_root="$(cd "$(dirname "$_pl_src")/.." && pwd)"
_pl_file="${ENV_FILE:-$_pl_root/.env}"

# Forget what the previous run derived, so an edited .env is not shadowed by stale values.
_pl_rest="${PL_ENV_DERIVED:-}"
while [ -n "$_pl_rest" ]; do
  _pl_rest="${_pl_rest# }"
  _pl_name="${_pl_rest%% *}"
  unset "$_pl_name"
  case "$_pl_rest" in *\ *) _pl_rest="${_pl_rest#* }" ;; *) _pl_rest="" ;; esac
done
PL_ENV_DERIVED=""

if [ ! -f "$_pl_file" ]; then
  echo "load-env: no env file at $_pl_file. Run: cp env.sample .env" >&2
  unset _pl_src _pl_sourced _pl_root _pl_file _pl_name _pl_rest
  return 1
fi

_pl_count=0
_pl_warn=""

while IFS= read -r _pl_line || [ -n "$_pl_line" ]; do
  # Trim leading whitespace, then skip blanks and comments.
  _pl_line="${_pl_line#"${_pl_line%%[![:space:]]*}"}"
  case "$_pl_line" in '' | '#'*) continue ;; esac
  case "$_pl_line" in
    export\ *)
      _pl_line="${_pl_line#export }"
      _pl_line="${_pl_line#"${_pl_line%%[![:space:]]*}"}"
      ;;
  esac

  _pl_key="${_pl_line%%=*}"
  [ "$_pl_key" = "$_pl_line" ] && continue
  _pl_key="${_pl_key%"${_pl_key##*[![:space:]]}"}"
  case "$_pl_key" in '' | [0-9]* | *[!A-Za-z0-9_]*) continue ;; esac

  _pl_val="${_pl_line#*=}"
  _pl_val="${_pl_val#"${_pl_val%%[![:space:]]*}"}"
  case "$_pl_val" in
    \"*)
      _pl_val="${_pl_val#\"}"
      _pl_val="${_pl_val%%\"*}"
      ;;
    \'*)
      _pl_val="${_pl_val#\'}"
      _pl_val="${_pl_val%%\'*}"
      ;;
    *)
      _pl_val="${_pl_val%% \#*}"
      _pl_val="${_pl_val%"${_pl_val##*[![:space:]]}"}"
      ;;
  esac

  # An empty value counts as unset, so the derived defaults below can fill it.
  if [ -z "$_pl_val" ]; then
    unset "$_pl_key"
    continue
  fi

  case "$_pl_val" in
    *\<*\>*) _pl_warn="$_pl_warn $_pl_key" ;;
  esac

  export "$_pl_key=$_pl_val"
  _pl_count=$((_pl_count + 1))
done < "$_pl_file"

# Set a derived variable only when it has no value, and remember it for the next run.
_pl_derive() {
  if [ -z "$(printenv "$1")" ] && [ -n "$2" ]; then
    export "$1=$2"
    PL_ENV_DERIVED="$PL_ENV_DERIVED $1"
  fi
}

_pl_derive ZONE "databases.neo4j.io"
_pl_derive SPARK_VERSION "16.4.x-scala2.12"
_pl_derive VNET_RG "${RG:-}"
_pl_derive WS_URL "${WORKSPACE_URL:-}"
_pl_derive WORKSPACE_URL "${WS_URL:-}"

# NEO4J_URI is neo4j+s://<host>[:port][/path]. Take the host.
_pl_host="${NEO4J_URI:-}"
_pl_host="${_pl_host#*://}"
_pl_host="${_pl_host%%/*}"
_pl_host="${_pl_host%%:*}"
_pl_derive AURA_PRIVATE_HOSTNAME "$_pl_host"
_pl_derive AURA_INSTANCE_ID "${AURA_PRIVATE_HOSTNAME%%.*}"

# ROUTING_HOST is <label>.<orch zone>. Split it at the first dot.
if [ -n "${ROUTING_HOST:-}" ]; then
  _pl_derive ORCH_ZONE "${ROUTING_HOST#*.}"
  _pl_derive ROUTING_LABEL "${ROUTING_HOST%%.*}"
fi

export PL_ENV_DERIVED

echo "load-env: loaded $_pl_count variables from $_pl_file"
if [ -n "$_pl_warn" ]; then
  echo "load-env: still a <placeholder>, fix it in .env:$_pl_warn" >&2
fi

if [ "${LOAD_ENV_LOOKUP:-}" = 1 ]; then
  if [ -z "${ACCOUNT_PROFILE:-}" ] || [ -z "${WORKSPACE_NAME:-}" ]; then
    echo "load-env: lookup skipped, set ACCOUNT_PROFILE and WORKSPACE_NAME in .env" >&2
  elif ! command -v databricks >/dev/null 2>&1 || ! command -v jq >/dev/null 2>&1; then
    echo "load-env: lookup skipped, the databricks CLI and jq are required" >&2
  else
    unset WORKSPACE_ID
    _pl_id="$(databricks --profile "$ACCOUNT_PROFILE" account workspaces list -o json 2>/dev/null \
      | jq -r --arg ws "$WORKSPACE_NAME" '.[] | select(.workspace_name==$ws) | .workspace_id' 2>/dev/null)"
    if [ -n "$_pl_id" ]; then
      export WORKSPACE_ID="$_pl_id"
      echo "load-env: WORKSPACE_ID=$WORKSPACE_ID (workspace $WORKSPACE_NAME)"
    else
      echo "load-env: no workspace named $WORKSPACE_NAME for profile $ACCOUNT_PROFILE. Log in with databricks auth login and check the name in Account Console, Workspaces." >&2
    fi
  fi
fi

unset _pl_src _pl_sourced _pl_root _pl_file _pl_name _pl_rest _pl_count _pl_warn \
  _pl_line _pl_key _pl_val _pl_host _pl_id
unset -f _pl_derive
