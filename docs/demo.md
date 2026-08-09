# Contest Demo Script

## Local Stack

```bash
docker compose -f infra/docker-compose.yml up -d
PYTHONPATH=packages/core:services/api .venv/bin/python -m uvicorn opensql_autorag_api.main:app --reload
PYTHONPATH=packages/core:services/api:services/worker .venv/bin/python -m opensql_autorag_worker.main
npm run dev:web
```

If the first command fails with `Bind for 0.0.0.0:5432 failed: port is already
allocated`, another PostgreSQL on the machine holds the port. Move this one
rather than stopping that one:

```bash
AUTORAG_DB_PORT=5442 docker compose -f infra/docker-compose.yml up -d
export AUTORAG_DATABASE_URL=postgresql://autorag:autorag@127.0.0.1:5442/autorag
```

The API, the worker, the MCP server, and the connector all read
`AUTORAG_DATABASE_URL`, so exporting it once covers every process.

## Real OpenSQL

The `db` service above is a stand-in for local development. To run the same
stack on Tmax OpenSQL 3.17.8.7 (PostgreSQL 17.8 with pgvector 0.8.1):

```bash
./infra/opensql/stage-artifacts.sh <opensql-package.tar.gz> <license.xml>
docker compose -f infra/docker-compose.yml --profile opensql up -d --build opensql
export AUTORAG_DATABASE_URL=postgresql://autorag:autorag@127.0.0.1:5433/autorag
```

The application is unchanged; only `AUTORAG_DATABASE_URL` differs. In a
high-availability deployment it points at the OpenProxy endpoint instead. See
[opensql.md](opensql.md) for the license constraints and the HA topology.

## Outline wiki

Documents can also be synced from an Outline wiki instead of being uploaded by
hand — see [outline.md](outline.md).

```bash
export PYTHONPATH=packages/core:services/api:services/connector
.venv/bin/python -m opensql_autorag_connector.backfill --collection <collection-id>
.venv/bin/python -m uvicorn opensql_autorag_connector.app:app --port 8200
```

## Embeddings

The API, the worker, and the MCP server all build their provider from the same
settings, so one variable switches every retrieval path:

| `AUTORAG_EMBEDDING_PROVIDER` | Model | Use |
|------------------------------|-------|-----|
| `hash` (default)             | `sha256-deterministic` | Offline, no model download; deterministic vectors with no semantic meaning |
| `sentence-transformers`      | `AUTORAG_EMBEDDING_MODEL`, default `intfloat/multilingual-e5-small` | Real semantic search, 384 dimensions |

```bash
export AUTORAG_EMBEDDING_PROVIDER=sentence-transformers
```

Vectors are stored per embedding model, and a search only compares against
vectors from the model that is currently configured — distances between
different models are meaningless. After switching providers the documents are
re-embedded on their next indexing job, since there is nothing to reuse.
`AUTORAG_EMBEDDING_DIMENSION` must match both the model and the
`vector(384)` column, and the worker refuses to start otherwise.

Use the hash provider for the demo run-through and the real model when search
quality is being shown.

## Demo Flow

1. Open the web console.
2. Upload a technical PDF, DOCX, Markdown, or text document.
3. Show that a document version and indexing job are created.
4. Let the worker index the document into OpenSQL pgvector.
5. Search for an OpenSQL or pgvector concept in the console.
6. Upload a revised copy of the same document content as a new version.
7. Show the sync run counts: reused chunks, embedded chunks, retired chunks.
8. Search again and show that the latest source metadata is returned.
9. Click *Sign in with Outline* and authorize the application. Search again: wiki
   documents appear, limited to the collections that account can read. Sign in as
   somebody with narrower access and run the same query — the results shrink. See
   [Syncing an Outline wiki](outline.md#permissions).
10. Delete a page in Outline and search once more: it is gone from the results,
    and the console marks it "removed at source".
11. Start the MCP server.

```bash
PYTHONPATH=packages/core:services/api:services/mcp .venv/bin/python -m opensql_autorag_mcp.server
```

12. Connect an MCP client and call `search_documents`.

## Positioning

- OpenSQL is the metadata, version, job, and vector store.
- pgvector handles semantic retrieval with `vector(384)` embeddings.
- Delta Sync avoids full re-embedding after small document edits.
- MCP exposes the same retrieval capability to AI tools and agents.
