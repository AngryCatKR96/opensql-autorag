# OpenSQL AutoRAG Sync

[Tmax OpenSQL](https://www.tmaxtibero.com/)과 pgvector 위에서 동작하는 문서 검색
플랫폼. 계속 바뀌는 문서를 대상으로 한다.

문서를 넣으면 — 직접 업로드하거나 Outline 위키를 연결하면 — 추출, 분할, 임베딩,
색인까지 자동으로 처리한다. 페이지의 한 섹션만 고치면 그 섹션만 다시 임베딩한다.
검색은 웹 콘솔, REST API, 그리고 코딩 에이전트에 붙이는 [MCP 서버](docs/mcp.md)
세 경로로 할 수 있다.

## pgvector 데모와 무엇이 다른가

**델타 동기화.** 청크는 문서의 섹션이고, 바뀌지 않은 청크는 기존 벡터를 그대로
유지한다. 다섯 섹션짜리 런북에서 한 섹션을 고치면 청크 하나만 다시 임베딩하고 네
개는 재사용한다. 색인할 수 있는 위키와 계속 색인해둘 수 있는 위키의 차이가 여기서
갈린다.

**권한은 원본에서 온다.** 위키에는 권한이 있는데 그것을 벡터 데이터베이스로 복사하면서
대개 버린다. 여기서는 호출자 본인의 Outline 접근 권한을 매 요청마다 해석해서 SQL 안에서
거르기 때문에, 범위 밖 문서는 결과 슬롯조차 차지하지 못한다. 로그인은 Outline OAuth로
하며 위키 자격증명을 이 애플리케이션에 입력하는 일은 없다.

**검색에 두 개의 팔이 있다.** 벡터 검색은 문단이 무엇에 관한 것인지에 답하고, 전문
검색은 그 단어를 실제로 포함하는지에 답한다. `ERR_HNSW_2481` 같은 식별자가 그 차이다 —
임베딩은 이것을 비슷하게 생긴 무언가로 뭉갠다. 두 팔을 모두 돌려 상호 순위 융합(RRF)으로
합치고, 각 결과에 어느 팔이 찾았는지 표시한다.

**문서가 밖으로 나가지 않는다.** 임베딩은 `intfloat/multilingual-e5-small`로 프로세스
안에서 직접 계산한다. 문서 본문도 검색 질의도 외부 서비스로 보내지 않으며, 이 플랫폼
자신의 API보다 멀리 가지 않는다.

## 아키텍처

```mermaid
flowchart LR
    subgraph dev["개발자 노트북 (사람마다)"]
        agent["코딩 에이전트<br/>Codex · Claude Code"]
        mcp["MCP 서버"]
        agent -- stdio --> mcp
    end

    browser["웹 콘솔"]

    subgraph srv["사내 서버"]
        api["REST API"]
        worker["워커"]
        connector["커넥터"]
        db[("OpenSQL<br/>PostgreSQL 17.8 + pgvector 0.8.1")]
    end

    outline[("Outline 위키")]

    browser --> api
    mcp -->|HTTP| api
    api --> db
    worker --> db
    connector --> db
    outline -->|웹훅| connector
    connector -->|백필| outline
    api -.->|권한 조회| outline
```

| 구성요소 | 역할 |
|---|---|
| **REST API** (`services/api`) | 검색, 업로드, 세션, Outline 권한 해석. 질의 임베딩도 여기서 한다 |
| **워커** (`services/worker`) | 추출 → 분할 → 임베딩 → 색인. 잡 큐를 돌며 처리한다 |
| **커넥터** (`services/connector`) | Outline 웹훅 수신, 백필, 사전 점검 |
| **MCP 서버** (`services/mcp`) | 에이전트용 도구 4개. API의 클라이언트라 DB 자격증명을 갖지 않는다 |
| **웹 콘솔** (`apps/web`) | React 콘솔 |
| **코어** (`packages/core`) | 도메인 모델, 분할, 델타 계획, 임베딩 프로바이더 |

핵심은 **모든 상태가 OpenSQL 한 곳에 있다**는 것이다. 문서, 버전, 출처, 청크, 벡터,
세션, 잡, 동기화 기록까지 별도 저장소 없이 전부 하나의 데이터베이스에 들어간다.

### 인제스트와 델타 동기화

```mermaid
flowchart TD
    A["문서 새 버전"] --> B["추출<br/>pypdf · python-docx · 텍스트"]
    B --> C["제목 기준 섹션 분할<br/>얇은 섹션은 이웃에 접고 긴 섹션은 나눈다"]
    C --> D{"stable_key로<br/>이전 버전 청크와 짝짓기"}
    D -->|content_hash 같음| E["기존 벡터 재사용"]
    D -->|다르거나 없음| F["그 청크만 임베딩"]
    E --> G["색인 · sync_runs에 기록"]
    F --> G
```

`sync_runs`에 남는 재사용/임베딩/회수/실패 건수가 델타 동기화가 실제로 동작한다는 증거라
API와 콘솔 양쪽에 노출한다. 재사용이 계속 0이면 `stable_key` 생성 규칙이 버전마다 달라진
것이다.

### 검색

질의가 들어오면 호출자의 신원을 먼저 해석한다. 브라우저는 세션 쿠키로, 기계 호출자는
`X-Outline-Token` 헤더로 신원을 제시하며, 둘 다 있으면 헤더가 이긴다. 해석된 컬렉션
허용 목록은 벡터 조회와 전문 검색 **양쪽 SQL 안에** 들어간다. 애플리케이션에서 나중에
거르면 상위 k개를 이미 범위 밖 문서가 차지한 뒤이기 때문이다.

두 팔의 결과는 상호 순위 융합으로 합친다. 점수가 아니라 순위를 쓰므로 스케일이 다른 두
점수를 정규화할 필요가 없다.

## 빠른 시작

```bash
# 1. 데이터베이스
docker compose -f infra/docker-compose.yml up -d

# 2. API (8000)
PYTHONPATH=packages/core:services/api \
  .venv/bin/python -m uvicorn opensql_autorag_api.main:app

# 3. 워커
PYTHONPATH=packages/core:services/api:services/worker \
  .venv/bin/python -m opensql_autorag_worker.main

# 4. 웹 콘솔 (5173)
npm run dev:web
```

5432 포트가 이미 쓰이고 있으면 남의 PostgreSQL을 내리지 말고 이쪽을 옮긴다.

```bash
AUTORAG_DB_PORT=5442 docker compose -f infra/docker-compose.yml up -d
export AUTORAG_DATABASE_URL=postgresql://autorag:autorag@127.0.0.1:5442/autorag
```

기본 임베딩 프로바이더는 `hash`로, 모델을 내려받지 않고 결정적인 벡터를 만들지만 의미는
없다. 실제 검색 품질을 보려면 바꾼다.

```bash
export AUTORAG_EMBEDDING_PROVIDER=sentence-transformers
```

### Outline 위키 연결

```bash
export AUTORAG_OUTLINE_BASE_URL=https://wiki.example.com
export AUTORAG_OUTLINE_API_KEY=<Settings → API Keys>

# 웹훅 수신기 (8200)
PYTHONPATH=packages/core:services/api:services/connector \
  .venv/bin/python -m uvicorn opensql_autorag_connector.app:app --port 8200

# 최초 적재
PYTHONPATH=packages/core:services/api:services/connector \
  .venv/bin/python -m opensql_autorag_connector.backfill --collection <collection-id>
```

개발용 일회용 Outline 인스턴스는 `--profile outline`로 함께 띄울 수 있다. 자세한 내용은
[docs/outline.md](docs/outline.md).

## 에이전트에 붙이기 (MCP)

MCP 서버는 **개발자 본인 노트북에서 본인 Outline 계정으로** 돌린다. API에 물어보는
구조라 데이터베이스 자격증명도 임베딩 모델도 필요 없고, 검색 범위는 그 사람이 위키에서
볼 수 있는 것과 정확히 같다.

```bash
export AUTORAG_API_BASE_URL=http://autorag.internal:8000
export AUTORAG_OUTLINE_USER_TOKEN=ol_api_...    # 본인 개인 토큰
```

Claude Code에 등록하는 예:

```bash
claude mcp add autorag \
  -e PYTHONPATH=services/api:services/mcp \
  -e AUTORAG_API_BASE_URL=http://autorag.internal:8000 \
  -e AUTORAG_OUTLINE_USER_TOKEN=ol_api_... \
  -- .venv/bin/python -m opensql_autorag_mcp.server
```

| 도구 | 무엇에 답하는가 |
|---|---|
| `search_documents` | 질문에 답하는 구절. `mode`는 `hybrid`(기본) · `vector` · `keyword` |
| `get_chunk_context` | 검색 결과 앞뒤 섹션 |
| `list_documents` | 읽을 수 있는 색인 문서 전체 |
| `get_sync_status` | 해당 문서의 마지막 색인 실행 결과 |

Codex 설정과 문제 해결은 [docs/mcp.md](docs/mcp.md).

## 주요 설정

모두 `AUTORAG_` 접두사를 쓰며 `.env` 파일도 읽는다.

| 변수 | 기본값 | 설명 |
|---|---|---|
| `DATABASE_URL` | `...@127.0.0.1:5432/autorag` | 모든 상태가 들어가는 데이터베이스 |
| `EMBEDDING_PROVIDER` | `hash` | `sentence-transformers`로 바꿔야 실제 의미 검색 |
| `SEARCH_MODE` | `hybrid` | `vector` · `keyword`로 한 팔만 쓸 수 있다 |
| `TEXT_SEARCH_CONFIG` | `english` | 영어는 어간 처리, 그 외 문자는 통째 토큰. 한영 혼용 위키에 맞는 설정 |
| `RRF_K` | `60` | 융합 감쇠 상수. 논문값 |
| `HNSW_ITERATIVE_SCAN` | `strict_order` | 권한 필터로 후보가 고갈될 때 검색을 재개한다 |
| `OUTLINE_BASE_URL` | `https://app.getoutline.com` | 커넥터와 권한 조회가 공유 |
| `ACCESS_CACHE_SECONDS` | `60` | 회수된 멤버십이 계속 통하는 시간의 상한 |
| `API_BASE_URL` | `http://127.0.0.1:8000` | MCP 서버가 API를 찾는 주소 |

## 저장소 구조

| 경로 | 내용 |
|---|---|
| `packages/core` | 도메인 모델, 분할, 델타 계획, 임베딩 프로바이더 |
| `services/api` | REST API, 검색, 세션, Outline 권한 해석 |
| `services/worker` | 추출·분할·임베딩·색인 잡 루프 |
| `services/connector` | Outline 웹훅 수신, 백필, 사전 점검 |
| `services/mcp` | MCP 서버 |
| `apps/web` | React 콘솔 |
| `infra` | compose 스택, 스키마, OpenSQL 이미지, Outline 테스트 인스턴스 |

## 문서

- [docs/demo.md](docs/demo.md) — 기동, 검색 모드, 분할, 데모 대본
- [docs/mcp.md](docs/mcp.md) — Codex·Claude Code에 붙이기
- [docs/outline.md](docs/outline.md) — 위키 동기화, 권한, 로그인, 웹훅
- [docs/opensql.md](docs/opensql.md) — 라이선스 빌드, 벡터 인덱스 설정, 고가용성
- [docs/superpowers/specs](docs/superpowers/specs) — 아키텍처 설계

## 테스트

```bash
.venv/bin/python -m pytest
```

SQL로 표현된 것은 가짜가 아니라 실제 데이터베이스에 대고 테스트한다. 권한 필터가
특히 그렇다 — 파이썬이 볼 수 없는 방식으로 틀린 필터가 잡아야 할 실패다. 데이터베이스에
닿을 수 없으면 실패가 아니라 건너뛴다.

## 라이선스

MIT, [LICENSE](LICENSE) 참고.

의존성도 여기에 맞춰 관대한 것으로 골랐다. PDF 추출은 더 뛰어난 PyMuPDF 대신 pypdf(BSD)를
쓰는데, PyMuPDF는 AGPL이라 링크하면 이 저작물 전체가 AGPL이 되기 때문이다. psycopg는
LGPL이며 이를 import하는 코드에는 조건을 걸지 않는다.

Tmax OpenSQL 위에서 돌리려면 배포판 tarball과 Tmax의 라이선스가 필요하다. 둘 다 이
저장소에 없으며 `infra/opensql`은 제공받은 아티팩트로 이미지를 빌드한다.
[docs/opensql.md](docs/opensql.md) 참고.
