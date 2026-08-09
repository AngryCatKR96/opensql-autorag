#!/usr/bin/env bash
# Bootstrap and run a single node OpenSQL (PostgreSQL 17.8) instance.
#
# The license is enforced inside the server by the opensql_license module, which
# is loaded through shared_preload_libraries. Both the hostname and the CPU
# count are part of that check, so mismatches are reported here before the
# server starts and fails with a less obvious message.
set -euo pipefail

PG_HOME="${PG_HOME:-/opt/opensql/pgsql}"
PGDATA="${PGDATA:-/opt/opensql/data}"
OPENSQL_LICENSE_PATH="${OPENSQL_LICENSE_PATH:-/opt/opensql/license/license.xml}"
POSTGRES_USER="${POSTGRES_USER:-postgres}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}"
POSTGRES_DB="${POSTGRES_DB:-$POSTGRES_USER}"
INITDB_DIR=/docker-entrypoint-initdb.d

export PATH="$PG_HOME/bin:$PATH"
export PGDATA OPENSQL_LICENSE_PATH

log() { printf 'opensql-entrypoint: %s\n' "$*"; }
fail() { printf 'opensql-entrypoint: %s\n' "$*" >&2; exit 1; }

# Reads <tag>value</tag>, tolerating attributes such as <product version="3.5">.
license_field() {
    sed -n "s@.*<$1\( [^>]*\)\?>[[:space:]]*\([^<]*\)[[:space:]]*</$1>.*@\2@p" "$OPENSQL_LICENSE_PATH" | head -1
}

# Number of CPUs the license module sees, matching how it reads cgroup v2/v1.
container_cpu_limit() {
    local quota period
    if [[ -r /sys/fs/cgroup/cpu.max ]]; then
        read -r quota period < /sys/fs/cgroup/cpu.max
        [[ "$quota" != "max" ]] && { echo $(( (quota + period - 1) / period )); return; }
    elif [[ -r /sys/fs/cgroup/cpu/cpu.cfs_quota_us ]]; then
        quota=$(< /sys/fs/cgroup/cpu/cpu.cfs_quota_us)
        period=$(< /sys/fs/cgroup/cpu/cpu.cfs_period_us)
        [[ "$quota" -gt 0 ]] && { echo $(( (quota + period - 1) / period )); return; }
    fi
    nproc
}

check_license() {
    [[ -f "$OPENSQL_LICENSE_PATH" ]] || fail "license file not found at $OPENSQL_LICENSE_PATH (mount the OpenSQL license XML there)"

    local licensed_host licensed_cpu end_date cpus
    licensed_host="$(license_field identified_by_host)"
    licensed_cpu="$(license_field limit_cpu)"
    end_date="$(license_field end_date)"

    log "license: $(license_field product) $(license_field edition)/$(license_field type), expires ${end_date:-unknown}"

    if [[ -n "$licensed_host" && "$licensed_host" != "$(hostname)" ]]; then
        fail "license is issued to host '$licensed_host' but this container is '$(hostname)' (set the container hostname to '$licensed_host')"
    fi

    cpus="$(container_cpu_limit)"
    if [[ -n "$licensed_cpu" && "$cpus" -gt "$licensed_cpu" ]]; then
        fail "license allows $licensed_cpu CPUs but $cpus are visible (limit the container, e.g. cpus: $licensed_cpu)"
    fi
    log "license host '$licensed_host' and $cpus/$licensed_cpu CPUs match this container"
}

configure_instance() {
    cat >> "$PGDATA/postgresql.conf" <<'CONF'

# --- OpenSQL AutoRAG demo defaults ---
listen_addresses = '*'
shared_preload_libraries = 'opensql_license'
log_line_prefix = '%m [%p] [%u@%d] '
CONF
    echo "host all all all scram-sha-256" >> "$PGDATA/pg_hba.conf"
}

run_init_scripts() {
    local psql_cmd=(psql -v ON_ERROR_STOP=1 --no-psqlrc --host=/tmp -U "$POSTGRES_USER" -d "$POSTGRES_DB")
    local file
    for file in "$INITDB_DIR"/*; do
        [[ -e "$file" ]] || continue
        case "$file" in
            *.sql) log "running $file"; "${psql_cmd[@]}" -f "$file" ;;
            *.sh)  log "sourcing $file"; . "$file" ;;
            *)     log "ignoring $file" ;;
        esac
    done
}

bootstrap() {
    [[ -n "$POSTGRES_PASSWORD" ]] || fail "POSTGRES_PASSWORD must be set to initialize a new data directory"

    local pwfile
    pwfile="$(mktemp)"
    printf '%s' "$POSTGRES_PASSWORD" > "$pwfile"
    initdb -D "$PGDATA" -U "$POSTGRES_USER" --pwfile="$pwfile" \
        --encoding=UTF8 --locale=C.UTF-8 --data-checksums \
        --auth-local=trust --auth-host=scram-sha-256
    rm -f "$pwfile"

    configure_instance

    # Bootstrap over the unix socket only, so nothing can connect over TCP while
    # the schema is being created.
    pg_ctl -D "$PGDATA" -w -o "-c listen_addresses='' -c unix_socket_directories=/tmp" start

    if [[ "$POSTGRES_DB" != "postgres" ]]; then
        log "creating database $POSTGRES_DB"
        psql -v ON_ERROR_STOP=1 --no-psqlrc --host=/tmp -U "$POSTGRES_USER" -d postgres \
            -c "CREATE DATABASE \"$POSTGRES_DB\""
    fi

    run_init_scripts
    pg_ctl -D "$PGDATA" -w -m fast stop
    log "initialization complete"
}

check_license

if [[ ! -s "$PGDATA/PG_VERSION" ]]; then
    bootstrap
else
    log "reusing existing data directory $PGDATA"
fi

if [[ "${1:-}" == "postgres" ]]; then
    shift
    exec postgres -D "$PGDATA" "$@"
fi

exec "$@"
