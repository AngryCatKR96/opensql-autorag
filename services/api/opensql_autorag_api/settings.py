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

    # How a search combines the two retrieval arms. `hybrid` fuses vector and
    # keyword results, `vector` and `keyword` use one arm alone. A request may
    # override it per query.
    search_mode: str = "hybrid"

    # The text search configuration used for the keyword arm. `english` stems
    # English and leaves other scripts as whole tokens, which is what a mixed
    # English and Korean wiki needs; `simple` disables stemming entirely.
    text_search_config: str = "english"

    # How many candidates each arm contributes before fusion. Fusion can only
    # rank what it is given, so this is deliberately wider than top_k.
    search_candidate_multiplier: int = 4
    search_candidate_minimum: int = 20

    # Reciprocal rank fusion's damping constant. 60 is the value from the paper
    # the method comes from and behaves well without tuning.
    rrf_k: int = 60

    # pgvector's HNSW index returns a fixed candidate pool, and this platform
    # filters those candidates by what the caller may read. Enough of them can be
    # filtered out that a query returns fewer than top_k while matching documents
    # remain unvisited. Iterative scan resumes the search instead of stopping;
    # `strict_order` keeps results in true distance order. Set to an empty string
    # to leave the server's own setting alone.
    hnsw_iterative_scan: str = "strict_order"
    # 0 leaves pgvector's own default in place.
    hnsw_max_scan_tuples: int = 0

    # Search honours the permissions of the wiki a document was synced from, by
    # asking Outline what the caller can read. The same instance the connector
    # syncs from, so these share AUTORAG_OUTLINE_* with it.
    outline_base_url: str = "https://app.getoutline.com"
    outline_timeout_seconds: float = 10.0
    outline_page_size: int = 50

    # The Outline token the MCP server presents to the API on every call. That
    # server speaks stdio to the one developer who launched it, so their token
    # comes from the environment rather than from a request. Without one, only
    # documents uploaded straight into AutoRAG are searched -- wiki content is
    # not served to an unidentified caller.
    outline_user_token: str = ""

    # Where the MCP server reaches this API. It holds no database credentials of
    # its own and embeds nothing: it asks the API, which resolves the token above
    # into a scope, applies the filter in SQL, and embeds the query. A developer
    # running it on their own machine needs this URL and their token, nothing
    # else -- no database reachable from their laptop, no model downloaded onto
    # it, and no way to read past the filter by querying around it.
    api_base_url: str = "http://127.0.0.1:8000"
    # Generous because a first search can wait on the API loading its model.
    api_timeout_seconds: float = 30.0

    # How long a resolved set of readable collections is reused. Longer means
    # fewer calls to Outline; it also bounds how long a revoked membership keeps
    # returning results.
    access_cache_seconds: int = 60

    # Signing in with Outline instead of pasting a personal API token. Registered
    # in Outline under Settings -> Applications; the redirect URI there must match
    # the one below exactly.
    outline_oauth_client_id: str = ""
    outline_oauth_client_secret: str = ""
    outline_oauth_scope: str = "read"

    # Where this API is reachable by a browser. Only used to build the redirect
    # URI, which Outline sends the caller back to.
    public_base_url: str = "http://127.0.0.1:8000"

    # Encrypts the Outline tokens held in oauth_sessions, and is what makes a
    # session readable. Rotating it signs everybody out.
    session_secret: str = ""
    session_ttl_seconds: int = 60 * 60 * 24 * 7
    session_cookie_secure: bool = False

    oauth_login_ttl_seconds: int = 600
    oauth_discovery_cache_seconds: int = 600

    @property
    def oauth_redirect_uri(self) -> str:
        return f"{self.public_base_url.rstrip('/')}/auth/outline/callback"


settings = Settings()
