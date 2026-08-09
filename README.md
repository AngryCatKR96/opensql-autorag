# OpenSQL AutoRAG Sync

Document search over [Tmax OpenSQL](https://www.tmaxtibero.com/) and pgvector, for
content that keeps changing.

Bring documents in — upload them, or point it at an Outline wiki — and the
platform extracts, chunks, embeds, and indexes them. Edit one section of a page
and it re-embeds that section, not the document. Search from the console, from
the REST API, or from any MCP client.

## What makes it more than a pgvector demo

**Delta sync.** A chunk is a section, and a chunk that did not change keeps its
vector. Editing one section of a five-section runbook re-embeds one chunk and
reuses the other four. That is the difference between a wiki you can index and a
wiki you can afford to keep indexed.

**Permissions come from the source.** A wiki has permissions and copying it into
a vector database usually throws them away. Search here resolves each caller's
own Outline access and filters inside the SQL, so a document out of scope cannot
even occupy a result slot. Signing in is Outline OAuth; no wiki credential is
ever typed into this application.

**Retrieval has two arms.** Vector search answers what a passage is about;
full text search answers whether it contains the words asked for. `ERR_HNSW_2481`
is the difference — an embedding blurs it into whatever it resembles. Both run
and are fused by reciprocal rank, and each result says which arm found it.

**Nothing leaves the host.** Embeddings are computed in-process by
`intfloat/multilingual-e5-small`. No document text and no query is sent anywhere.

## Running it

```bash
docker compose -f infra/docker-compose.yml up -d          # PostgreSQL + pgvector
PYTHONPATH=packages/core:services/api .venv/bin/python -m uvicorn opensql_autorag_api.main:app
PYTHONPATH=packages/core:services/api:services/worker .venv/bin/python -m opensql_autorag_worker.main
npm run dev:web
```

Then [docs/demo.md](docs/demo.md) for the full walkthrough, including the run on
real OpenSQL and a disposable Outline instance to develop against.

## Layout

| Path | What it is |
|------|------------|
| `packages/core` | Domain models, chunking, delta planning, embedding providers |
| `services/api` | REST API, retrieval, sessions, Outline access resolution |
| `services/worker` | Extraction, chunking, embedding, index job loop |
| `services/connector` | Outline webhook receiver, backfill, preflight |
| `services/mcp` | MCP server, over the same retrieval path as the API |
| `apps/web` | React console |
| `infra` | Compose stacks, schema, the OpenSQL image, an Outline test instance |

## Documentation

- [docs/demo.md](docs/demo.md) — running it, search modes, chunking, the demo script
- [docs/outline.md](docs/outline.md) — syncing a wiki, permissions, signing in, webhooks
- [docs/opensql.md](docs/opensql.md) — the licensed OpenSQL build, vector index settings, HA
- [docs/superpowers/specs](docs/superpowers/specs) — architecture design

## Tests

```bash
.venv/bin/python -m pytest
```

Anything expressed in SQL is tested against a real database rather than a fake —
the permission filter especially, since a filter that is wrong in a way Python
cannot see is the one worth catching. Those tests skip rather than fail when no
database is reachable.

## Licence

MIT, see [LICENSE](LICENSE).

Dependencies are permissive to match: PDF extraction uses pypdf (BSD) rather
than the more capable PyMuPDF, which is AGPL and would have made the whole work
AGPL. psycopg is LGPL, which places no condition on the code that imports it.

Running against Tmax OpenSQL needs a distribution tarball and a licence from
Tmax; neither is in this repository, and `infra/opensql` builds an image from
artifacts you supply. See [docs/opensql.md](docs/opensql.md).
