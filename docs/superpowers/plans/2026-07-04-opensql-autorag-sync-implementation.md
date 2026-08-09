# OpenSQL AutoRAG Sync Implementation Plan

> **Completed, and kept as a record.** These fifteen tasks were the original
> build and are all done. Work since then — the Outline connector, permission
> filtering, sign-in, hybrid retrieval, and the chunking changes — is not in
> here, and several details below have been superseded.
>
> For what the system is now, read
> `docs/superpowers/specs/2026-07-04-opensql-autorag-sync-design.md`, whose
> section 20 lists where this plan and the built system differ. Do not follow
> this document as instructions.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a working product demo where users upload documents, OpenSQL stores versioned pgvector embeddings, changed chunks are re-embedded selectively, and MCP clients can search the latest indexed content.

**Architecture:** A monorepo with a Python core package shared by FastAPI, a worker, and an MCP server, plus a React/Vite web console. OpenSQL is the source of truth for documents, versions, chunks, embeddings, jobs, sync runs, and query logs. A deterministic hash embedding provider is used for tests and offline smoke runs; the production provider wraps `intfloat/multilingual-e5-small` with 384-dimensional vectors.

**Tech Stack:** Python, FastAPI, psycopg, pgvector/OpenSQL, PyMuPDF, python-docx, MCP Python SDK, pytest, React, Vite, TypeScript, Docker Compose.

---

## File Structure

- `pyproject.toml`: Python project metadata, pytest config, ruff config.
- `requirements.txt`: Python runtime and test dependencies.
- `package.json`: root scripts for web commands.
- `apps/web/package.json`: web app dependencies and scripts.
- `apps/web/tsconfig.json`: TypeScript compiler options.
- `apps/web/vite.config.ts`: Vite React config and API proxy.
- `apps/web/index.html`: Vite entry HTML.
- `apps/web/src/main.tsx`: React app entry.
- `apps/web/src/App.tsx`: product console screens and API calls.
- `apps/web/src/styles.css`: console layout and visual styling.
- `packages/core/opensql_autorag/domain.py`: shared dataclasses and enums.
- `packages/core/opensql_autorag/hash_utils.py`: normalization and hashing helpers.
- `packages/core/opensql_autorag/chunking.py`: semantic chunk planner.
- `packages/core/opensql_autorag/delta.py`: changed-chunk synchronization planner.
- `packages/core/opensql_autorag/embeddings.py`: embedding provider interface and implementations.
- `packages/core/opensql_autorag/retrieval.py`: retrieval query/result model and scoring helpers.
- `services/api/opensql_autorag_api/settings.py`: environment configuration.
- `services/api/opensql_autorag_api/db.py`: database connection helper.
- `services/api/opensql_autorag_api/repository.py`: OpenSQL persistence methods.
- `services/api/opensql_autorag_api/schemas.py`: REST request and response models.
- `services/api/opensql_autorag_api/main.py`: FastAPI routes.
- `services/worker/opensql_autorag_worker/extractors.py`: PDF, DOCX, Markdown, and text extraction.
- `services/worker/opensql_autorag_worker/processor.py`: indexing job processor.
- `services/worker/opensql_autorag_worker/main.py`: worker loop.
- `services/mcp/opensql_autorag_mcp/server.py`: MCP tool server.
- `infra/db/init.sql`: OpenSQL/pgvector schema.
- `infra/docker-compose.yml`: local OpenSQL-compatible development stack.
- `docs/demo.md`: contest demo script.
- `tests/core/test_hash_utils.py`: hash helper tests.
- `tests/core/test_chunking.py`: chunker tests.
- `tests/core/test_delta.py`: delta planner tests.
- `tests/core/test_embeddings.py`: embedding validation tests.
- `tests/api/test_schemas.py`: API schema tests.
- `tests/worker/test_extractors.py`: extraction tests.
- `tests/worker/test_processor.py`: delta-sync processor tests.
- `tests/mcp/test_contract.py`: MCP tool contract tests.

## Task 1: Repository Scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `requirements.txt`
- Create: `package.json`
- Create: `apps/web/package.json`
- Create: `apps/web/tsconfig.json`
- Create: `apps/web/vite.config.ts`
- Create: `apps/web/index.html`
- Create: `packages/core/opensql_autorag/__init__.py`
- Create: `services/api/opensql_autorag_api/__init__.py`
- Create: `services/worker/opensql_autorag_worker/__init__.py`
- Create: `services/mcp/opensql_autorag_mcp/__init__.py`

- [ ] **Step 1: Create Python project configuration**

Write `pyproject.toml`:

```toml
[project]
name = "opensql-autorag-sync"
version = "0.1.0"
description = "OpenSQL based AutoRAG document indexing and delta sync demo"
requires-python = ">=3.11"

[tool.pytest.ini_options]
pythonpath = ["packages/core", "services/api", "services/worker", "services/mcp"]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]
```

- [ ] **Step 2: Create Python dependency list**

Write `requirements.txt`:

```text
fastapi==0.115.6
uvicorn[standard]==0.34.0
python-multipart==0.0.20
pydantic==2.10.4
pydantic-settings==2.7.1
psycopg[binary]==3.2.3
numpy==2.2.1
pymupdf==1.25.1
python-docx==1.1.2
sentence-transformers==3.3.1
mcp==1.2.0
pytest==8.3.4
httpx==0.28.1
ruff==0.8.4
```

- [ ] **Step 3: Create root web script manifest**

Write `package.json`:

```json
{
  "name": "opensql-autorag-sync",
  "private": true,
  "scripts": {
    "dev:web": "npm --prefix apps/web run dev",
    "build:web": "npm --prefix apps/web run build",
    "test:web": "npm --prefix apps/web run test"
  }
}
```

- [ ] **Step 4: Create Vite web manifest**

Write `apps/web/package.json`:

```json
{
  "name": "opensql-autorag-web",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite --host 127.0.0.1",
    "build": "tsc -b && vite build",
    "test": "tsc --noEmit"
  },
  "dependencies": {
    "@vitejs/plugin-react": "latest",
    "vite": "latest",
    "typescript": "latest",
    "react": "latest",
    "react-dom": "latest",
    "lucide-react": "latest"
  },
  "devDependencies": {}
}
```

- [ ] **Step 5: Create TypeScript config**

Write `apps/web/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["DOM", "DOM.Iterable", "ES2020"],
    "allowJs": false,
    "skipLibCheck": true,
    "esModuleInterop": true,
    "allowSyntheticDefaultImports": true,
    "strict": true,
    "forceConsistentCasingInFileNames": true,
    "module": "ESNext",
    "moduleResolution": "Node",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx"
  },
  "include": ["src"]
}
```

- [ ] **Step 6: Create Vite config with API proxy**

Write `apps/web/vite.config.ts`:

```ts
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, "")
      }
    }
  }
});
```

- [ ] **Step 7: Create Vite entry file**

Write `apps/web/index.html`:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>OpenSQL AutoRAG Sync</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 8: Create package markers**

Create empty `__init__.py` files in:

```text
packages/core/opensql_autorag/__init__.py
services/api/opensql_autorag_api/__init__.py
services/worker/opensql_autorag_worker/__init__.py
services/mcp/opensql_autorag_mcp/__init__.py
```

- [ ] **Step 9: Verify scaffold files exist**

Run:

```bash
python -m compileall packages services
```

Expected:

```text
Listing 'packages'...
Listing 'services'...
```

- [ ] **Step 10: Commit**

```bash
git add pyproject.toml requirements.txt package.json apps/web/package.json apps/web/tsconfig.json apps/web/vite.config.ts apps/web/index.html packages services
git commit -m "chore: scaffold OpenSQL AutoRAG project"
```

## Task 2: Core Domain Models and Hash Helpers

**Files:**
- Create: `packages/core/opensql_autorag/domain.py`
- Create: `packages/core/opensql_autorag/hash_utils.py`
- Create: `tests/core/test_hash_utils.py`

- [ ] **Step 1: Write failing hash helper tests**

Write `tests/core/test_hash_utils.py`:

```python
from opensql_autorag.hash_utils import content_hash, normalize_text, stable_key


def test_normalize_text_collapses_whitespace():
    assert normalize_text("OpenSQL\n\n  pgvector\t검색") == "OpenSQL pgvector 검색"


def test_content_hash_is_stable_for_formatting_noise():
    assert content_hash("A\n\nB") == content_hash("A B")


def test_stable_key_includes_document_heading_and_index():
    key = stable_key("doc-1", ("Chapter 1", "Vector"), 3, "hello")
    assert key == stable_key("doc-1", ("Chapter 1", "Vector"), 3, "hello")
    assert key != stable_key("doc-1", ("Chapter 1", "Vector"), 4, "hello")
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
pytest tests/core/test_hash_utils.py -v
```

Expected: FAIL with `ModuleNotFoundError` or missing function errors.

- [ ] **Step 3: Implement domain models**

Write `packages/core/opensql_autorag/domain.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ChunkDecision(StrEnum):
    REUSE = "reuse"
    EMBED = "embed"
    RETIRE = "retire"


@dataclass(frozen=True)
class SourceLocation:
    page_start: int | None = None
    page_end: int | None = None
    heading_path: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class TextBlock:
    text: str
    location: SourceLocation
    block_index: int


@dataclass(frozen=True)
class Chunk:
    stable_key: str
    text: str
    content_hash: str
    chunk_index: int
    location: SourceLocation
    token_estimate: int


@dataclass(frozen=True)
class PlannedChunk:
    chunk: Chunk
    decision: ChunkDecision
    previous_stable_key: str | None = None


@dataclass(frozen=True)
class DeltaPlan:
    chunks: tuple[PlannedChunk, ...]

    @property
    def reused_count(self) -> int:
        return sum(1 for item in self.chunks if item.decision == ChunkDecision.REUSE)

    @property
    def embedded_count(self) -> int:
        return sum(1 for item in self.chunks if item.decision == ChunkDecision.EMBED)

    @property
    def retired_count(self) -> int:
        return sum(1 for item in self.chunks if item.decision == ChunkDecision.RETIRE)
```

- [ ] **Step 4: Implement hash helpers**

Write `packages/core/opensql_autorag/hash_utils.py`:

```python
from __future__ import annotations

import hashlib
import re

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip()


def content_hash(text: str) -> str:
    normalized = normalize_text(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def stable_key(document_id: str, heading_path: tuple[str, ...], index: int, text: str) -> str:
    heading = "/".join(normalize_text(part) for part in heading_path)
    payload = f"{document_id}|{heading}|{index}|{content_hash(text)[:16]}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

- [ ] **Step 5: Run tests to verify pass**

Run:

```bash
pytest tests/core/test_hash_utils.py -v
```

Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add packages/core/opensql_autorag/domain.py packages/core/opensql_autorag/hash_utils.py tests/core/test_hash_utils.py
git commit -m "feat: add core document hash helpers"
```

## Task 3: Semantic Chunker

**Files:**
- Create: `packages/core/opensql_autorag/chunking.py`
- Create: `tests/core/test_chunking.py`

- [ ] **Step 1: Write failing chunker tests**

Write `tests/core/test_chunking.py`:

```python
from opensql_autorag.chunking import SemanticChunker
from opensql_autorag.domain import SourceLocation, TextBlock


def test_chunker_preserves_heading_path_and_source_location():
    blocks = [
        TextBlock("OpenSQL overview", SourceLocation(1, 1, ("Intro",)), 0),
        TextBlock("pgvector stores embeddings for semantic search.", SourceLocation(1, 1, ("Intro",)), 1),
    ]

    chunks = SemanticChunker(target_tokens=20, overlap_tokens=4).chunk("doc-1", blocks)

    assert len(chunks) == 1
    assert chunks[0].location.heading_path == ("Intro",)
    assert chunks[0].location.page_start == 1
    assert "pgvector" in chunks[0].text


def test_chunker_splits_large_sections_deterministically():
    text = " ".join(f"token{i}" for i in range(45))
    blocks = [TextBlock(text, SourceLocation(2, 2, ("Long",)), 0)]

    chunks = SemanticChunker(target_tokens=15, overlap_tokens=3).chunk("doc-1", blocks)

    assert [chunk.chunk_index for chunk in chunks] == [0, 1, 2, 3]
    assert chunks[0].stable_key != chunks[1].stable_key
    assert chunks[1].text.startswith("token12")
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
pytest tests/core/test_chunking.py -v
```

Expected: FAIL with missing `opensql_autorag.chunking`.

- [ ] **Step 3: Implement semantic chunker**

Write `packages/core/opensql_autorag/chunking.py`:

```python
from __future__ import annotations

from collections.abc import Iterable

from opensql_autorag.domain import Chunk, SourceLocation, TextBlock
from opensql_autorag.hash_utils import content_hash, normalize_text, stable_key


class SemanticChunker:
    def __init__(self, target_tokens: int = 220, overlap_tokens: int = 30) -> None:
        if target_tokens <= 0:
            raise ValueError("target_tokens must be positive")
        if overlap_tokens < 0 or overlap_tokens >= target_tokens:
            raise ValueError("overlap_tokens must be non-negative and smaller than target_tokens")
        self.target_tokens = target_tokens
        self.overlap_tokens = overlap_tokens

    def chunk(self, document_id: str, blocks: Iterable[TextBlock]) -> tuple[Chunk, ...]:
        chunks: list[Chunk] = []
        buffer_words: list[str] = []
        buffer_location: SourceLocation | None = None

        for block in blocks:
            words = normalize_text(block.text).split()
            if not words:
                continue
            if buffer_location is None:
                buffer_location = block.location
            if buffer_words and (
                len(buffer_words) + len(words) > self.target_tokens
                or block.location.heading_path != buffer_location.heading_path
            ):
                self._flush(document_id, chunks, buffer_words, buffer_location)
                buffer_words = buffer_words[-self.overlap_tokens :] if self.overlap_tokens else []
                buffer_location = block.location
            buffer_words.extend(words)

            while len(buffer_words) >= self.target_tokens:
                window = buffer_words[: self.target_tokens]
                self._flush(document_id, chunks, window, buffer_location)
                buffer_words = buffer_words[self.target_tokens - self.overlap_tokens :]

        if buffer_words and buffer_location is not None:
            self._flush(document_id, chunks, buffer_words, buffer_location)

        return tuple(chunks)

    def _flush(
        self,
        document_id: str,
        chunks: list[Chunk],
        words: list[str],
        location: SourceLocation,
    ) -> None:
        text = " ".join(words)
        index = len(chunks)
        chunks.append(
            Chunk(
                stable_key=stable_key(document_id, location.heading_path, index, text),
                text=text,
                content_hash=content_hash(text),
                chunk_index=index,
                location=location,
                token_estimate=len(words),
            )
        )
```

- [ ] **Step 4: Run tests to verify pass**

Run:

```bash
pytest tests/core/test_chunking.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/core/opensql_autorag/chunking.py tests/core/test_chunking.py
git commit -m "feat: add semantic document chunker"
```

## Task 4: Delta Sync Planner

**Files:**
- Create: `packages/core/opensql_autorag/delta.py`
- Create: `tests/core/test_delta.py`

- [ ] **Step 1: Write failing delta planner tests**

Write `tests/core/test_delta.py`:

```python
from opensql_autorag.delta import DeltaPlanner
from opensql_autorag.domain import Chunk, ChunkDecision, SourceLocation
from opensql_autorag.hash_utils import content_hash, stable_key


def make_chunk(index: int, text: str) -> Chunk:
    location = SourceLocation(1, 1, ("Guide",))
    return Chunk(
        stable_key=stable_key("doc-1", location.heading_path, index, text),
        text=text,
        content_hash=content_hash(text),
        chunk_index=index,
        location=location,
        token_estimate=len(text.split()),
    )


def test_delta_reuses_unchanged_chunks():
    previous = (make_chunk(0, "same content"),)
    current = (make_chunk(0, "same content"),)

    plan = DeltaPlanner().plan(previous, current)

    assert plan.reused_count == 1
    assert plan.embedded_count == 0
    assert plan.chunks[0].decision == ChunkDecision.REUSE


def test_delta_embeds_changed_and_retires_missing_chunks():
    previous = (make_chunk(0, "old content"), make_chunk(1, "removed content"))
    current = (make_chunk(0, "new content"),)

    plan = DeltaPlanner().plan(previous, current)

    assert plan.embedded_count == 1
    assert plan.retired_count == 1
    assert [item.decision for item in plan.chunks] == [
        ChunkDecision.EMBED,
        ChunkDecision.RETIRE,
    ]
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
pytest tests/core/test_delta.py -v
```

Expected: FAIL with missing `opensql_autorag.delta`.

- [ ] **Step 3: Implement delta planner**

Write `packages/core/opensql_autorag/delta.py`:

```python
from __future__ import annotations

from opensql_autorag.domain import Chunk, ChunkDecision, DeltaPlan, PlannedChunk


class DeltaPlanner:
    def plan(self, previous: tuple[Chunk, ...], current: tuple[Chunk, ...]) -> DeltaPlan:
        previous_by_hash = {chunk.content_hash: chunk for chunk in previous}
        current_hashes = {chunk.content_hash for chunk in current}
        planned: list[PlannedChunk] = []

        for chunk in current:
            previous_chunk = previous_by_hash.get(chunk.content_hash)
            if previous_chunk is not None:
                planned.append(
                    PlannedChunk(
                        chunk=chunk,
                        decision=ChunkDecision.REUSE,
                        previous_stable_key=previous_chunk.stable_key,
                    )
                )
            else:
                planned.append(PlannedChunk(chunk=chunk, decision=ChunkDecision.EMBED))

        for chunk in previous:
            if chunk.content_hash not in current_hashes:
                planned.append(PlannedChunk(chunk=chunk, decision=ChunkDecision.RETIRE))

        return DeltaPlan(chunks=tuple(planned))
```

- [ ] **Step 4: Run tests to verify pass**

Run:

```bash
pytest tests/core/test_delta.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/core/opensql_autorag/delta.py tests/core/test_delta.py
git commit -m "feat: add changed chunk delta planner"
```

## Task 5: Embedding Providers and Dimension Validation

**Files:**
- Create: `packages/core/opensql_autorag/embeddings.py`
- Create: `tests/core/test_embeddings.py`

- [ ] **Step 1: Write failing embedding tests**

Write `tests/core/test_embeddings.py`:

```python
import pytest

from opensql_autorag.embeddings import HashEmbeddingProvider, validate_dimension


def test_hash_embedding_provider_returns_configured_dimension():
    provider = HashEmbeddingProvider(dimension=384)

    vector = provider.embed("OpenSQL pgvector")

    assert len(vector) == 384
    assert all(isinstance(value, float) for value in vector)


def test_validate_dimension_rejects_mismatch():
    with pytest.raises(ValueError, match="Embedding dimension mismatch"):
        validate_dimension(configured=384, observed=1024, column=384)
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
pytest tests/core/test_embeddings.py -v
```

Expected: FAIL with missing `opensql_autorag.embeddings`.

- [ ] **Step 3: Implement embedding providers**

Write `packages/core/opensql_autorag/embeddings.py`:

```python
from __future__ import annotations

import hashlib
from typing import Protocol

import numpy as np


class EmbeddingProvider(Protocol):
    dimension: int

    def embed(self, text: str) -> list[float]:
        raise NotImplementedError


class HashEmbeddingProvider:
    def __init__(self, dimension: int = 384) -> None:
        self.dimension = dimension

    def embed(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        seed = int.from_bytes(digest[:8], "big", signed=False)
        rng = np.random.default_rng(seed)
        vector = rng.normal(size=self.dimension)
        norm = np.linalg.norm(vector)
        return (vector / norm).astype(float).tolist()


class SentenceTransformerEmbeddingProvider:
    def __init__(self, model_name: str = "intfloat/multilingual-e5-small") -> None:
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name
        self.model = SentenceTransformer(model_name)
        self.dimension = int(self.model.get_sentence_embedding_dimension())

    def embed(self, text: str) -> list[float]:
        prefixed = f"query: {text}" if len(text.split()) < 80 else f"passage: {text}"
        vector = self.model.encode(prefixed, normalize_embeddings=True)
        return [float(value) for value in vector]


def validate_dimension(configured: int, observed: int, column: int) -> None:
    if configured != observed or observed != column:
        raise ValueError(
            "Embedding dimension mismatch: "
            f"configured={configured}, observed={observed}, column={column}"
        )
```

- [ ] **Step 4: Run tests to verify pass**

Run:

```bash
pytest tests/core/test_embeddings.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/core/opensql_autorag/embeddings.py tests/core/test_embeddings.py
git commit -m "feat: add embedding providers"
```

## Task 6: OpenSQL Schema

**Files:**
- Create: `infra/db/init.sql`

- [ ] **Step 1: Write schema file**

Write `infra/db/init.sql`:

```sql
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
```

- [ ] **Step 2: Verify schema mentions vector dimension**

Run:

```bash
grep -n "vector(384)" infra/db/init.sql
```

Expected:

```text
chunk_embeddings embedding vector(384) NOT NULL
```

- [ ] **Step 3: Commit**

```bash
git add infra/db/init.sql
git commit -m "feat: add OpenSQL pgvector schema"
```

## Task 7: API Settings, Database Helper, and Repository

**Files:**
- Create: `services/api/opensql_autorag_api/settings.py`
- Create: `services/api/opensql_autorag_api/db.py`
- Create: `services/api/opensql_autorag_api/repository.py`

- [ ] **Step 1: Implement settings**

Write `services/api/opensql_autorag_api/settings.py`:

```python
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTORAG_", env_file=".env")

    database_url: str = "postgresql://autorag:autorag@127.0.0.1:5432/autorag"
    storage_dir: Path = Path("data/documents")
    embedding_provider: str = "hash"
    embedding_model: str = "intfloat/multilingual-e5-small"
    embedding_dimension: int = 384


settings = Settings()
```

- [ ] **Step 2: Implement database helper**

Write `services/api/opensql_autorag_api/db.py`:

```python
from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator

import psycopg
from psycopg.rows import dict_row

from opensql_autorag_api.settings import settings


@contextmanager
def get_connection() -> Iterator[psycopg.Connection]:
    with psycopg.connect(settings.database_url, row_factory=dict_row) as connection:
        yield connection
```

- [ ] **Step 3: Implement repository skeleton**

Write `services/api/opensql_autorag_api/repository.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from psycopg import Connection


@dataclass(frozen=True)
class CreatedVersion:
    document_id: UUID
    version_id: UUID
    job_id: UUID


class Repository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def create_document_version(
        self,
        title: str,
        source_type: str,
        source_path: str,
        file_hash: str,
        document_id: UUID | None = None,
    ) -> CreatedVersion:
        doc_id = document_id or uuid4()
        version_id = uuid4()
        job_id = uuid4()
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO documents (id, title, source_type)
                VALUES (%s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET updated_at = now()
                """,
                (doc_id, title, source_type),
            )
            cursor.execute(
                """
                INSERT INTO document_versions (id, document_id, file_hash, status, source_path)
                VALUES (%s, %s, %s, 'pending', %s)
                """,
                (version_id, doc_id, file_hash, source_path),
            )
            cursor.execute(
                """
                INSERT INTO index_jobs (id, document_id, version_id, status)
                VALUES (%s, %s, %s, 'pending')
                """,
                (job_id, doc_id, version_id),
            )
        return CreatedVersion(document_id=doc_id, version_id=version_id, job_id=job_id)

    def list_documents(self) -> list[dict]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT d.id, d.title, d.source_type, d.current_version_id, d.created_at, d.updated_at,
                       COUNT(c.id) FILTER (WHERE c.active) AS active_chunk_count
                FROM documents d
                LEFT JOIN document_chunks c ON c.document_id = d.id
                GROUP BY d.id
                ORDER BY d.updated_at DESC
                """
            )
            return list(cursor.fetchall())
```

- [ ] **Step 4: Run Python compile check**

Run:

```bash
python -m compileall services/api packages/core
```

Expected: compile succeeds with no syntax errors.

- [ ] **Step 5: Commit**

```bash
git add services/api/opensql_autorag_api/settings.py services/api/opensql_autorag_api/db.py services/api/opensql_autorag_api/repository.py
git commit -m "feat: add API persistence foundation"
```

## Task 8: REST API

**Files:**
- Create: `services/api/opensql_autorag_api/schemas.py`
- Create: `services/api/opensql_autorag_api/main.py`
- Create: `tests/api/test_schemas.py`

- [ ] **Step 1: Write API schema tests**

Write `tests/api/test_schemas.py`:

```python
from uuid import uuid4

from opensql_autorag_api.schemas import DocumentUploadResponse, SearchRequest


def test_upload_response_serializes_ids():
    response = DocumentUploadResponse(document_id=uuid4(), version_id=uuid4(), job_id=uuid4())
    payload = response.model_dump(mode="json")
    assert isinstance(payload["document_id"], str)
    assert isinstance(payload["version_id"], str)
    assert isinstance(payload["job_id"], str)


def test_search_request_defaults_top_k_to_five():
    request = SearchRequest(query="OpenSQL pgvector")
    assert request.top_k == 5
```

- [ ] **Step 2: Implement schemas**

Write `services/api/opensql_autorag_api/schemas.py`:

```python
from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class DocumentUploadResponse(BaseModel):
    document_id: UUID
    version_id: UUID
    job_id: UUID


class DocumentSummary(BaseModel):
    id: UUID
    title: str
    source_type: str
    current_version_id: UUID | None
    active_chunk_count: int


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)


class SearchResult(BaseModel):
    chunk_id: UUID
    document_id: UUID
    version_id: UUID
    text: str
    score: float
    heading_path: str
    page_start: int | None
    page_end: int | None
```

- [ ] **Step 3: Implement FastAPI app**

Write `services/api/opensql_autorag_api/main.py`:

```python
from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile

from opensql_autorag_api.db import get_connection
from opensql_autorag_api.repository import Repository
from opensql_autorag_api.schemas import DocumentSummary, DocumentUploadResponse, SearchRequest
from opensql_autorag_api.settings import settings

app = FastAPI(title="OpenSQL AutoRAG Sync")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/documents", response_model=list[DocumentSummary])
def list_documents() -> list[dict]:
    with get_connection() as connection:
        return Repository(connection).list_documents()


@app.post("/documents", response_model=DocumentUploadResponse)
async def upload_document(file: UploadFile = File(...)) -> DocumentUploadResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="filename is required")
    suffix = Path(file.filename).suffix.lower().lstrip(".")
    if suffix not in {"pdf", "docx", "md", "txt"}:
        raise HTTPException(status_code=400, detail=f"unsupported file type: {suffix}")

    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    content = await file.read()
    file_hash = hashlib.sha256(content).hexdigest()
    source_path = settings.storage_dir / f"{file_hash}.{suffix}"
    source_path.write_bytes(content)

    with get_connection() as connection:
        created = Repository(connection).create_document_version(
            title=file.filename,
            source_type=suffix,
            source_path=str(source_path),
            file_hash=file_hash,
        )

    return DocumentUploadResponse(
        document_id=created.document_id,
        version_id=created.version_id,
        job_id=created.job_id,
    )


@app.post("/search")
def search_documents(request: SearchRequest) -> dict:
    return {"query": request.query, "top_k": request.top_k, "results": []}
```

- [ ] **Step 4: Run schema tests**

Run:

```bash
pytest tests/api/test_schemas.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Run API import check**

Run:

```bash
python -c "from opensql_autorag_api.main import app; print(app.title)"
```

Expected:

```text
OpenSQL AutoRAG Sync
```

- [ ] **Step 6: Commit**

```bash
git add services/api/opensql_autorag_api/schemas.py services/api/opensql_autorag_api/main.py tests/api/test_schemas.py
git commit -m "feat: add document upload API"
```

## Task 9: Document Extractors and Worker Processor

**Files:**
- Create: `services/worker/opensql_autorag_worker/extractors.py`
- Create: `services/worker/opensql_autorag_worker/processor.py`
- Create: `services/worker/opensql_autorag_worker/main.py`
- Create: `tests/worker/test_extractors.py`

- [ ] **Step 1: Write extractor tests**

Write `tests/worker/test_extractors.py`:

```python
from pathlib import Path

from opensql_autorag_worker.extractors import extract_blocks


def test_extract_text_file_to_blocks(tmp_path: Path):
    path = tmp_path / "guide.txt"
    path.write_text("Intro\nOpenSQL stores vectors.", encoding="utf-8")

    blocks = extract_blocks(path)

    assert len(blocks) == 2
    assert blocks[0].text == "Intro"
    assert blocks[1].text == "OpenSQL stores vectors."
```

- [ ] **Step 2: Implement extractors**

Write `services/worker/opensql_autorag_worker/extractors.py`:

```python
from __future__ import annotations

from pathlib import Path

from docx import Document
import fitz

from opensql_autorag.domain import SourceLocation, TextBlock


def extract_blocks(path: Path) -> tuple[TextBlock, ...]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(path)
    if suffix == ".docx":
        return _extract_docx(path)
    if suffix in {".md", ".txt"}:
        return _extract_plain_text(path)
    raise ValueError(f"unsupported file type: {suffix}")


def _extract_plain_text(path: Path) -> tuple[TextBlock, ...]:
    blocks: list[TextBlock] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        text = line.strip()
        if text:
            blocks.append(TextBlock(text=text, location=SourceLocation(), block_index=index))
    return tuple(blocks)


def _extract_docx(path: Path) -> tuple[TextBlock, ...]:
    document = Document(path)
    blocks: list[TextBlock] = []
    heading_path: list[str] = []
    for index, paragraph in enumerate(document.paragraphs):
        text = paragraph.text.strip()
        if not text:
            continue
        if paragraph.style and paragraph.style.name.startswith("Heading"):
            heading_path = [text]
        blocks.append(
            TextBlock(
                text=text,
                location=SourceLocation(heading_path=tuple(heading_path)),
                block_index=index,
            )
        )
    return tuple(blocks)


def _extract_pdf(path: Path) -> tuple[TextBlock, ...]:
    blocks: list[TextBlock] = []
    with fitz.open(path) as document:
        for page_index, page in enumerate(document, start=1):
            for line_index, line in enumerate(page.get_text().splitlines()):
                text = line.strip()
                if text:
                    blocks.append(
                        TextBlock(
                            text=text,
                            location=SourceLocation(page_index, page_index),
                            block_index=len(blocks) + line_index,
                        )
                    )
    return tuple(blocks)
```

- [ ] **Step 3: Implement processor skeleton**

Write `services/worker/opensql_autorag_worker/processor.py`:

```python
from __future__ import annotations

from pathlib import Path

from opensql_autorag.chunking import SemanticChunker
from opensql_autorag.delta import DeltaPlanner
from opensql_autorag.embeddings import HashEmbeddingProvider
from opensql_autorag_worker.extractors import extract_blocks


class IndexProcessor:
    def __init__(self) -> None:
        self.chunker = SemanticChunker()
        self.delta_planner = DeltaPlanner()
        self.embedding_provider = HashEmbeddingProvider(dimension=384)

    def preview_file(self, document_id: str, path: Path) -> dict[str, int]:
        blocks = extract_blocks(path)
        chunks = self.chunker.chunk(document_id, blocks)
        plan = self.delta_planner.plan(previous=(), current=chunks)
        for item in plan.chunks:
            if item.decision == "embed":
                self.embedding_provider.embed(item.chunk.text)
        return {
            "blocks": len(blocks),
            "chunks": len(chunks),
            "embedded": plan.embedded_count,
            "reused": plan.reused_count,
            "retired": plan.retired_count,
        }
```

- [ ] **Step 4: Implement worker loop entry**

Write `services/worker/opensql_autorag_worker/main.py`:

```python
from __future__ import annotations

import time


def run_worker() -> None:
    while True:
        print("OpenSQL AutoRAG worker heartbeat", flush=True)
        time.sleep(10)


if __name__ == "__main__":
    run_worker()
```

- [ ] **Step 5: Run extractor tests**

Run:

```bash
pytest tests/worker/test_extractors.py -v
```

Expected: 1 passed.

- [ ] **Step 6: Commit**

```bash
git add services/worker/opensql_autorag_worker tests/worker/test_extractors.py
git commit -m "feat: add document extraction worker foundation"
```

## Task 10: Index Job Persistence and Delta Sync Worker

**Files:**
- Modify: `services/api/opensql_autorag_api/repository.py`
- Modify: `services/worker/opensql_autorag_worker/processor.py`
- Create: `tests/worker/test_processor.py`

- [ ] **Step 1: Write processor delta-sync test**

Write `tests/worker/test_processor.py`:

```python
from pathlib import Path

from opensql_autorag.domain import Chunk, SourceLocation
from opensql_autorag.hash_utils import content_hash, stable_key
from opensql_autorag_worker.processor import CountingEmbeddingProvider, IndexProcessor


def make_previous_chunk(index: int, text: str) -> Chunk:
    location = SourceLocation(heading_path=("Guide",))
    return Chunk(
        stable_key=stable_key("doc-1", location.heading_path, index, text),
        text=text,
        content_hash=content_hash(text),
        chunk_index=index,
        location=location,
        token_estimate=len(text.split()),
    )


def test_processor_embeds_only_changed_chunks(tmp_path: Path):
    path = tmp_path / "guide.txt"
    path.write_text("same content\nnew content", encoding="utf-8")
    provider = CountingEmbeddingProvider()
    processor = IndexProcessor(embedding_provider=provider)

    summary = processor.preview_file(
        document_id="doc-1",
        path=path,
        previous_chunks=(make_previous_chunk(0, "same content"),),
    )

    assert summary["reused"] == 1
    assert summary["embedded"] == 1
    assert provider.calls == ["new content"]
```

- [ ] **Step 2: Update processor to accept previous chunks and injectable provider**

Replace `services/worker/opensql_autorag_worker/processor.py` with:

```python
from __future__ import annotations

from pathlib import Path

from opensql_autorag.chunking import SemanticChunker
from opensql_autorag.delta import DeltaPlanner
from opensql_autorag.domain import Chunk, ChunkDecision
from opensql_autorag.embeddings import HashEmbeddingProvider
from opensql_autorag_worker.extractors import extract_blocks


class CountingEmbeddingProvider:
    dimension = 384

    def __init__(self) -> None:
        self.calls: list[str] = []

    def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        return [0.0] * self.dimension


class IndexProcessor:
    def __init__(self, embedding_provider: object | None = None) -> None:
        self.chunker = SemanticChunker()
        self.delta_planner = DeltaPlanner()
        self.embedding_provider = embedding_provider or HashEmbeddingProvider(dimension=384)

    def preview_file(
        self,
        document_id: str,
        path: Path,
        previous_chunks: tuple[Chunk, ...] = (),
    ) -> dict[str, int]:
        blocks = extract_blocks(path)
        chunks = self.chunker.chunk(document_id, blocks)
        plan = self.delta_planner.plan(previous=previous_chunks, current=chunks)
        for item in plan.chunks:
            if item.decision == ChunkDecision.EMBED:
                self.embedding_provider.embed(item.chunk.text)
        return {
            "blocks": len(blocks),
            "chunks": len(chunks),
            "embedded": plan.embedded_count,
            "reused": plan.reused_count,
            "retired": plan.retired_count,
        }
```

- [ ] **Step 3: Add repository methods for worker persistence**

Add these imports to `services/api/opensql_autorag_api/repository.py`:

```python
import hashlib

from opensql_autorag.domain import Chunk
```

Append these methods to `Repository`:

```python
    def claim_next_job(self) -> dict | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE index_jobs
                SET status = 'running', attempts = attempts + 1, updated_at = now()
                WHERE id = (
                    SELECT id
                    FROM index_jobs
                    WHERE status = 'pending'
                    ORDER BY created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                RETURNING id, document_id, version_id
                """
            )
            return cursor.fetchone()

    def get_version_source_path(self, version_id: UUID) -> str:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT source_path FROM document_versions WHERE id = %s",
                (version_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError(f"version not found: {version_id}")
            return str(row["source_path"])

    def mark_job_failed(self, job_id: UUID, message: str) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE index_jobs
                SET status = 'failed', error_message = %s, updated_at = now()
                WHERE id = %s
                """,
                (message, job_id),
            )
```

- [ ] **Step 4: Add active chunk loader**

Append this method to `Repository`:

```python
    def load_active_chunks(self, document_id: UUID) -> list[dict]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, stable_key, text, content_hash, chunk_index, heading_path,
                       page_start, page_end, token_estimate
                FROM document_chunks
                WHERE document_id = %s AND active = TRUE
                ORDER BY chunk_index
                """,
                (document_id,),
            )
            return list(cursor.fetchall())
```

- [ ] **Step 5: Add indexing completion method**

Append this method to `Repository`:

```python
    def insert_chunk_with_embedding(
        self,
        document_id: UUID,
        version_id: UUID,
        chunk: Chunk,
        embedding: list[float],
        embedding_model_id: int = 1,
    ) -> UUID:
        chunk_id = uuid4()
        heading_path = " / ".join(chunk.location.heading_path)
        vector_literal = "[" + ",".join(f"{value:.8f}" for value in embedding) + "]"
        vector_hash = hashlib.sha256(vector_literal.encode("utf-8")).hexdigest()
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO document_chunks (
                    id, document_id, version_id, stable_key, chunk_index, text,
                    content_hash, heading_path, page_start, page_end, token_estimate, active
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE)
                """,
                (
                    chunk_id,
                    document_id,
                    version_id,
                    chunk.stable_key,
                    chunk.chunk_index,
                    chunk.text,
                    chunk.content_hash,
                    heading_path,
                    chunk.location.page_start,
                    chunk.location.page_end,
                    chunk.token_estimate,
                ),
            )
            cursor.execute(
                """
                INSERT INTO chunk_embeddings (
                    chunk_id, embedding_model_id, embedding, vector_hash
                )
                VALUES (%s, %s, %s::vector, %s)
                """,
                (chunk_id, embedding_model_id, vector_literal, vector_hash),
            )
        return chunk_id
```

- [ ] **Step 6: Add indexing completion method**

Append this method to `Repository`:

```python
    def insert_chunk_reusing_embedding(
        self,
        document_id: UUID,
        version_id: UUID,
        chunk: Chunk,
        embedding_model_id: int = 1,
    ) -> UUID:
        chunk_id = uuid4()
        heading_path = " / ".join(chunk.location.heading_path)
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO document_chunks (
                    id, document_id, version_id, stable_key, chunk_index, text,
                    content_hash, heading_path, page_start, page_end, token_estimate, active
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE)
                """,
                (
                    chunk_id,
                    document_id,
                    version_id,
                    chunk.stable_key,
                    chunk.chunk_index,
                    chunk.text,
                    chunk.content_hash,
                    heading_path,
                    chunk.location.page_start,
                    chunk.location.page_end,
                    chunk.token_estimate,
                ),
            )
            cursor.execute(
                """
                INSERT INTO chunk_embeddings (
                    chunk_id, embedding_model_id, embedding, vector_hash
                )
                SELECT %s, e.embedding_model_id, e.embedding, e.vector_hash
                FROM chunk_embeddings e
                JOIN document_chunks c ON c.id = e.chunk_id
                WHERE c.document_id = %s
                  AND c.content_hash = %s
                  AND e.embedding_model_id = %s
                ORDER BY c.created_at DESC
                LIMIT 1
                """,
                (chunk_id, document_id, chunk.content_hash, embedding_model_id),
            )
        return chunk_id
```

- [ ] **Step 7: Add indexing completion method**

Append this method to `Repository`:

```python
    def complete_indexing(
        self,
        job_id: UUID,
        document_id: UUID,
        version_id: UUID,
        reused_count: int,
        embedded_count: int,
        retired_count: int,
        elapsed_ms: int,
    ) -> None:
        sync_run_id = uuid4()
        with self.connection.cursor() as cursor:
            cursor.execute(
                "UPDATE document_chunks SET active = (version_id = %s) WHERE document_id = %s",
                (version_id, document_id),
            )
            cursor.execute(
                """
                UPDATE document_versions
                SET status = 'indexed', extracted_text_hash = COALESCE(extracted_text_hash, file_hash)
                WHERE id = %s
                """,
                (version_id,),
            )
            cursor.execute(
                """
                UPDATE documents
                SET current_version_id = %s, updated_at = now()
                WHERE id = %s
                """,
                (version_id, document_id),
            )
            cursor.execute(
                """
                UPDATE index_jobs
                SET status = 'succeeded', updated_at = now()
                WHERE id = %s
                """,
                (job_id,),
            )
            cursor.execute(
                """
                INSERT INTO sync_runs (
                    id, document_id, version_id, reused_count, embedded_count,
                    retired_count, elapsed_ms
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    sync_run_id,
                    document_id,
                    version_id,
                    reused_count,
                    embedded_count,
                    retired_count,
                    elapsed_ms,
                ),
            )
```

- [ ] **Step 8: Replace worker loop with job processing**

Write `services/worker/opensql_autorag_worker/main.py`:

```python
from __future__ import annotations

import time
from pathlib import Path
from time import perf_counter
from uuid import UUID

from opensql_autorag.delta import DeltaPlanner
from opensql_autorag.domain import Chunk, ChunkDecision, SourceLocation
from opensql_autorag.embeddings import HashEmbeddingProvider
from opensql_autorag.hash_utils import content_hash
from opensql_autorag_api.db import get_connection
from opensql_autorag_api.repository import Repository
from opensql_autorag_worker.extractors import extract_blocks
from opensql_autorag_worker.processor import IndexProcessor


def _row_to_chunk(row: dict) -> Chunk:
    heading_path = tuple(part.strip() for part in str(row["heading_path"]).split("/") if part.strip())
    return Chunk(
        stable_key=str(row["stable_key"]),
        text=str(row["text"]),
        content_hash=str(row["content_hash"]),
        chunk_index=int(row["chunk_index"]),
        location=SourceLocation(
            page_start=row["page_start"],
            page_end=row["page_end"],
            heading_path=heading_path,
        ),
        token_estimate=int(row["token_estimate"]),
    )


def process_next_job() -> bool:
    started = perf_counter()
    provider = HashEmbeddingProvider(dimension=384)
    processor = IndexProcessor(embedding_provider=provider)
    planner = DeltaPlanner()
    with get_connection() as connection:
        repo = Repository(connection)
        job = repo.claim_next_job()
        if job is None:
            return False
        job_id = UUID(str(job["id"]))
        document_id = UUID(str(job["document_id"]))
        version_id = UUID(str(job["version_id"]))
        try:
            source_path = Path(repo.get_version_source_path(version_id))
            blocks = extract_blocks(source_path)
            current_chunks = processor.chunker.chunk(str(document_id), blocks)
            previous_chunks = tuple(_row_to_chunk(row) for row in repo.load_active_chunks(document_id))
            plan = planner.plan(previous=previous_chunks, current=current_chunks)
            for item in plan.chunks:
                if item.decision == ChunkDecision.EMBED:
                    embedding = provider.embed(item.chunk.text)
                    repo.insert_chunk_with_embedding(document_id, version_id, item.chunk, embedding)
                elif item.decision == ChunkDecision.REUSE:
                    repo.insert_chunk_reusing_embedding(document_id, version_id, item.chunk)
            elapsed_ms = int((perf_counter() - started) * 1000)
            repo.complete_indexing(
                job_id=job_id,
                document_id=document_id,
                version_id=version_id,
                reused_count=plan.reused_count,
                embedded_count=plan.embedded_count,
                retired_count=plan.retired_count,
                elapsed_ms=elapsed_ms,
            )
            return True
        except Exception as exc:
            repo.mark_job_failed(job_id, str(exc))
            return True


def run_worker() -> None:
    while True:
        processed = process_next_job()
        if not processed:
            time.sleep(2)


if __name__ == "__main__":
    run_worker()
```

- [ ] **Step 9: Run processor test**

Run:

```bash
pytest tests/worker/test_processor.py -v
```

Expected: 1 passed.

- [ ] **Step 10: Commit**

```bash
git add services/api/opensql_autorag_api/repository.py services/worker/opensql_autorag_worker/processor.py services/worker/opensql_autorag_worker/main.py tests/worker/test_processor.py
git commit -m "feat: add delta-sync worker persistence hooks"
```

## Task 11: Retrieval Service

**Files:**
- Create: `packages/core/opensql_autorag/retrieval.py`
- Modify: `services/api/opensql_autorag_api/main.py`

- [ ] **Step 1: Implement retrieval models**

Write `packages/core/opensql_autorag/retrieval.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class RetrievalResult:
    chunk_id: UUID
    document_id: UUID
    version_id: UUID
    text: str
    score: float
    heading_path: str
    page_start: int | None
    page_end: int | None


@dataclass(frozen=True)
class RetrievalQuery:
    query: str
    top_k: int = 5
```

- [ ] **Step 2: Add repository search method**

Append this method to `Repository` in `services/api/opensql_autorag_api/repository.py`:

```python
    def search_chunks(self, query_embedding: list[float], top_k: int) -> list[dict]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT c.id AS chunk_id, c.document_id, c.version_id, c.text,
                       c.heading_path, c.page_start, c.page_end,
                       1 - (e.embedding <=> %s::vector) AS score
                FROM chunk_embeddings e
                JOIN document_chunks c ON c.id = e.chunk_id
                WHERE c.active = TRUE
                ORDER BY e.embedding <=> %s::vector
                LIMIT %s
                """,
                (query_embedding, query_embedding, top_k),
            )
            return list(cursor.fetchall())
```

- [ ] **Step 3: Wire API search to retrieval**

Replace the `/search` route in `services/api/opensql_autorag_api/main.py` with:

```python
@app.post("/search")
def search_documents(request: SearchRequest) -> dict:
    from opensql_autorag.embeddings import HashEmbeddingProvider

    provider = HashEmbeddingProvider(dimension=settings.embedding_dimension)
    query_embedding = provider.embed(request.query)
    with get_connection() as connection:
        rows = Repository(connection).search_chunks(query_embedding, request.top_k)
    return {"query": request.query, "top_k": request.top_k, "results": rows}
```

- [ ] **Step 4: Run compile check**

Run:

```bash
python -m compileall packages services
```

Expected: compile succeeds with no syntax errors.

- [ ] **Step 5: Commit**

```bash
git add packages/core/opensql_autorag/retrieval.py services/api/opensql_autorag_api/main.py services/api/opensql_autorag_api/repository.py
git commit -m "feat: add pgvector retrieval path"
```

## Task 12: MCP Server

**Files:**
- Create: `services/mcp/opensql_autorag_mcp/server.py`
- Create: `tests/mcp/test_contract.py`

- [ ] **Step 1: Write MCP contract test**

Write `tests/mcp/test_contract.py`:

```python
from opensql_autorag_mcp.server import TOOL_NAMES


def test_mcp_tool_names_are_stable():
    assert TOOL_NAMES == {
        "search_documents",
        "get_chunk_context",
        "list_documents",
        "get_sync_status",
    }
```

- [ ] **Step 2: Implement MCP server**

Write `services/mcp/opensql_autorag_mcp/server.py`:

```python
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

TOOL_NAMES = {
    "search_documents",
    "get_chunk_context",
    "list_documents",
    "get_sync_status",
}

mcp = FastMCP("OpenSQL AutoRAG Sync")


@mcp.tool()
def search_documents(query: str, top_k: int = 5) -> dict:
    return {"query": query, "top_k": top_k, "results": []}


@mcp.tool()
def get_chunk_context(chunk_id: str) -> dict:
    return {"chunk_id": chunk_id, "context": []}


@mcp.tool()
def list_documents() -> dict:
    return {"documents": []}


@mcp.tool()
def get_sync_status(document_id: str) -> dict:
    return {"document_id": document_id, "status": "unknown"}


if __name__ == "__main__":
    mcp.run()
```

- [ ] **Step 3: Run MCP contract test**

Run:

```bash
pytest tests/mcp/test_contract.py -v
```

Expected: 1 passed.

- [ ] **Step 4: Commit**

```bash
git add services/mcp/opensql_autorag_mcp/server.py tests/mcp/test_contract.py
git commit -m "feat: add MCP search server contract"
```

## Task 13: Web Console

**Files:**
- Create: `apps/web/src/main.tsx`
- Create: `apps/web/src/App.tsx`
- Create: `apps/web/src/styles.css`

- [ ] **Step 1: Create React entry**

Write `apps/web/src/main.tsx`:

```tsx
import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
```

- [ ] **Step 2: Create product console**

Write `apps/web/src/App.tsx`:

```tsx
import { Database, FileUp, RefreshCw, Search } from "lucide-react";
import { useState } from "react";

type DocumentSummary = {
  id: string;
  title: string;
  source_type: string;
  current_version_id: string | null;
  active_chunk_count: number;
};

export function App() {
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [query, setQuery] = useState("OpenSQL pgvector");
  const [results, setResults] = useState<any[]>([]);

  async function refreshDocuments() {
    const response = await fetch("/api/documents");
    setDocuments(await response.json());
  }

  async function search() {
    const response = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, top_k: 5 })
    });
    const payload = await response.json();
    setResults(payload.results ?? []);
  }

  return (
    <main className="shell">
      <aside className="sidebar">
        <div className="brand"><Database size={22} /> OpenSQL AutoRAG</div>
        <button onClick={refreshDocuments}><RefreshCw size={16} /> Refresh</button>
      </aside>
      <section className="workspace">
        <header>
          <h1>Document AI Search on OpenSQL</h1>
          <p>Upload, version, delta-sync, and retrieve grounded chunks from pgvector.</p>
        </header>
        <div className="grid">
          <section className="panel">
            <h2><FileUp size={18} /> Documents</h2>
            {documents.length === 0 ? <p className="muted">No indexed documents loaded.</p> : null}
            {documents.map((doc) => (
              <article className="row" key={doc.id}>
                <strong>{doc.title}</strong>
                <span>{doc.source_type} · {doc.active_chunk_count} chunks</span>
              </article>
            ))}
          </section>
          <section className="panel">
            <h2><Search size={18} /> Search Playground</h2>
            <div className="searchbar">
              <input value={query} onChange={(event) => setQuery(event.target.value)} />
              <button onClick={search}>Search</button>
            </div>
            {results.map((result, index) => (
              <article className="result" key={`${result.chunk_id}-${index}`}>
                <strong>{Number(result.score).toFixed(3)}</strong>
                <p>{result.text}</p>
                <span>{result.heading_path}</span>
              </article>
            ))}
          </section>
        </div>
      </section>
    </main>
  );
}
```

- [ ] **Step 3: Create console styles**

Write `apps/web/src/styles.css`:

```css
:root {
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  color: #172033;
  background: #f5f7fb;
}

body {
  margin: 0;
}

button {
  border: 1px solid #cbd5e1;
  background: #ffffff;
  color: #172033;
  border-radius: 8px;
  padding: 10px 12px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
}

.shell {
  min-height: 100vh;
  display: grid;
  grid-template-columns: 260px 1fr;
}

.sidebar {
  background: #102033;
  color: #ffffff;
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  font-weight: 700;
}

.workspace {
  padding: 32px;
}

header h1 {
  margin: 0 0 8px;
  font-size: 30px;
}

header p,
.muted {
  color: #64748b;
}

.grid {
  display: grid;
  grid-template-columns: minmax(280px, 0.9fr) minmax(360px, 1.4fr);
  gap: 20px;
  margin-top: 24px;
}

.panel {
  background: #ffffff;
  border: 1px solid #dbe3ef;
  border-radius: 8px;
  padding: 20px;
}

.panel h2 {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 0;
  font-size: 18px;
}

.row,
.result {
  border-top: 1px solid #e2e8f0;
  padding: 14px 0;
  display: grid;
  gap: 6px;
}

.row span,
.result span {
  color: #64748b;
  font-size: 14px;
}

.searchbar {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 10px;
}

.searchbar input {
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  padding: 10px 12px;
}

@media (max-width: 840px) {
  .shell {
    grid-template-columns: 1fr;
  }

  .grid {
    grid-template-columns: 1fr;
  }
}
```

- [ ] **Step 4: Run web type check**

Run:

```bash
npm --prefix apps/web run test
```

Expected: TypeScript completes without errors.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/main.tsx apps/web/src/App.tsx apps/web/src/styles.css
git commit -m "feat: add OpenSQL AutoRAG web console"
```

## Task 14: Local Infrastructure and Demo Documentation

**Files:**
- Create: `infra/docker-compose.yml`
- Create: `docs/demo.md`
- Modify: `.gitignore`

- [ ] **Step 1: Create Docker Compose stack**

Write `infra/docker-compose.yml`:

```yaml
services:
  db:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: autorag
      POSTGRES_USER: autorag
      POSTGRES_PASSWORD: autorag
    ports:
      - "5432:5432"
    volumes:
      - ./db/init.sql:/docker-entrypoint-initdb.d/001-init.sql:ro
      - opensql_autorag_data:/var/lib/postgresql/data

volumes:
  opensql_autorag_data:
```

- [ ] **Step 2: Ensure generated runtime data stays untracked**

Confirm `.gitignore` contains:

```text
data/
*.log
```

- [ ] **Step 3: Create demo script**

Write `docs/demo.md`:

```markdown
# Contest Demo Script

1. Start the OpenSQL-compatible local stack with `docker compose -f infra/docker-compose.yml up -d`.
2. Start the API with `uvicorn opensql_autorag_api.main:app --reload`.
3. Start the web console with `npm run dev:web`.
4. Upload a technical document.
5. Show document version creation and indexing status.
6. Search for an OpenSQL or pgvector concept.
7. Upload a revised copy of the same document.
8. Show the sync run counts: reused chunks, embedded chunks, retired chunks.
9. Search again and show the latest version metadata in the result.
10. Start the MCP server with `python -m opensql_autorag_mcp.server`.
11. Show that MCP tools expose the same document search capability.
```

- [ ] **Step 4: Commit**

```bash
git add infra/docker-compose.yml docs/demo.md .gitignore
git commit -m "chore: add local demo infrastructure"
```

## Task 15: Verification Pass

**Files:**
- Modify only files needed to fix failures found by these commands.

- [ ] **Step 1: Run Python unit tests**

Run:

```bash
pytest -v
```

Expected: all Python tests pass.

- [ ] **Step 2: Run Python lint**

Run:

```bash
ruff check packages services tests
```

Expected: no lint violations.

- [ ] **Step 3: Run Python compile check**

Run:

```bash
python -m compileall packages services
```

Expected: compile succeeds with no syntax errors.

- [ ] **Step 4: Run web type check**

Run:

```bash
npm --prefix apps/web run test
```

Expected: TypeScript completes without errors.

- [ ] **Step 5: Commit verification fixes**

If any files changed during verification:

```bash
git add <changed-files>
git commit -m "fix: resolve verification issues"
```

If no files changed:

```bash
git status --short
```

Expected: no unstaged product source changes.
