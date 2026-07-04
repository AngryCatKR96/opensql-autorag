# Contest Demo Script

## Local Stack

```bash
docker compose -f infra/docker-compose.yml up -d
PYTHONPATH=packages/core:services/api .venv/bin/python -m uvicorn opensql_autorag_api.main:app --reload
PYTHONPATH=packages/core:services/api:services/worker .venv/bin/python -m opensql_autorag_worker.main
npm run dev:web
```

For an OpenSQL environment, keep the application unchanged and set `AUTORAG_DATABASE_URL` to the OpenSQL connection endpoint. In a high-availability deployment this can point at the OpenSQL proxy endpoint.

## Demo Flow

1. Open the web console.
2. Upload a technical PDF, DOCX, Markdown, or text document.
3. Show that a document version and indexing job are created.
4. Let the worker index the document into OpenSQL pgvector.
5. Search for an OpenSQL or pgvector concept in the console.
6. Upload a revised copy of the same document content as a new version.
7. Show the sync run counts: reused chunks, embedded chunks, retired chunks.
8. Search again and show that the latest source metadata is returned.
9. Start the MCP server.

```bash
PYTHONPATH=packages/core:services/api:services/mcp .venv/bin/python -m opensql_autorag_mcp.server
```

10. Connect an MCP client and call `search_documents`.

## Positioning

- OpenSQL is the metadata, version, job, and vector store.
- pgvector handles semantic retrieval with `vector(384)` embeddings.
- Delta Sync avoids full re-embedding after small document edits.
- MCP exposes the same retrieval capability to AI tools and agents.
