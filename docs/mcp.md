# Attaching an agent

The MCP server puts AutoRAG's retrieval in front of a coding agent — Codex,
Claude Code, or any other MCP client — so a question about a planning document
can be answered from the wiki instead of from the model's guess.

It is meant to run on your own machine, launched by your agent, under your own
Outline account.

## What it is, and what it is not

The server is a translator. It turns a tool call into an HTTP request to the
AutoRAG API and hands the answer back unchanged. The API does the work: it
resolves your Outline token into the set of collections you can read, applies
that filter inside the SQL, and embeds the query.

That split is the reason this is safe to hand around a team:

- **No database credential leaves the server.** Every developer running one of
  these would otherwise need to reach OpenSQL directly, and the permission
  filter is something the application puts into each query. A laptop with
  database access could simply write a query without it.
- **No model is downloaded onto your machine.** Embedding happens in the API, so
  installing this needs `mcp` and `httpx`, not PyTorch.
- **No way to configure a wrong answer.** A local embedding model that does not
  match the one the corpus was indexed with returns zero matches, which reads
  exactly like a subject nobody has written about. There is no local model to
  get wrong.

It also means the API has to be reachable from wherever you run this.

## Setup

Two settings, both read from the environment:

```bash
export AUTORAG_API_BASE_URL=http://autorag.internal:8000   # where the API runs
export AUTORAG_OUTLINE_USER_TOKEN=ol_api_...               # your own token
```

The token is a personal API token from Outline, under **Settings → API tokens**.
It is yours, not the platform's: what the agent can find is exactly what you
could find by searching Outline yourself. Revoking it there ends this access too.

Leaving the token unset is a working configuration rather than an error — the
server then reaches only documents uploaded straight into AutoRAG, and no wiki
content at all.

### Codex

In `~/.codex/config.toml`:

```toml
[mcp_servers.autorag]
command = "/path/to/opensql_proejct/.venv/bin/python"
args = ["-m", "opensql_autorag_mcp.server"]

[mcp_servers.autorag.env]
PYTHONPATH = "/path/to/opensql_proejct/services/api:/path/to/opensql_proejct/services/mcp"
AUTORAG_API_BASE_URL = "http://autorag.internal:8000"
AUTORAG_OUTLINE_USER_TOKEN = "ol_api_..."
```

### Claude Code

```bash
claude mcp add autorag \
  --env PYTHONPATH=services/api:services/mcp \
  --env AUTORAG_API_BASE_URL=http://autorag.internal:8000 \
  --env AUTORAG_OUTLINE_USER_TOKEN=ol_api_... \
  -- .venv/bin/python -m opensql_autorag_mcp.server
```

## The tools

| Tool | What it answers |
|------|-----------------|
| `search_documents` | The passages that answer a question. `mode` is `hybrid` (default), `vector` for meaning alone, or `keyword` for literal wording — the one to reach for when the query is an error code or a setting name |
| `get_chunk_context` | The sections either side of a hit, for reading around it |
| `list_documents` | Every indexed document you can read |
| `get_sync_status` | What the last indexing run did to one document |

Every one of them is scoped to your token. A document you cannot read in Outline
cannot occupy a result slot, and a chunk id from a colleague's search does not
fetch its text for you.

## When something is wrong

The server raises rather than returning an empty list, because an agent shown
zero results will answer from its own guess and has no server log to check.

| Message | What to do |
|---------|------------|
| `could not be reached` | The API is down or `AUTORAG_API_BASE_URL` is wrong |
| `Outline rejected the configured token` | Issue a new personal API token |
| `The API could not reach Outline` | Not an empty wiki — your access is unknown, so nothing was searched |

A `warning` field on a search result is worth reading out: it reports a query run
against an embedding model that has nothing indexed under it, which otherwise
looks identical to a question nobody has written about.
