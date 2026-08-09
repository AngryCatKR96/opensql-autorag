# Running on Tmax OpenSQL

The demo stack ships two interchangeable databases:

| Service   | Image                                     | Port | Purpose                        |
|-----------|-------------------------------------------|------|--------------------------------|
| `db`      | `pgvector/pgvector:pg16`                  | 5432 | Local development and tests    |
| `opensql` | built from the OpenSQL distribution       | 5433 | The real target database       |

The application code is identical for both; only `AUTORAG_DATABASE_URL` changes.

## What the OpenSQL image contains

Built from `Tmax_OpenSQL_3.17.8.7_rockylinux9.7_buildtime20260720` (package
release 3.7) on Rocky Linux 9:

- PostgreSQL 17.8, installed with the vendor's `scripts/install.sh`
- pgvector 0.8.1, compiled against the shipped server headers
- `opensql_license`, loaded via `shared_preload_libraries`
- the `opensql` information binary at `/opt/opensql/bin/opensql`

Patroni, etcd, OpenProxy, and Barman are part of the distribution but are not
installed: the trial license covers a single host, which is the installer's
`single` mode. See "High availability" below.

## Prerequisites

Two licensed Tmax artifacts, neither of which is committed to this repository —
both come from Tmax with your own license:

- the distribution tarball, `Tmax_OpenSQL_<version>_rockylinux9.7_*.tar.gz`
- a license XML issued for the host the container will run as

A trial license covers a single host and a fixed CPU count. Read the
`identified_by_host` and `limit_cpu` values out of your own XML and make the
compose service match them; see "License constraints" below.

## Setup

```bash
./infra/opensql/stage-artifacts.sh \
    ~/Downloads/Tmax_OpenSQL_<version>_rockylinux9.7_<build>.tar.gz \
    ~/Downloads/<your-license>.xml

docker compose -f infra/docker-compose.yml --profile opensql up -d --build opensql
```

The first build takes a while on Apple Silicon: the distribution is x86_64 only,
so every build step and the server itself run under emulation.

Point the application at OpenSQL:

```bash
export AUTORAG_DATABASE_URL=postgresql://autorag:autorag@127.0.0.1:5433/autorag
```

`infra/db/init.sql` is mounted into the container and applied on first start, so
the schema, the `vector` extension, and the HNSW index are created the same way
as for the development database.

## License constraints

`opensql_license` is a preload module, so a license problem prevents the server
from starting. Two fields of the license shape the compose service, and the values
committed here are the ones this demo's license carries — change both to match
yours:

- `identified_by_host` has to equal the container's hostname, so the service sets
  `hostname:` to it.
- `limit_cpu` has to equal the container's CPU allowance, so the service sets
  `cpus:` to it. Without the limit the module reads the host CPU count from the
  cgroup and rejects the license.

The entrypoint compares both values against the container before starting
PostgreSQL and reports a mismatch directly instead of leaving a preload failure
in the server log.

Check the license from inside the container:

```bash
docker compose -f infra/docker-compose.yml exec opensql opensql --version
docker compose -f infra/docker-compose.yml logs opensql | head -20
```

When the license expires, replace `infra/opensql/licenses/license.xml` and
restart the container; no rebuild is needed.

## Vector index settings

An HNSW scan visits a bounded pool of candidates, and this platform filters those
candidates by the collections a caller may read in Outline. On a wiki where one
person can read a small part of what is indexed, enough of the pool can be
filtered away that a query returns fewer than `top_k` results while matching
documents were never visited. It fails quietly: a short result list is
indistinguishable from a narrow query.

pgvector 0.8 added iterative scans for exactly this, and they are off by default.
Every connection this platform opens turns one on:

```
hnsw.iterative_scan = strict_order   # AUTORAG_HNSW_ITERATIVE_SCAN
hnsw.max_scan_tuples                 # AUTORAG_HNSW_MAX_SCAN_TUPLES, 0 leaves pgvector's default
```

`strict_order` keeps results in true distance order; `relaxed_order` is faster
and may return them slightly out of order. Setting the variable to an empty
string leaves the server's own configuration alone, and a pgvector older than 0.8
logs one warning and carries on without it.

This only matters once the index is large enough for the planner to choose it. On
a demo-sized corpus a sequential scan is cheaper, so the plan is an exact
brute-force nearest neighbour search and the setting has nothing to do — which is
also why this problem never appears in a demo.

## High availability

`docs/demo.md` positions OpenSQL as the metadata and vector store, and in a
production deployment the application would connect to the OpenProxy endpoint
(6432) in front of a Patroni cluster. That requires one license per data node
with distinct signatures, so it cannot be demonstrated with a single-host trial
license. The distribution's own installer covers it:

```bash
# inside the extracted distribution, on Rocky Linux 9 hosts
cd opensql-installer
# fill in config/common.env, place one license XML per node in licenses/
python3 opensql_local_installer.py --mode 3node
```

Nothing in the application changes for that topology beyond
`AUTORAG_DATABASE_URL`.
