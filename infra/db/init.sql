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

INSERT INTO embedding_models (provider, model_name, dimension, distance_metric)
VALUES ('sentence-transformers', 'intfloat/multilingual-e5-small', 384, 'cosine')
ON CONFLICT (provider, model_name) DO NOTHING;

CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY,
    title TEXT NOT NULL,
    source_type TEXT NOT NULL,
    current_version_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
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

CREATE TABLE IF NOT EXISTS query_logs (
    id UUID PRIMARY KEY,
    query TEXT NOT NULL,
    filters JSONB NOT NULL DEFAULT '{}'::jsonb,
    result_count INTEGER NOT NULL,
    latency_ms INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
