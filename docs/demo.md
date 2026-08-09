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

Embedding runs in the process that needs it — `sentence-transformers` loads the
model once per process and encodes locally, on Apple Silicon through MPS. No
document text and no search query leaves the machine. Each of the API, the
worker, and the MCP server keeps its own copy in memory.

### Query and passage are not the same text

e5 is asymmetric: it is trained with `query:` and `passage:` prefixes, and gives
a different vector for the same words depending on which is claimed. `embed`
takes the role from the caller and has no default, because nothing about a
string reveals what it is for — the worker embeds chunks as `passage`, a search
embeds the query as `query`.

An index built before this used the text's length to choose, so any chunk under
80 words was stored as a query. That mixes both prefixes through one corpus and
compares the halves on different footings. **Existing indexes have to be rebuilt,
and `--force` alone will not do it**: delta sync reuses an embedding by content
hash, so unchanged text keeps its old vector. Drop the vectors first.

```bash
psql "$AUTORAG_DATABASE_URL" -c "DELETE FROM chunk_embeddings e USING embedding_models m
  WHERE m.id = e.embedding_model_id AND m.provider = 'sentence-transformers';"
.venv/bin/python -m opensql_autorag_connector.backfill --force
```

Measured on the demo corpus, the correction changed the ranking of nothing: the
expected chunk was already first for all seven test questions, before and after.
It matters as corpus size grows and as chunks fall on both sides of that 80-word
line, not as a quality win to demonstrate.

Vectors are stored per embedding model, and a search only compares against
vectors from the model that is currently configured — distances between
different models are meaningless. After switching providers the documents are
re-embedded on their next indexing job, since there is nothing to reuse.
`AUTORAG_EMBEDDING_DIMENSION` must match both the model and the
`vector(384)` column, and the worker refuses to start otherwise.

Use the hash provider for the demo run-through and the real model when search
quality is being shown.

A search whose configured model has nothing indexed under it returns an empty
list and says so, both in the API response and in the MCP tool result. That case
looks identical to a query with no matches otherwise, and it is the one a
misconfigured process actually produces.

## Chunking

Documents are split at their headings, so a chunk is a section rather than a
fixed slice. That is what gives search results their section context and what
lets delta sync re-embed one edited section instead of a whole document.

Taken alone it also produces chunks too small to retrieve: a one line section
becomes eight words, and eight words carry too little for a query to tell them
apart — on the demo corpus every such chunk scored within 0.02 of every other. A
section under `min_tokens` (a fifth of `target_tokens`) is therefore folded into
its neighbour, and one at the end folds backwards. Merging is skipped where it
would push the chunk past the target: a one line section beside a long one is
left alone rather than made oversized.

A merged chunk is labelled with the deepest heading its sections share, so a
chunk holding Travel, Equipment and Receipts reads as "Expense policy" rather
than claiming to be any one of them.

The cost is delta granularity. Sections that merge share a chunk, so editing one
re-embeds the others with it — a four section document small enough to become a
single chunk is re-embedded whole. Measured on the demo corpus the merge took 30
chunks to 18 and the median chunk from 42 to 80 words, while an edit to one
section of a longer document still re-embedded exactly one chunk.

Retrieval accuracy is unchanged by it here: the expected chunk ranked first for
all seven test questions both before and after. What it fixed was a question the
corpus previously got wrong, where an eight word section had been competing on
equal terms with everything else.

## Search modes

Retrieval has two arms. The vector arm answers what a passage is about; the
keyword arm answers whether it contains the words asked for. Identifiers, error
strings, and product names are what separate them — an embedding blurs
`ERR_HNSW_2481` into whatever it resembles, and a reader searching a wiki for it
means it literally.

```bash
curl -s localhost:8000/search -H 'Content-Type: application/json' \
  -d '{"query": "ERR_HNSW_2481 index scan stops early", "top_k": 3, "mode": "hybrid"}'
```

| `mode` | What runs |
|--------|-----------|
| `hybrid` (default) | Both arms, fused by reciprocal rank |
| `vector` | Nearest neighbours only |
| `keyword` | Full text only — needs no embedding model, so it answers even when none is loaded |

`AUTORAG_SEARCH_MODE` sets the default; a request overrides it. Under `hybrid`
each result carries `matched_by`, `vector_score`, and `keyword_score`, so it is
visible which arm found it, and the console badges them.

Fusion is on rank rather than score because the arms are not on a comparable
scale: cosine similarity sits near 1 for anything topical, while `ts_rank_cd` is
unbounded. Reciprocal rank fusion needs no calibration and none that changes per
corpus, at the cost of `score` no longer being a similarity — the per-arm scores
are there for that.

The keyword arm combines terms with OR. `websearch_to_tsquery` and
`plainto_tsquery` both require every term, which for a conversational query means
one absent word discards the whole match, and the arm meant to carry the
identifier contributes nothing. `ts_rank_cd` discriminates instead. The cost is
that quoted phrases and `-exclusion` are not honoured.

`AUTORAG_TEXT_SEARCH_CONFIG` selects the text search configuration, default
`english`: it stems English and leaves other scripts as whole tokens, which is
what a mixed English and Korean wiki needs. It must match the configuration the
index in `infra/db/init.sql` was built with; changing one means rebuilding the
other.

## Demo Flow

1. Open the web console.
2. Upload a technical PDF, DOCX, Markdown, or text document.
3. Show that a document version and indexing job are created.
4. Let the worker index the document into OpenSQL pgvector.
5. Search for an OpenSQL or pgvector concept in the console.
6. Upload a revised copy of the same document content as a new version.
7. Show the sync run counts: reused chunks, embedded chunks, retired chunks.
8. Search again and show that the latest source metadata is returned.
9. Search for an identifier rather than a concept — an error code, a hostname, a
   setting name. Each result is badged with the arm that found it; one the
   embedding alone would have blurred comes back badged by both. Re-run it with
   `"mode": "vector"` to show what the keyword arm was contributing.
10. Click *Sign in with Outline* and authorize the application. Search again: wiki
    documents appear, limited to the collections that account can read. Sign in as
    somebody with narrower access and run the same query — the results shrink. See
    [Syncing an Outline wiki](outline.md#permissions).
11. Delete a page in Outline and search once more: it is gone from the results,
    and the console marks it "removed at source".
12. Start the MCP server.

```bash
PYTHONPATH=packages/core:services/api:services/mcp .venv/bin/python -m opensql_autorag_mcp.server
```

13. Connect an MCP client and call `search_documents`. It answers over the same
    retrieval path as the console, scoped to the token the server runs with.

## Positioning

- OpenSQL is the metadata, version, source, session, job, and vector store. Every
  piece of state the platform has lives in it.
- pgvector handles semantic retrieval with `vector(384)` embeddings, alongside
  PostgreSQL full text search; the two are fused rather than chosen between.
- Delta Sync avoids full re-embedding after small document edits, which is what
  makes a continuously edited wiki affordable to keep indexed rather than merely
  possible to index.
- Retrieval honours the permissions of the wiki a document came from, enforced in
  SQL. Indexing an internal wiki does not flatten its access control.
- Embeddings are computed in-process; no document text or query leaves the host.
- MCP exposes the same retrieval capability to AI tools and agents — the same
  code path as the REST API, not a second implementation of it.
