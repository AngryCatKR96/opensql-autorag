# Syncing an Outline wiki

`services/connector` keeps an [Outline](https://www.getoutline.com) wiki indexed in
AutoRAG. Outline serves documents as markdown, which the upload path already
accepts, and a wiki is edited continuously — the workload delta sync exists for.

Nothing in the API, worker, or MCP server is Outline-specific: the connector
writes documents and indexing jobs through the same repository they use.

## How documents are identified

Outline document ids are UUIDs and are reused verbatim as AutoRAG document ids,
so there is no mapping table. A re-sync of the same wiki page lands as a new
version of the same document, and delta sync reuses the chunks that did not
change.

`document_sources` records where each document came from: collection, external
URL, the `updatedAt` reported by Outline, and the hash of the body last synced.
Search results carry `source_system` and `source_url`, and the web console links
each hit back to the wiki page.

## Configuration

| Variable | Meaning |
|----------|---------|
| `AUTORAG_OUTLINE_BASE_URL` | Outline instance, e.g. `https://wiki.internal.example.com`. Read by the connector and by the API, which asks it what a caller may read |
| `AUTORAG_OUTLINE_API_KEY` | API key the connector syncs with, sent as a bearer token |
| `AUTORAG_OUTLINE_WEBHOOK_SECRET` | Signing secret of the webhook subscription |
| `AUTORAG_OUTLINE_COLLECTIONS` | Comma separated collection ids to sync. Empty means everything the key can read |
| `AUTORAG_OUTLINE_WEBHOOK_TOLERANCE_SECONDS` | Reject webhooks whose signature is older than this. Default `300`; `0` accepts any age |
| `AUTORAG_OUTLINE_USER_TOKEN` | The MCP server's own Outline token — see [Permissions](#permissions) |
| `AUTORAG_ACCESS_CACHE_SECONDS` | How long a caller's resolved collections are reused. Default `60` |

## Connecting an existing wiki

Pointing this at a wiki people already use, rather than a fresh one, in order.

**1. Check the instance before writing anything.** Every call this makes is a
read, so it is safe against production, and it names the endpoint that failed
rather than reporting a generic error:

```bash
export PYTHONPATH=packages/core:services/api:services/connector
export AUTORAG_OUTLINE_BASE_URL=https://wiki.internal.example.com   # no trailing /api
export AUTORAG_OUTLINE_API_KEY=<the sync key>
.venv/bin/python -m opensql_autorag_connector.backfill --check
```

```
[ok  ] auth.info: authenticated as Dana <dana@example.com> in workspace 'Acme'
[ok  ] collections.list: 4 readable collection(s): Platform, Engineering, ...
[ok  ] sync scope: no collection filter: every collection above would be synced.
[ok  ] documents.list: reachable; newest in scope is 'Index rebuild runbook'
[ok  ] documents.info: fetched 4812 characters of markdown for 'Index rebuild runbook'
```

It exits non-zero on any failure, so it can gate a deploy. An Outline API key
created with scopes only permits the endpoints it lists (`server/models/ApiKey.ts`),
and a 403 here is reported as a scope problem rather than a bad key. The key
needs `auth.info`, `collections.list`, `documents.list`, and `documents.info`.

**2. Decide what gets indexed.** The sync key sees everything its owner can read,
and the backfill copies document bodies into OpenSQL. Set the scope explicitly
rather than syncing the whole wiki:

```bash
.venv/bin/python -m opensql_autorag_connector.backfill --list-collections
export AUTORAG_OUTLINE_COLLECTIONS=<id>,<id>
```

Search enforces per-caller permissions regardless (see [Permissions](#permissions)),
so an over-broad sync is not a leak. It is still a copy of wiki content into
another database, which is usually somebody's decision to make and not a
technical one.

**3. Backfill, then index.** The first run fetches every body in scope, so give
the worker time to catch up before demonstrating search.

**4. Register the webhook, if Outline can reach this platform.** Settings →
Webhooks in Outline, subscribed to the document events, pointing at
`https://<host>/outline/webhook` with a secret matching
`AUTORAG_OUTLINE_WEBHOOK_SECRET`.

This is the step that usually blocks. Outline pushes to the connector, so the
connector needs an address Outline can resolve and connect to:

| Where Outline runs | What is needed |
|--------------------|----------------|
| Self-hosted on the same network | The connector's host and port reachable from the Outline container |
| Outline Cloud (`app.getoutline.com`) | A public HTTPS URL — a tunnel for a demo, an ingress for anything longer-lived |

Without it, nothing breaks: the backfill is the same sync path on a schedule.
A cron entry every few minutes picks up new, changed, and deleted pages, and it
is the safety net for missed webhooks either way. The only cost is that changes
land a cycle late instead of within seconds.

## Permissions

Search honours Outline's permissions by asking Outline. A caller arrives with an
Outline access token of their own, the API resolves it to the collections that
token can read, and the query only reaches chunks of documents in those
collections.

There are two ways a caller arrives with one.

**Signing in (the web console).** The console has a *Sign in with Outline* button;
the caller authorizes this application in Outline and comes back signed in. No
credential is ever typed into or held by the browser. See
[Signing in with Outline](#signing-in-with-outline).

**A token on the request (machine callers).** For curl, scripts, and the MCP
server:

```bash
curl -s localhost:8000/search -H 'Content-Type: application/json' \
  -H "X-Outline-Token: $MY_OUTLINE_TOKEN" \
  -d '{"query": "index rebuild", "top_k": 5}'
```

`Authorization: Bearer <token>` works too, and either takes precedence over a
session cookie — an explicit header is a deliberate act, a cookie is ambient.

Whichever way, the token belongs to the caller, so there is no service credential
here that could be talked into reading more than its owner can. Only the resolved
list of collection ids is cached, keyed by a digest of the token — the token is
never stored by the resolver — and `AUTORAG_ACCESS_CACHE_SECONDS` bounds how long
a revoked membership keeps working.

Why collections are the unit: Outline grants document read access through
collection access, a per-document membership, or being the author of a draft
(`server/policies/document.ts`). Outline's own `documents.list` scopes to
`user.collectionIds()`, so filtering on collection is no more permissive than the
wiki's own document listing. It is less permissive in one direction: a document
shared with a caller individually, outside any collection they can read, is not
returned. That errs toward showing too little.

Two rules follow from that, and both are enforced in the query rather than
applied to its results, so an unreachable document cannot take up one of the
`top_k` slots:

- A document synced from Outline whose collection is unknown — a draft, for
  instance — is reachable by nobody.
- A caller who presents no token is not an anonymous wiki member. They get
  nothing that came from Outline, only documents uploaded straight into AutoRAG.
  If Outline cannot be reached at all, `/search` answers 503 rather than quietly
  serving those uploads, which would read as a wiki with nothing in it.

`list_documents` and `get_chunk_context` are scoped the same way: a title is
content, and a chunk id is not a capability.

The MCP server speaks stdio to one user, so it has no request to carry a token
and reads `AUTORAG_OUTLINE_USER_TOKEN` instead. Unset, it searches only
locally uploaded documents.

## Signing in with Outline

Asking everyone to paste a personal API key works, but it puts a wiki credential
in a browser and leaves each person to manage it. Outline implements OAuth 2.0, so
the console can send people to Outline to sign in instead.

**1. Register the application in Outline.** Settings → Applications. The redirect
URI has to match exactly what this service will send:

```
<AUTORAG_PUBLIC_BASE_URL>/auth/outline/callback
```

`AUTORAG_PUBLIC_BASE_URL` is where a *browser* reaches the API, not where the
process listens. When the console serves the API under `/api` on its own origin —
which is how it runs in development and behind one ingress in production — that is
the console's origin plus `/api`:

```bash
export AUTORAG_PUBLIC_BASE_URL=https://autorag.internal.example.com/api
# redirect URI to register: https://autorag.internal.example.com/api/auth/outline/callback
```

**2. Configure it.**

| Variable | Meaning |
|----------|---------|
| `AUTORAG_OUTLINE_OAUTH_CLIENT_ID` | From the application Outline just created |
| `AUTORAG_OUTLINE_OAUTH_CLIENT_SECRET` | Same |
| `AUTORAG_OUTLINE_OAUTH_SCOPE` | Default `read`. Nothing here writes to the wiki |
| `AUTORAG_PUBLIC_BASE_URL` | Browser-visible base of this API, as above |
| `AUTORAG_SESSION_SECRET` | Encrypts the stored Outline tokens. Required; rotating it signs everybody out |
| `AUTORAG_SESSION_COOKIE_SECURE` | Set to `true` when served over HTTPS |
| `AUTORAG_SESSION_TTL_SECONDS` | How long a session lasts. Default 7 days |

Without all three of client id, secret, and session secret, the console hides the
button and says sign-in is not configured; search still works over uploaded
documents.

**3. How it works.** The endpoint paths are read from Outline's
`/.well-known/oauth-authorization-server` rather than assumed, so a self-hosted
instance behind a reverse proxy reports the origin it is really reachable at.
The flow is authorization code with PKCE (S256) and a required `state`, both held
server side for the round trip — neither the verifier nor the state passes through
the browser as something it could tamper with. A callback whose `state` this
service did not issue, or that has already been used, is refused.

What the browser gets is an opaque, `HttpOnly`, `SameSite=Lax` cookie. What the
database keeps in `oauth_sessions` is that cookie's digest — so a dump cannot be
replayed as a session — and the Outline access and refresh tokens encrypted with
`AUTORAG_SESSION_SECRET`. Access tokens are refreshed a minute before they expire;
Outline rotates the refresh token every time, so the replacement is written back.
Signing out deletes the session here and revokes the token at Outline.

## Backfill

```bash
export PYTHONPATH=packages/core:services/api:services/connector
.venv/bin/python -m opensql_autorag_connector.backfill --list-collections
.venv/bin/python -m opensql_autorag_connector.backfill --collection <collection-id>
```

`documents.list` is walked newest first for metadata only; a document body is
fetched with `documents.info` only when its `updatedAt` differs from the last
sync, its collection changed, or it is currently retired. Re-running the backfill
on an unchanged wiki fetches no bodies and creates no versions. `--force`
re-ingests everything.

A document AutoRAG has from the synced scope that Outline no longer lists in it is
retired — how a sync recovers from a webhook that never arrived. This is skipped
when any document in the run failed, because a listing cut short by an error is
not evidence that anything is gone. `--no-prune` turns it off.

## Webhook

```bash
.venv/bin/python -m uvicorn opensql_autorag_connector.app:app --port 8200
```

Register `https://<host>:8200/outline/webhook` under Settings → Webhooks in
Outline and subscribe to the document events. Requests are authenticated with
the `Outline-Signature` header: HMAC-SHA256 over `{timestamp}.{raw body}`, and an
unsigned or tampered request is rejected with 401.

`timestamp` is the `t` field of that header, in milliseconds — Outline stamps it
with `Date.now()` in `signature()`, `server/models/WebhookSubscription.ts`. A
signature more than `AUTORAG_OUTLINE_WEBHOOK_TOLERANCE_SECONDS` from now is
rejected, which bounds how long a captured request can be replayed. A delivery
Outline retries is signed again, so the window never rejects a legitimate retry.

Outline expects a 200 within 5 seconds, so the endpoint verifies the signature,
decides what the event means, and returns; the work happens afterwards.

Events are routed by the list in `DocumentEvent`, `server/types.ts`:

| Event | Effect |
|-------|--------|
| `delete`, `permanent_delete`, `archive`, `unpublish` | The document is retired. `unpublish` belongs here because it turns the document back into a draft, which only its author may read |
| `restore`, `unarchive`, `publish`, `move`, `update`, anything else under `documents.` | The document is re-fetched and re-indexed |
| `empty_trash` | Ignored; it carries no document id, and each document raised `delete` on its way into the trash |

A retired document keeps its chunks and versions — the page may come back, and its
stored embeddings are then reused — but none of them are active. Retirement is
recorded as `documents.retired_at` rather than by deactivating chunks alone, so an
indexing job that was already queued cannot reactivate them when it completes.
Coming back always goes through indexing, even when the body is byte for byte the
same, because that is what makes the chunks active again.

`documents.move` is why an unchanged body still rewrites the source metadata: a
move changes which collection a document belongs to without touching its text,
and the collection is what search filters permissions on. A document that is no
longer readable when re-fetched is logged and skipped.

## Limits

- **Permissions are enforced per collection, not per document.** A document
  shared with a caller individually, outside any collection they can read, is not
  returned to them. Reaching those would mean resolving each caller's document
  memberships as well; see [Permissions](#permissions) for why this errs toward
  showing too little rather than too much.
- **A revoked membership keeps working for up to
  `AUTORAG_ACCESS_CACHE_SECONDS`.** Set it to `0` to resolve access on every
  query, at the cost of two Outline calls per search.
- **A session outlives an Outline sign-out.** Revoking access in Outline stops the
  next token refresh, but until then the stored access token still works. Shorten
  `AUTORAG_SESSION_TTL_SECONDS` where that matters, or rotate
  `AUTORAG_SESSION_SECRET` to sign everybody out at once.
- **`AUTORAG_SESSION_SECRET` is only as protected as its environment.** The tokens
  in `oauth_sessions` are encrypted with a key derived from it, which defends a
  database dump, not a host where the process environment can be read.
- **Archived collections are unreachable.** `collections.list` omits them, so
  documents synced from a collection that was later archived stay indexed but
  match nobody's scope.
- **Attachments and images are skipped.** Outline embeds them as
  `attachments.redirect` links, so only the markdown text is indexed.
- **Document hierarchy is not captured.** `parentDocumentId` is ignored; chunk
  metadata keeps the heading path within a document only.
