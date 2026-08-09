CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS embedding_models (
    id BIGSERIAL PRIMARY KEY,
    provider TEXT NOT NULL,
    model_name TEXT NOT NULL,
    dimension INTEGER NOT NULL,
    distance_metric TEXT NOT NULL DEFAULT 'cosine',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (provider, model_name)
);

-- Selected with AUTORAG_EMBEDDING_PROVIDER; the services register the row they
-- use on startup, so this seed only documents the expected models.
INSERT INTO embedding_models (provider, model_name, dimension, distance_metric)
VALUES
    ('sentence-transformers', 'intfloat/multilingual-e5-small', 384, 'cosine'),
    ('hash', 'sha256-deterministic', 384, 'cosine')
ON CONFLICT (provider, model_name) DO NOTHING;

CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY,
    title TEXT NOT NULL,
    source_type TEXT NOT NULL,
    current_version_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Set when the document was removed at its source. Retired documents keep
    -- their chunks for audit but none of them are active, and an indexing job
    -- that completes afterwards must not reactivate them.
    retired_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS document_versions (
    id UUID PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES documents(id),
    file_hash TEXT NOT NULL,
    extracted_text_hash TEXT,
    status TEXT NOT NULL,
    source_path TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE documents
    ADD CONSTRAINT documents_current_version_fk
    FOREIGN KEY (current_version_id) REFERENCES document_versions(id);

-- Where a document came from, when it is synced from an external system such as
-- an Outline wiki. Documents uploaded through the API have no row here.
CREATE TABLE IF NOT EXISTS document_sources (
    document_id UUID PRIMARY KEY REFERENCES documents(id),
    source_system TEXT NOT NULL,
    external_id TEXT NOT NULL,
    external_url TEXT,
    external_updated_at TIMESTAMPTZ,
    collection_id TEXT,
    last_file_hash TEXT,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_system, external_id)
);

CREATE TABLE IF NOT EXISTS document_chunks (
    id UUID PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES documents(id),
    version_id UUID NOT NULL REFERENCES document_versions(id),
    stable_key TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    heading_path TEXT NOT NULL,
    page_start INTEGER,
    page_end INTEGER,
    token_estimate INTEGER NOT NULL,
    active BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (version_id, stable_key)
);

-- The keyword arm of retrieval. An expression index rather than a stored
-- tsvector column, so adding it to a populated database does not rewrite the
-- table. The configuration is fixed here because an index and the query using it
-- must agree on one: `english` stems English and leaves other scripts as whole
-- tokens, which is what a mixed English and Korean wiki needs. Changing
-- AUTORAG_TEXT_SEARCH_CONFIG means rebuilding this index to match.
CREATE INDEX IF NOT EXISTS document_chunks_text_idx
    ON document_chunks USING gin (to_tsvector('english', text));

CREATE TABLE IF NOT EXISTS chunk_embeddings (
    chunk_id UUID PRIMARY KEY REFERENCES document_chunks(id),
    embedding_model_id BIGINT NOT NULL REFERENCES embedding_models(id),
    embedding vector(384) NOT NULL,
    vector_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS chunk_embeddings_vector_idx
    ON chunk_embeddings USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS index_jobs (
    id UUID PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES documents(id),
    version_id UUID NOT NULL REFERENCES document_versions(id),
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id UUID PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES documents(id),
    version_id UUID NOT NULL REFERENCES document_versions(id),
    reused_count INTEGER NOT NULL,
    embedded_count INTEGER NOT NULL,
    retired_count INTEGER NOT NULL,
    failed_count INTEGER NOT NULL DEFAULT 0,
    elapsed_ms INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One in-flight OAuth login. Holds the CSRF state and the PKCE verifier between
-- the redirect to Outline and the callback, so neither has to travel through the
-- browser. Rows are consumed on callback and are useless once expired.
CREATE TABLE IF NOT EXISTS oauth_logins (
    state TEXT PRIMARY KEY,
    code_verifier TEXT NOT NULL,
    redirect_after TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);

-- A signed-in caller. The session cookie is stored as a digest so a leaked dump
-- cannot be replayed as a session, and the Outline tokens are encrypted with
-- AUTORAG_SESSION_SECRET rather than kept as plain text.
CREATE TABLE IF NOT EXISTS oauth_sessions (
    id TEXT PRIMARY KEY,
    outline_user_id TEXT NOT NULL,
    outline_user_name TEXT NOT NULL DEFAULT '',
    access_token_encrypted BYTEA NOT NULL,
    refresh_token_encrypted BYTEA,
    access_token_expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS oauth_sessions_expires_idx ON oauth_sessions (expires_at);

CREATE TABLE IF NOT EXISTS query_logs (
    id UUID PRIMARY KEY,
    query TEXT NOT NULL,
    filters JSONB NOT NULL DEFAULT '{}'::jsonb,
    result_count INTEGER NOT NULL,
    latency_ms INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
